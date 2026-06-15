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
from app.core import scan_probes
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


@router.get("/probes")
async def list_probes(_user: CurrentUser) -> dict[str, Any]:
    """探測項目目錄 + OS 家族（前端三處設定共用：代理 / 子網路 / IP）。
    需登入即可（子網路 / IP 編輯者也要用），非僅 admin。"""
    from app.core.os_fingerprint import families_for_api
    return {"probes": scan_probes.catalog_for_api(), "os_families": families_for_api()}


class ScanAgentCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    description: Annotated[str | None, Field(max_length=1024)] = None
    enabled: bool = True
    enabled_probes: list[str] | None = None
    probe_intervals: dict[str, int] | None = None


class ScanAgentUpdate(StrictModel):
    description: Annotated[str | None, Field(max_length=1024)] = None
    enabled: bool | None = None
    enabled_probes: list[str] | None = None
    probe_intervals: dict[str, int] | None = None


class ScanAgentRead(StrictModel):
    id: uuid.UUID
    name: str
    description: str | None
    agent_url: str | None
    enabled: bool
    has_key: bool = False
    agent_version: str | None = None
    server_agent_version: str | None = None   # server 端 agent.py 版本；UI 比對標「可更新」
    last_source_ip: str | None = None
    enabled_probes: list[str] = Field(default_factory=lambda: ["icmp"])
    probe_intervals: dict[str, int] | None = None
    available_probes: list[str] | None = None
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
    m.server_agent_version = _server_agent_version()
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
        enabled_probes=scan_probes.normalize_probes(payload.enabled_probes)
        or list(scan_probes.DEFAULT_AGENT_PROBES),
        probe_intervals=payload.probe_intervals or None,
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


@router.post("/{agent_id}/scan-now", status_code=202,
             dependencies=[Depends(require_admin)])
async def scan_now(
    agent_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """立刻執行一次：設旗標，代理下次 poll（最多一個間隔）取走後本輪所有探測強制立即跑。"""
    obj = await session.get(ScanAgent, agent_id)
    if obj is None:
        raise HTTPException(404, detail="Agent not found")
    obj.force_scan_at = datetime.now(UTC)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="scan_agent", object_id=str(obj.id), action="scan_now",
        diff={"name": obj.name},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    # 代理下次 poll 的等待上限 = 快迴圈節奏
    eta = scan_probes.fast_interval(scan_probes.probe_intervals(obj.probe_intervals))
    return {"queued": True, "eta_seconds": eta}


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
    if payload.enabled_probes is not None:
        obj.enabled_probes = scan_probes.normalize_probes(payload.enabled_probes) or ["icmp"]
    if payload.probe_intervals is not None:
        obj.probe_intervals = payload.probe_intervals or None

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


def _server_agent_version() -> str | None:
    """從 server 端 agent.py 解析 AGENT_VERSION，給 UI 標示「代理版本落後」。"""
    p = _AGENT_DIR / "jt_ipam_agent.py"
    try:
        import re
        m = re.search(r'^AGENT_VERSION\s*=\s*["\']([^"\']+)["\']', p.read_text(), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


class AgentPollOut(StrictModel):
    agent: str
    # 每個子網路：{subnet_id, cidr, probes:[...]}（probes = 子網路要跑 ∩ 代理能力）
    subnets: list[dict[str, Any]]
    interval_seconds: int = 300          # 快迴圈節奏（相容舊欄位名）
    intervals: dict[str, int] = Field(default_factory=dict)   # 各 probe 間隔（秒）
    # 已知 IP 的逐項略過：{"<ip>": ["icmp", ...]}；代理對該 IP 扣掉這些 probe
    ip_overrides: dict[str, list[str]] = Field(default_factory=dict)
    agent_sha: str = ""             # server 端 agent.py 的 sha256；不同→agent 自動更新
    force_scan: bool = False        # 「立刻執行一次」：本輪所有探測強制到期立即跑


@router.get("/poll", response_model=AgentPollOut)
async def agent_poll(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
    x_agent_version: Annotated[str | None, Header()] = None,
    x_agent_probes: Annotated[str | None, Header()] = None,
) -> AgentPollOut:
    """Agent 主動拉取「要掃哪些網段、各網段跑哪些探測、各探測間隔、逐 IP 略過」。"""
    agent = await _agent_from_key(session, x_agent_key)
    agent.last_seen_at = datetime.now(UTC)
    # 記錄 agent 連上來的來源 IP（走 nginx 反代要取 X-Forwarded-For 的第一個）
    _xff = request.headers.get("x-forwarded-for")
    agent.last_source_ip = (_xff.split(",")[0].strip() if _xff
                            else (request.client.host if request.client else None))
    if x_agent_version:
        agent.agent_version = x_agent_version[:32]
    # 代理用 X-Agent-Probes 回報它「裝得起」哪些（逗號分隔）→ UI 反灰用
    if x_agent_probes is not None:
        agent.available_probes = scan_probes.normalize_probes(
            [p.strip() for p in x_agent_probes.split(",") if p.strip()]
        )

    cap = set(agent.enabled_probes or ["icmp"])   # 代理能力天花板
    rows = (await session.execute(
        select(Subnet.id, Subnet.cidr, Subnet.scan_method).where(
            Subnet.scan_agent_id == agent.id,
            Subnet.scan_enabled.is_(True),
            Subnet.archived_at.is_(None),
        )
    )).all()
    subnets_out: list[dict[str, Any]] = []
    sub_ids: list[Any] = []
    for sid, cidr, methods in rows:
        sub_ids.append(sid)
        probes = [p for p in scan_probes.normalize_probes(list(methods or [])) if p in cap]
        subnets_out.append({"subnet_id": str(sid), "cidr": str(cidr), "probes": probes})

    # 逐 IP 略過（只送有設定的，量小）
    ip_overrides: dict[str, list[str]] = {}
    if sub_ids:
        orows = (await session.execute(
            select(IPAddress.ip, IPAddress.excluded_probes).where(
                IPAddress.subnet_id.in_(sub_ids),
                func.cardinality(IPAddress.excluded_probes) > 0,
            )
        )).all()
        for ip, excl in orows:
            ip_overrides[str(ip)] = scan_probes.normalize_probes(list(excl or []))

    intervals = scan_probes.probe_intervals(agent.probe_intervals)
    # 「立刻執行一次」：有旗標就回 force_scan=True 並清掉（一次性消費）
    force_scan = agent.force_scan_at is not None
    if force_scan:
        agent.force_scan_at = None
    await session.commit()
    return AgentPollOut(
        agent=agent.name,
        subnets=subnets_out,
        interval_seconds=scan_probes.fast_interval(intervals),
        intervals=intervals,
        ip_overrides=ip_overrides,
        force_scan=force_scan,
        agent_sha=_agent_sha(),
    )


class AgentReportItem(StrictModel):
    ip: str
    alive: bool = True
    mac: str | None = None
    rdns: str | None = None          # 反解 PTR / NetBIOS / mDNS 主機名稱
    os_guess: str | None = None      # OS 偵測原始字串
    open_ports: list[int] | None = None
    probes_run: list[str] | None = None   # 這輪實際對此 IP 跑了哪些 probe（回填 last_run）


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
                effective_status="online (scanner)",
            )
            session.add(ipa)
            # ipa.id 由 DB server_default（gen_random_uuid）產生；session 設 autoflush=False，
            # 不 flush 的話 ipa.id 仍是 None，後面 consider_mac / apply_observation 會用
            # ip_id=None 建 FK row → NOT NULL 違規 500（rdns/mdns/os 等帶 hostname 的回報才會踩到）。
            await session.flush()
            created += 1
        else:
            ipa.last_seen_scanner = now
            # 掃描代理看到回應＝即時上線證據，立刻更新實際狀態（不必等 LibreNMS sync）
            from app.services.librenms import mark_scanner_seen
            await mark_scanner_seen(session, ipa, now)
        if item.mac:
            from app.services.arp_precedence import consider_mac
            await consider_mac(session, ip=ipa, mac=item.mac, source="scanner")
        # OS 偵測：存原始字串 + 正規化家族（前端依 family 配 icon）
        if item.os_guess:
            from app.core.os_fingerprint import normalize_os
            ipa.os_guess = item.os_guess[:160]
            ipa.os_family = normalize_os(item.os_guess)
        # 反解 / NetBIOS / mDNS 主機名稱 → 走既有觀測優先序（source=scanner，不會 thrash）
        if item.rdns:
            from app.services.hostname import apply_observation
            await apply_observation(session, ip=ipa, source="scanner",
                                    hostname=item.rdns, tiebreak_min=True)
        # 記各 probe 上次執行時間（給「下次到期」顯示）
        if item.probes_run:
            lr = dict(ipa.probe_last_run or {})
            for p in scan_probes.normalize_probes(item.probes_run):
                lr[p] = now.isoformat()
            ipa.probe_last_run = lr
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
