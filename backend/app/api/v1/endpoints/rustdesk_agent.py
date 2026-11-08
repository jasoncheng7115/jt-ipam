"""RustDesk 專用代理的協定（X-Agent-Key 認證，不是 JWT）。

代理裝在 RustDesk 主機上（agent/jt_ipam_rustdesk_agent.py），一把金鑰對應一台 RustDesk 伺服器：
- `poll`：每 AGENT_POLL_SECONDS 秒一次，回報代理自己的狀態，拿設定、「立即同步」與「測試」請求；
  1.1.0 起順便帶 hbbr／hbbs 日誌裡的 Key 檢查結果（哪個 IP 因為 Key 被拒、哪個 IP 通過中繼）
- `report`：照回報間隔（或立即同步）讀 hbbs 資料庫與線上狀態的結果
- `events`：客戶端回報（心跳、系統資訊、連線／檔案稽核、告警），代理已在本機比對過 uuid
- `test-result`：「測試」的逐項結果
- `delete-result`：「刪除舊註冊」的結果（1.2.0 起；輪詢回應帶 `peer_deletes`，只有網頁上允許時才有）

下載 `agent.py`／`installer.sh` 是純程式碼、沒有密鑰，公開可取（同掃描代理、憑證代理）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import append_audit
from app.core.db import get_session
from app.models.rustdesk import RustDeskServer
from app.schemas.rustdesk import (
    RustDeskAgentPollIn,
    RustDeskAgentPollOut,
    RustDeskDeleteResultIn,
    RustDeskEvent,
    RustDeskEventsIn,
    RustDeskReport,
    RustDeskTestResultIn,
)
from app.services import agent_trust
from app.services import rustdesk as rustdesk_svc

router = APIRouter(prefix="/rustdesk/agent", tags=["rustdesk-agent"])


async def _server_from_key(session: AsyncSession, key: str | None) -> RustDeskServer:
    if not key:
        raise HTTPException(401, detail="missing agent key")
    srv = (await session.execute(select(RustDeskServer).where(
        RustDeskServer.agent_key_hash == rustdesk_svc.agent_key_hash(key)))).scalar_one_or_none()
    if srv is None:
        raise HTTPException(401, detail="invalid agent key")
    from app.core.rate_limit import limit_agent
    await limit_agent("rustdesk", srv.id)
    return srv


def _source_ip(request: Request) -> str | None:
    # 走 nginx 反代要取 X-Forwarded-For 的第一個（同掃描代理）
    xff = request.headers.get("x-forwarded-for")
    return xff.split(",")[0].strip()[:64] if xff else (request.client.host if request.client else None)


def _touch(srv: RustDeskServer, request: Request, version: str | None) -> None:
    srv.agent_last_seen_at = datetime.now(UTC)
    srv.agent_source_ip = _source_ip(request)
    if version:
        srv.agent_version = version[:32]


def _same_server(srv: RustDeskServer, source_id: Any) -> None:
    # 金鑰已經決定是哪一台；回報裡的 source_id 對不上（裝錯金鑰、複製過來的設定）就整批拒收
    if str(source_id) != str(srv.id):
        raise HTTPException(409, detail="source_id does not match this agent key")


@router.get("/installer.sh")
async def download_installer() -> PlainTextResponse:
    if not rustdesk_svc.AGENT_INSTALLER.exists():
        raise HTTPException(404, detail="installer not found")
    return PlainTextResponse(rustdesk_svc.AGENT_INSTALLER.read_text(encoding="utf-8"),
                             media_type="text/x-shellscript")


def _exact(path: Any, media_type: str) -> Response:
    """原封不動送出檔案的位元組：代理驗的是這份的簽章與 sha256。"""
    if not path.exists():
        raise HTTPException(404, detail="not found")
    return Response(path.read_bytes(), media_type=media_type)


@router.get("/agent.py")
async def download_agent() -> Response:
    return _exact(rustdesk_svc.AGENT_FILE, "text/x-python")


@router.get("/agent.py.sig")
async def download_agent_signature() -> Response:
    """agent.py 的簽章（base64 RSA / SHA-256）。代理自我更新只裝驗得過的版本。"""
    return _exact(rustdesk_svc.AGENT_FILE.with_name(rustdesk_svc.AGENT_FILE.name + ".sig"), "text/plain")


@router.post("/poll", response_model=RustDeskAgentPollOut)
async def agent_poll(
    payload: RustDeskAgentPollIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
    x_agent_tls: Annotated[str | None, Header()] = None,
) -> RustDeskAgentPollOut:
    srv = await _server_from_key(session, x_agent_key)
    _touch(srv, request, payload.version)
    srv.agent_tls_mode = await agent_trust.record_tls_mode(
        session, request, object_type="rustdesk_server", object_id=srv.id, name=srv.name,
        old=srv.agent_tls_mode, header=x_agent_tls)
    if payload.hostname:
        srv.agent_hostname = payload.hostname
    kc = payload.key_checks
    srv.agent_status = {"data_dir": payload.data_dir,
                        "receiver": payload.receiver.model_dump() if payload.receiver else None,
                        # 代理 1.1.0 起：讀不讀得到 hbbr／hbbs 日誌（讀不到就看不出客戶端的 Key 設錯）
                        "logs": kc.logs.model_dump() if kc is not None and kc.logs is not None else None,
                        # 代理 1.2.0 起：寫不寫得了 hbbs 的資料庫（「刪除舊註冊」要主機端以 --allow-delete 安裝）
                        "capabilities": payload.capabilities.model_dump() if payload.capabilities else None}
    if kc is not None and srv.enabled and (kc.fails or kc.ok):
        await rustdesk_svc.ingest_key_checks(session, srv, kc.model_dump())
    # 「立即同步」單次消費；停用中的伺服器不讀也不收，但代理照樣輪詢（重新啟用馬上生效）
    report_now = srv.enabled and srv.force_report_at is not None
    srv.force_report_at = None
    # 「測試」在拿到結果前每次輪詢都帶著（代理重啟、結果送失敗都還會再做一次）
    test_id = srv.test_id if srv.test_requested_at and not srv.test_result_at else None
    # 「刪除舊註冊」：超過一天沒取走的先判逾時，其餘等待中的帶給代理（結果送到前每次輪詢都帶著）
    await rustdesk_svc.expire_peer_deletes(session, srv.id)
    jobs = await rustdesk_svc.pending_delete_jobs(session, srv)
    out = RustDeskAgentPollOut(
        source_id=srv.id, enabled=srv.enabled, interval_seconds=srv.report_interval_seconds,
        api_listen=srv.enabled and srv.receive_reports, api_port=srv.api_port, report_now=report_now,
        test_id=test_id, agent_sha=rustdesk_svc.agent_sha(), poll_seconds=rustdesk_svc.AGENT_POLL_SECONDS,
        peer_deletes=jobs)
    await session.commit()
    return out


@router.post("/report")
async def agent_report(
    payload: RustDeskReport,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
    x_agent_version: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """hbbs 資料庫與線上狀態的結果。"""
    srv = await _server_from_key(session, x_agent_key)
    _same_server(srv, payload.source_id)
    _touch(srv, request, x_agent_version)
    if not srv.enabled:
        await session.commit()
        return {"status": "disabled"}
    counts = await rustdesk_svc.ingest_report(session, srv, payload.model_dump())
    sid, sname = srv.id, srv.name          # commit 之後屬性會過期，async 下不能再延遲載入
    await session.commit()
    # 作業頁：每台伺服器一列（每次完整回報更新同一列；輪詢與事件不算）
    from app.services.background_tasks import upsert_scheduled_task
    await upsert_scheduled_task(
        session, kind="rustdesk.sync", target_type="rustdesk_server", target_id=sid,
        target_label=sname, ok=not counts.get("error"), error=counts.get("error"),
        summary={k: counts.get(k) for k in ("peers", "online", "matched", "removed")})
    return {"status": "ok", **counts}


@router.post("/events")
async def agent_events(
    payload: RustDeskEventsIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
    x_agent_version: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """客戶端回報。事件逐筆驗證：一筆不合只丟那一筆（整批 422 會讓代理永遠重送同一批）。"""
    srv = await _server_from_key(session, x_agent_key)
    _same_server(srv, payload.source_id)
    _touch(srv, request, x_agent_version)
    if not srv.enabled or not srv.receive_reports:
        await session.commit()
        return {"status": "disabled"}
    good: list[dict[str, Any]] = []
    rejected = 0
    for raw in payload.events:
        try:
            good.append(RustDeskEvent.model_validate(raw).model_dump())
        except ValidationError:
            rejected += 1
    dropped = dict(payload.dropped)
    if rejected:
        dropped["rejected"] = dropped.get("rejected", 0) + rejected
    counts = await rustdesk_svc.ingest_events(session, srv, {"dropped": dropped, "events": good})
    await session.commit()
    return {"status": "ok", "rejected": rejected, **counts}


@router.post("/test-result")
async def agent_test_result(
    payload: RustDeskTestResultIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
    x_agent_version: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    srv = await _server_from_key(session, x_agent_key)
    _touch(srv, request, x_agent_version)
    # 只收目前這一次測試的結果；舊的（管理員又按了一次測試）丟掉
    if srv.test_id != payload.test_id or srv.test_result_at is not None:
        await session.commit()
        return {"status": "stale"}
    srv.test_result = {"checks": [c.model_dump() for c in payload.checks]}
    srv.test_result_at = datetime.now(UTC)
    await session.commit()
    return {"status": "ok"}


@router.post("/delete-result")
async def agent_delete_result(
    payload: RustDeskDeleteResultIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
    x_agent_version: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """「刪除舊註冊」的結果。刪掉了的裝置在這邊也一起刪，並要代理馬上重讀一次資料庫（裝置數跟著更新）。"""
    srv = await _server_from_key(session, x_agent_key)
    _same_server(srv, payload.source_id)
    _touch(srv, request, x_agent_version)
    res = await rustdesk_svc.apply_delete_results(session, srv, [r.model_dump() for r in payload.results])
    if res["deleted"]:
        # 動到了 RustDesk 的資料：留稽核（發起時另有 rustdesk.peer_delete_requested，記著是誰要求的）
        await append_audit(
            session, actor_user_id=None, actor_ip=request.client.host if request.client else None,
            actor_user_agent=f"rustdesk-agent/{(x_agent_version or '')[:32]}",
            object_type="rustdesk_server", object_id=str(srv.id), action="rustdesk.peer_deleted",
            diff={"name": srv.name, "ids": res["deleted"], "count": len(res["deleted"]),
                  "skipped_online": len(res["skipped_online"]), "not_found": len(res["not_found"]),
                  "failed": len(res["failed"])},
            request_id=getattr(request.state, "request_id", None))
        if srv.enabled:
            srv.force_report_at = datetime.now(UTC)
    await session.commit()
    return {"status": "ok", **{k: len(v) for k, v in res.items() if isinstance(v, list)}, "stale": res["stale"]}
