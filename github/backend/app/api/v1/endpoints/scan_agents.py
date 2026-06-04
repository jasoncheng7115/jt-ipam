"""Scan Agent CRUD（admin）。"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.address import IPAddress
from app.models.scan_agent import ScanAgent
from app.models.subnet import Subnet
from app.schemas.base import Paginated, StrictModel

router = APIRouter(prefix="/scan-agents", tags=["scan-agents"])

# agent 程式與安裝器檔案位置（repo 根目錄下 /agent）
_AGENT_DIR = __import__("pathlib").Path(__file__).resolve().parents[5] / "agent"


@router.get("/installer.sh", include_in_schema=False)
async def download_installer() -> Any:
    """一鍵安裝器（純程式碼、無密鑰）→ 可 curl | sudo bash。"""
    from fastapi.responses import PlainTextResponse
    p = _AGENT_DIR / "jt-ipam-agent-installer.sh"
    if not p.exists():
        raise HTTPException(404, detail="installer not found")
    return PlainTextResponse(p.read_text(), media_type="text/x-shellscript")


@router.get("/agent.py", include_in_schema=False)
async def download_agent() -> Any:
    from fastapi.responses import PlainTextResponse
    p = _AGENT_DIR / "jt_ipam_agent.py"
    if not p.exists():
        raise HTTPException(404, detail="agent not found")
    return PlainTextResponse(p.read_text(), media_type="text/x-python")


class ScanAgentCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    description: Annotated[str | None, Field(max_length=1024)] = None
    enabled: bool = True


class ScanAgentUpdate(StrictModel):
    description: Annotated[str | None, Field(max_length=1024)] = None
    enabled: bool | None = None


class ScanAgentRead(StrictModel):
    id: uuid.UUID
    name: str
    description: str | None
    agent_url: str | None
    enabled: bool
    has_key: bool = False
    agent_version: str | None = None
    subnet_count: int = 0
    last_seen_at: Any
    last_error: str | None
    created_at: Any
    updated_at: Any


class ScanAgentCreated(ScanAgentRead):
    # 明文 enrollment key：只在建立 / 重設時回傳一次，之後只存 hash
    enroll_key: str


def _key_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_key() -> str:
    return secrets.token_urlsafe(32)


def _to_read(obj: ScanAgent) -> ScanAgentRead:
    m = ScanAgentRead.model_validate(obj)
    m.has_key = bool(obj.enroll_key_hash)
    return m


async def _agent_from_key(session: AsyncSession, key: str | None) -> ScanAgent:
    """agent push 用：用 X-Agent-Key header 找 agent（驗證 + enabled）。"""
    if not key:
        raise HTTPException(401, detail="missing agent key")
    obj = (await session.execute(
        select(ScanAgent).where(ScanAgent.enroll_key_hash == _key_hash(key))
    )).scalar_one_or_none()
    if obj is None or not obj.enabled:
        raise HTTPException(401, detail="invalid agent key")
    return obj


@router.get("", response_model=Paginated[ScanAgentRead],
            dependencies=[Depends(require_admin)])
async def list_agents(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Paginated[ScanAgentRead]:
    rows = list(
        (await session.execute(
            select(ScanAgent).order_by(ScanAgent.name)
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
    )
    total = int(await session.scalar(select(func.count()).select_from(ScanAgent)) or 0)
    # 各 agent 負責掃描的子網路數
    counts: dict[Any, Any] = {}
    if rows:
        crows = (await session.execute(
            select(Subnet.scan_agent_id, func.count())
            .where(Subnet.scan_agent_id.in_([r.id for r in rows]))
            .group_by(Subnet.scan_agent_id)
        )).all()
        counts = {sid: n for sid, n in crows}
    items = []
    for r in rows:
        m = _to_read(r)
        m.subnet_count = int(counts.get(r.id, 0))
        items.append(m)
    return Paginated[ScanAgentRead](items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=ScanAgentCreated, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_agent(
    payload: ScanAgentCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ScanAgentCreated:
    """建立掃描代理（push 模型）。回傳一次性 enrollment key — 請複製到 agent 設定。"""
    raw_key = _new_key()
    obj = ScanAgent(
        name=payload.name,
        description=payload.description,
        enabled=payload.enabled,
        enroll_key_hash=_key_hash(raw_key),
    )
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Agent name conflict") from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="scan_agent", object_id=str(obj.id), action="create",
        diff={"name": obj.name},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    out = ScanAgentCreated(**_to_read(obj).model_dump(), enroll_key=raw_key)
    return out


@router.post("/{agent_id}/rotate-key", response_model=ScanAgentCreated,
             dependencies=[Depends(require_admin)])
async def rotate_key(
    agent_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ScanAgentCreated:
    """重新產生 enrollment key（舊 key 立即失效），回傳新 key 一次。"""
    obj = await session.get(ScanAgent, agent_id)
    if obj is None:
        raise HTTPException(404, detail="Agent not found")
    raw_key = _new_key()
    obj.enroll_key_hash = _key_hash(raw_key)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="scan_agent", object_id=str(obj.id), action="rotate_key",
        diff={"name": obj.name},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return ScanAgentCreated(**_to_read(obj).model_dump(), enroll_key=raw_key)


@router.patch("/{agent_id}", response_model=ScanAgentRead,
              dependencies=[Depends(require_admin)])
async def update_agent(
    agent_id: uuid.UUID,
    payload: ScanAgentUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ScanAgentRead:
    obj = await session.get(ScanAgent, agent_id)
    if obj is None:
        raise HTTPException(404, detail="Agent not found")

    before = {"enabled": obj.enabled}
    if payload.description is not None:
        obj.description = payload.description
    if payload.enabled is not None:
        obj.enabled = payload.enabled

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="scan_agent", object_id=str(obj.id), action="update",
        diff={"before": before},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return _to_read(obj)


class AgentSubnetsOut(StrictModel):
    subnet_ids: list[uuid.UUID]


class AgentSubnetsPatch(StrictModel):
    subnet_ids: list[uuid.UUID]


@router.get("/{agent_id}/subnets", response_model=AgentSubnetsOut,
            dependencies=[Depends(require_admin)])
async def get_agent_subnets(
    agent_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentSubnetsOut:
    """此 agent 目前負責掃描的子網路 id。"""
    if await session.get(ScanAgent, agent_id) is None:
        raise HTTPException(404, detail="Agent not found")
    ids = (await session.execute(
        select(Subnet.id).where(Subnet.scan_agent_id == agent_id)
    )).scalars().all()
    return AgentSubnetsOut(subnet_ids=list(ids))


@router.put("/{agent_id}/subnets", response_model=AgentSubnetsOut,
            dependencies=[Depends(require_admin)])
async def set_agent_subnets(
    agent_id: uuid.UUID,
    payload: AgentSubnetsPatch,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentSubnetsOut:
    """設定此 agent 要掃哪些子網路（與子網路編輯頁的 scan_agent 是同一份設定）。

    指派的子網路會自動啟用掃描；取消指派的清掉 scan_agent（保留 scan_enabled 不動）。
    """
    obj = await session.get(ScanAgent, agent_id)
    if obj is None:
        raise HTTPException(404, detail="Agent not found")
    want = set(payload.subnet_ids)
    current = set((await session.execute(
        select(Subnet.id).where(Subnet.scan_agent_id == agent_id)
    )).scalars().all())
    to_clear = current - want
    if to_clear:
        await session.execute(
            sa_update(Subnet).where(Subnet.id.in_(to_clear))
            .values(scan_agent_id=None)
        )
    if want:
        await session.execute(
            sa_update(Subnet).where(Subnet.id.in_(want))
            .values(scan_agent_id=agent_id, scan_enabled=True)
        )
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="scan_agent", object_id=str(agent_id), action="update",
        diff={"target": "agent_subnets", "count": len(want)},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return AgentSubnetsOut(subnet_ids=list(want))


# ─────────────────── Agent push 協定（用 X-Agent-Key 驗證，非 JWT） ───────────────────


def _agent_sha() -> str:
    """目前 server 上 agent 程式的 sha256（給 agent 自動更新比對用）。"""
    import hashlib
    p = _AGENT_DIR / "jt_ipam_agent.py"
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""


class AgentPollOut(StrictModel):
    agent: str
    subnets: list[dict[str, Any]]   # [{subnet_id, cidr}]
    interval_seconds: int = 300
    agent_sha: str = ""             # server 端 agent.py 的 sha256；不同→agent 自動更新


@router.get("/poll", response_model=AgentPollOut)
async def agent_poll(
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
    x_agent_version: Annotated[str | None, Header()] = None,
) -> AgentPollOut:
    """Agent 主動拉取「要掃哪些網段」。回傳指派給此 agent 且啟用掃描的子網路。"""
    agent = await _agent_from_key(session, x_agent_key)
    agent.last_seen_at = datetime.now(UTC)
    if x_agent_version:
        agent.agent_version = x_agent_version[:32]
    rows = (await session.execute(
        select(Subnet.id, Subnet.cidr).where(
            Subnet.scan_agent_id == agent.id,
            Subnet.scan_enabled.is_(True),
        )
    )).all()
    await session.commit()
    return AgentPollOut(
        agent=agent.name,
        subnets=[{"subnet_id": str(sid), "cidr": str(cidr)} for sid, cidr in rows],
        agent_sha=_agent_sha(),
    )


class AgentReportItem(StrictModel):
    ip: str
    alive: bool = True
    mac: str | None = None


class AgentReportIn(StrictModel):
    results: Annotated[list[AgentReportItem], Field(max_length=100_000)]


@router.post("/report")
async def agent_report(
    payload: AgentReportIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
) -> dict[str, int]:
    """Agent push 掃描結果：對有回應的 IP stamp last_seen_scanner（+補 MAC）。"""
    import ipaddress as _ipaddr
    agent = await _agent_from_key(session, x_agent_key)
    now = datetime.now(UTC)
    # 只在「指派給此 agent 的子網路」範圍內配對 —— 解決重疊網段（A/B 客戶都用
    # 192.168.1.0/24）誤配到別人子網路的問題。同時帶出 CIDR，供自動新增比對。
    agent_subnets = (await session.execute(
        select(Subnet.id, Subnet.cidr, Subnet.scan_enabled)
        .where(Subnet.scan_agent_id == agent.id)
    )).all()
    agent_subnet_ids = {s.id for s in agent_subnets}
    # 可自動新增的網段（有開掃描）→ (network, subnet_id)，依首碼長度由長到短比對
    addable_nets: list[tuple[Any, ...]] = []
    for s in agent_subnets:
        if not s.scan_enabled:
            continue
        try:
            addable_nets.append((_ipaddr.ip_network(str(s.cidr), strict=False), s.id))
        except ValueError:
            continue
    addable_nets.sort(key=lambda x: x[0].prefixlen, reverse=True)

    updated = 0
    created = 0
    for item in payload.results:
        if not item.alive:
            continue
        stmt = select(IPAddress).where(IPAddress.ip == item.ip)
        if agent_subnet_ids:
            stmt = stmt.where(IPAddress.subnet_id.in_(agent_subnet_ids))
        # 重疊網段下可能有多筆同 IP；限定 agent 子網路後通常唯一，取第一筆
        ipa = (await session.execute(stmt.limit(1))).scalar_one_or_none()
        if ipa is None:
            # 掃描代理發現的新 IP → 自動加進它所屬（有開掃描）的子網路
            try:
                aip = _ipaddr.ip_address(str(item.ip).split("/")[0])
            except ValueError:
                continue
            sub_id = next((sid for net, sid in addable_nets if aip in net), None)
            if sub_id is None:
                continue
            ipa = IPAddress(
                subnet_id=sub_id, ip=str(item.ip).split("/")[0], state="active",
                discovery_source="scanner",
                description="掃描代理自動探索新增",
                note=(f"此 IP 由掃描代理「{agent.name}」於 "
                      f"{now.astimezone().strftime('%Y-%m-%d %H:%M')} 主動探索時發現並自動建立。"),
                last_seen_scanner=now,
            )
            session.add(ipa)
            created += 1
        else:
            ipa.last_seen_scanner = now
        if item.mac:
            from app.services.arp_precedence import consider_mac
            await consider_mac(session, ip=ipa, mac=item.mac, source="scanner")
        updated += 1
    agent.last_seen_at = now
    agent.last_error = None
    await session.commit()
    return {"received": len(payload.results), "updated": updated}


@router.delete("/{agent_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_agent(
    agent_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(ScanAgent, agent_id)
    if obj is None:
        raise HTTPException(404, detail="Agent not found")

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="scan_agent",
        object_id=str(obj.id),
        action="delete",
        diff={"before": {"name": obj.name}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()
