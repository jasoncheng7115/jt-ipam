"""Scan Agent CRUD（admin）。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core import scan_probes
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.sqlin import in_values
from app.core.ui_error import ui_detail
from app.models.address import IPAddress
from app.models.scan_agent import ScanAgent
from app.models.subnet import Subnet
from app.schemas.base import Paginated, StrictModel
from app.services import agent_trust

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


def _exact(name: str, media_type: str) -> Any:
    """原封不動送出檔案的位元組：代理驗的是這份的簽章與 sha256（read_text 會改掉換行）。"""
    from fastapi.responses import Response
    p = _AGENT_DIR / name
    if not p.exists():
        raise HTTPException(404, detail="not found")
    return Response(p.read_bytes(), media_type=media_type)


@router.get("/agent.py", include_in_schema=False)
async def download_agent() -> Any:
    return _exact("jt_ipam_agent.py", "text/x-python")


@router.get("/agent.py.sig", include_in_schema=False)
async def download_agent_signature() -> Any:
    """agent.py 的簽章（base64 RSA / SHA-256）。代理自我更新只裝驗得過的版本。"""
    return _exact("jt_ipam_agent.py.sig", "text/plain")


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
    auto_create_ips: bool = False
    enabled_probes: list[str] | None = None
    probe_intervals: dict[str, int] | None = None


class ScanAgentUpdate(StrictModel):
    #: 可以改名（10-22）：名稱只是顯示用，代理靠金鑰辨識；名稱仍不可重複
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    enabled: bool | None = None
    auto_create_ips: bool | None = None
    enabled_probes: list[str] | None = None
    probe_intervals: dict[str, int] | None = None
    # 主控台中繼（issue #24 階段二）：允許這台代理中繼、同時中繼上限、允許的埠（"22,3389,5900-5910"）
    relay_allowed: bool | None = None
    relay_max_sessions: Annotated[int | None, Field(ge=1, le=64)] = None
    relay_ports: Annotated[str | None, Field(max_length=200)] = None


class ScanAgentRead(StrictModel):
    id: uuid.UUID
    name: str
    description: str | None
    agent_url: str | None
    enabled: bool
    # 跑在 jt-ipam 主機上的那一個（安裝時自動建立）—— UI 靠它判斷「本機有沒有代理」
    is_local: bool = False
    # 掃到未登錄的位址要不要自動建立（預設關閉，見 model 的說明）
    auto_create_ips: bool = False
    has_key: bool = False
    agent_version: str | None = None
    tls_mode: str | None = None               # 代理怎麼驗證伺服器：pinned / ca / insecure；None＝舊版代理
    server_agent_version: str | None = None   # server 端 agent.py 版本；UI 比對標「可更新」
    last_source_ip: str | None = None
    enabled_probes: list[str] = Field(default_factory=lambda: ["icmp"])
    probe_intervals: dict[str, int] | None = None
    available_probes: list[str] | None = None
    # 相依工具盤點：[{name, installed, version, probes, package}]（哪些裝了/版本/缺）
    tools: list[dict[str, Any]] | None = None
    subnet_count: int = 0
    # 最近一輪的負載摘要（services/scan_load.summary）：{ratio, level, duration_s, interval_s,
    # heavy_backlog, truncated, at}；代理還沒回報過每輪統計（1.10.0 以前）就是 None
    load: dict[str, Any] | None = None
    # 主控台中繼：管理員的允許、上限、代理回報的能力（舊代理＝None）、目前中繼中的工作階段數
    relay_allowed: bool = False
    relay_max_sessions: int = 4
    relay_ports: str = "22,3389,5900-5910"
    relay_caps: dict[str, Any] | None = None
    relay_active: int = 0
    last_seen_at: Any
    last_error: str | None
    created_at: Any
    updated_at: Any


async def _with_relay_active(m: ScanAgentRead) -> ScanAgentRead:
    """目前中繼中的工作階段數（Redis）。Redis 不通時當 0：這只是顯示，不能讓代理清單整頁壞掉。"""
    if m.relay_allowed:
        try:
            from app.services.console_relay import active_count
            m.relay_active = await active_count(m.id)
        except Exception:
            m.relay_active = 0
    return m


class ScanAgentCreated(ScanAgentRead):
    # 明文 enrollment key：只在建立 / 重設時回傳一次，之後只存 hash
    enroll_key: str


def _key_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_key() -> str:
    return secrets.token_urlsafe(32)


# 相依工具的顯示對照表（probes＝啟用哪些探測、package＝apt 套件名）。agent 只回報 installed/version。
_DEP_TOOL_META: dict[str, tuple[list[str], str]] = {
    "python3": ([], "python3"),
    "ping": (["icmp"], "iputils-ping"),
    "ip": (["arp"], "iproute2"),
    "nmap": (["os", "ports"], "nmap"),
    "nmblookup": (["netbios"], "samba-common-bin"),
    "nbtscan": (["netbios"], "nbtscan"),
    "avahi-resolve": (["mdns"], "avahi-utils"),
    # SNMP 收集（0217）：net-snmp 用戶端工具；不是逐子網路的探測，是「外部系統整合 → SNMP」的收集目標
    "snmpbulkwalk": (["snmp"], "snmp"),
}


def _parse_tools_header(raw: str) -> list[dict[str, Any]]:
    """X-Agent-Tools: `name|installed(1/0)|version` 以分號相接 → [{name, installed, version}]。"""
    out: list[dict[str, Any]] = []
    for part in raw.split(";"):
        seg = part.split("|")
        name = seg[0].strip()[:40] if seg else ""
        if not name:
            continue
        installed = len(seg) > 1 and seg[1].strip() == "1"
        version = seg[2].strip()[:32] if len(seg) > 2 and seg[2].strip() else None
        out.append({"name": name, "installed": installed, "version": version})
        if len(out) >= 50:
            break
    return out


def _merge_tool_meta(tools: list | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    out: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        probes, pkg = _DEP_TOOL_META.get(t.get("name"), ([], t.get("name")))
        out.append({**t, "probes": probes, "package": pkg})
    return out


def _to_read(obj: ScanAgent) -> ScanAgentRead:
    m = ScanAgentRead.model_validate(obj)
    m.has_key = bool(obj.enroll_key_hash)
    m.server_agent_version = _server_agent_version()
    m.tools = _merge_tool_meta(obj.tools)
    from app.services.scan_load import summary
    m.load = summary(obj)
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
    from app.core.rate_limit import limit_agent
    await limit_agent("scan", obj.id)
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
            .where(Subnet.scan_agent_id.in_([r.id for r in rows]))  # bounded: scan agents
            .group_by(Subnet.scan_agent_id)
        )).all()
        counts = {sid: n for sid, n in crows}
    items = []
    for r in rows:
        m = await _with_relay_active(_to_read(r))
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
        auto_create_ips=payload.auto_create_ips,
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

    before = {"name": obj.name, "enabled": obj.enabled, "auto_create_ips": obj.auto_create_ips,
              "relay_allowed": obj.relay_allowed, "relay_max_sessions": obj.relay_max_sessions,
              "relay_ports": obj.relay_ports}
    if payload.relay_ports is not None:
        from app.services.relay_scope import normalize_ports
        try:
            obj.relay_ports = normalize_ports(payload.relay_ports)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=ui_detail(
                "relay_ports_invalid", f"允許中繼的埠格式不對：{exc}（例：22,3389,5900-5910）",
                value=str(exc))) from None
    if payload.relay_allowed is not None:
        obj.relay_allowed = payload.relay_allowed
    if payload.relay_max_sessions is not None:
        obj.relay_max_sessions = payload.relay_max_sessions
    if payload.name is not None and payload.name.strip() and payload.name.strip() != obj.name:
        new_name = payload.name.strip()
        if (await session.execute(select(ScanAgent.id).where(ScanAgent.name == new_name,
                                                             ScanAgent.id != obj.id))).first():
            raise HTTPException(409, detail=ui_detail("scan_agent_name_taken", "this name is already used",
                                                      name=new_name[:60]))
        obj.name = new_name
    if payload.description is not None:
        obj.description = payload.description
    if payload.enabled is not None:
        obj.enabled = payload.enabled
    if payload.auto_create_ips is not None:
        obj.auto_create_ips = payload.auto_create_ips
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
        # 打開中繼＝這台代理變成通往客戶網路的跳點，改了什麼要看得到
        diff={"before": before, "changes": payload.model_dump(exclude_none=True)},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return await _with_relay_active(_to_read(obj))


class AgentSubnetItem(StrictModel):
    id: uuid.UUID
    cidr: str
    description: str | None = None
    scan_enabled: bool = False


class AgentSubnetsOut(StrictModel):
    subnet_ids: list[uuid.UUID]
    #: 給代理頁點子網路數字時彈出的清單（10-22）；PUT 回應不帶
    subnets: list[AgentSubnetItem] | None = None


class AgentSubnetsPatch(StrictModel):
    subnet_ids: list[uuid.UUID]
    # 要指派的子網路目前由別台代理掃描時，要明確帶 true 才搬過來（客戶 2026-11-05：以前默默搶走）
    move_from_other_agents: bool = False


@router.get("/{agent_id}/subnets", response_model=AgentSubnetsOut,
            dependencies=[Depends(require_admin)])
async def get_agent_subnets(
    agent_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentSubnetsOut:
    """此 agent 目前負責掃描的子網路（id，以及 CIDR／說明／是否啟用掃描，依 CIDR 排序）。"""
    if await session.get(ScanAgent, agent_id) is None:
        raise HTTPException(404, detail="Agent not found")
    rows = (await session.execute(
        select(Subnet.id, Subnet.cidr, Subnet.description, Subnet.scan_enabled)
        .where(Subnet.scan_agent_id == agent_id).order_by(Subnet.cidr)
    )).all()
    return AgentSubnetsOut(subnet_ids=[r.id for r in rows], subnets=[
        AgentSubnetItem(id=r.id, cidr=str(r.cidr), description=r.description, scan_enabled=bool(r.scan_enabled))
        for r in rows])


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
    # 別台代理手上的子網路：以前直接覆寫 scan_agent_id，客戶高雄的網段就這樣被搬到本機代理，
    # 本機到不了那個網段 → 整段判離線好幾週（2026-11-05）。沒有明確確認就擋下，講出是哪幾個、原本哪台。
    taken = (await session.execute(
        select(Subnet.cidr, ScanAgent.name).join(ScanAgent, ScanAgent.id == Subnet.scan_agent_id)
        .where(in_values(Subnet.id, want), Subnet.scan_agent_id != agent_id)
        .order_by(Subnet.cidr))).all() if want else []
    moved = [{"subnet": str(c), "from_agent": n} for c, n in taken]
    if moved and not payload.move_from_other_agents:
        raise HTTPException(409, detail=ui_detail(
            "scan_agent_subnets_taken", "Some subnets are scanned by another agent; confirm to move them",
            count=len(moved), subnets=", ".join(f"{m['subnet']} ({m['from_agent']})" for m in moved[:20])))
    to_clear = current - want
    if to_clear:
        await session.execute(
            sa_update(Subnet).where(in_values(Subnet.id, to_clear))
            .values(scan_agent_id=None)
        )
    if want:
        await session.execute(
            sa_update(Subnet).where(in_values(Subnet.id, want))
            .values(scan_agent_id=agent_id, scan_enabled=True)
        )
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="scan_agent", object_id=str(agent_id), action="update",
        diff={"target": "agent_subnets", "count": len(want), **({"moved": moved} if moved else {})},
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
    # 獨立 ISC DHCP Server（issue #45）：有來源指到這台代理時才帶 {source_id, interval_seconds}，
    # 代理才會去讀本機的 dhcpd.conf／dhcpd.leases；沒有就是 None，代理完全不碰那兩個檔
    dhcpd: dict[str, Any] | None = None
    # 主控台中繼（issue #24 階段二）：網頁上允許的範圍（被指派的子網路、埠、同時上限）。代理只中繼範圍內的目標；
    # 系統設定與掃描代理頁兩道開關都開才給，否則是空的（代理什麼都不中繼）
    relay_cidrs: list[str] = Field(default_factory=list)
    relay_ports: list[int] = Field(default_factory=list)
    relay_max: int = 0
    # SNMP 收集（0217，代理 1.19.0 起）：指派給這台代理的收集目標（含解密後的憑證，只給被指派的代理）與負載上限。
    # 每筆 {target_id, ip, port, sections:{arp:300,...}, credential:{version, community | username/
    # security_level/auth_protocol/auth_passphrase/priv_protocol/priv_passphrase, timeout_s, retries}}。
    # 代理沒有 SNMP 能力（沒裝 net-snmp 用戶端工具）就是空的；舊代理不認得這兩個欄位，照舊忽略
    snmp: list[dict[str, Any]] = Field(default_factory=list)
    snmp_budget: dict[str, int] = Field(default_factory=dict)


def parse_relay_header(raw: str | None) -> dict[str, Any] | None:
    """`X-Agent-Relay: enabled=1;ports=22,3389,5900-5910;max=4;pinned=0` → 能力；格式不對回 None。

    埠的範圍展開成清單（最多 256 個，代理端預設 13 個）。
    """
    if raw is None:
        return None
    out: dict[str, Any] = {"enabled": False, "ports": [], "max": 0, "pinned": False}
    for part in raw.split(";")[:8]:
        k, _, v = part.strip().partition("=")
        k, v = k.strip().lower(), v.strip()
        if k == "enabled":
            out["enabled"] = v in ("1", "true", "yes")
        elif k == "max" and v.isdigit():
            out["max"] = min(int(v), 64)
        elif k == "pinned":
            out["pinned"] = v in ("1", "true", "yes")
        elif k == "ports":
            ports: list[int] = []
            for tok in v.split(",")[:64]:
                a, _, b = tok.strip().partition("-")
                if not a.isdigit() or (b and not b.isdigit()):
                    continue
                lo, hi = int(a), int(b or a)
                if 1 <= lo <= hi <= 65535 and hi - lo < 256:
                    ports.extend(range(lo, hi + 1))
            out["ports"] = sorted(set(ports))[:256]
    return out


@router.get("/poll", response_model=AgentPollOut)
async def agent_poll(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
    x_agent_version: Annotated[str | None, Header()] = None,
    x_agent_probes: Annotated[str | None, Header()] = None,
    x_agent_tools: Annotated[str | None, Header()] = None,
    x_agent_relay: Annotated[str | None, Header()] = None,
    x_agent_tls: Annotated[str | None, Header()] = None,
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
    agent.tls_mode = await agent_trust.record_tls_mode(
        session, request, object_type="scan_agent", object_id=agent.id, name=agent.name,
        old=agent.tls_mode, header=x_agent_tls)
    # 代理用 X-Agent-Probes 回報它「裝得起」哪些（逗號分隔）→ UI 反灰用
    if x_agent_probes is not None:
        agent.available_probes = scan_probes.normalize_probes(
            [p.strip() for p in x_agent_probes.split(",") if p.strip()]
        )
    # 相依工具盤點（裝了哪些 / 版本）→ 掃描代理頁「相依套件 N/M」
    if x_agent_tools is not None:
        agent.tools = _parse_tools_header(x_agent_tools)
    # 中繼能力（代理 1.15.0 起）：舊代理不帶這個標頭 → 視為不支援
    agent.relay_caps = parse_relay_header(x_agent_relay)

    cap = set(agent.enabled_probes or ["icmp"])   # 代理能力天花板
    rows = (await session.execute(
        select(Subnet.id, Subnet.cidr, Subnet.scan_method, Subnet.scan_scope).where(
            Subnet.scan_agent_id == agent.id,
            Subnet.scan_enabled.is_(True),
            Subnet.archived_at.is_(None),
        )
    )).all()
    # 大子網路的掃描計畫（已知位址每輪掃＋其餘背景探索；services/scan_plan）。小子網路沒有計畫＝照舊全掃，
    # 舊代理不認得 plan 欄位也是照舊全掃
    from app.services.scan_plan import plans as _scan_plans
    plan_by_id = await _scan_plans(session, rows)
    subnets_out: list[dict[str, Any]] = []
    sub_ids: list[Any] = []
    for sid, cidr, methods, _scope in rows:
        sub_ids.append(sid)
        probes = [p for p in scan_probes.normalize_probes(list(methods or [])) if p in cap]
        entry: dict[str, Any] = {"subnet_id": str(sid), "cidr": str(cidr), "probes": probes}
        if plan_by_id.get(sid) is not None:
            entry["plan"] = plan_by_id[sid]
        subnets_out.append(entry)

    # 逐 IP 略過（只送有設定的，量小）
    ip_overrides: dict[str, list[str]] = {}
    if sub_ids:
        orows = (await session.execute(
            select(IPAddress.ip, IPAddress.excluded_probes).where(
                in_values(IPAddress.subnet_id, sub_ids),
                func.cardinality(IPAddress.excluded_probes) > 0,
            )
        )).all()
        for ip, excl in orows:
            ip_overrides[str(ip)] = scan_probes.normalize_probes(list(excl or []))

    intervals = scan_probes.probe_intervals(agent.probe_intervals)
    from app.models.dhcp_standalone import IscDhcpServer
    isc = (await session.execute(select(IscDhcpServer).where(
        IscDhcpServer.agent_id == agent.id, IscDhcpServer.enabled.is_(True)))).scalars().first()
    dhcpd = ({"source_id": str(isc.id), "interval_seconds": isc.report_interval_seconds}
             if isc is not None else None)
    # 「立刻執行一次」：有旗標就回 force_scan=True 並清掉（一次性消費）
    force_scan = agent.force_scan_at is not None
    if force_scan:
        agent.force_scan_at = None
    from app.services.relay_scope import relay_scope
    scope = await relay_scope(session, agent)
    from app.services import snmp as snmp_svc
    snmp_targets = await snmp_svc.poll_targets(session, agent)
    await session.commit()
    return AgentPollOut(
        snmp=snmp_targets,
        snmp_budget=dict(snmp_svc.BUDGET) if snmp_targets else {},
        agent=agent.name,
        subnets=subnets_out,
        relay_cidrs=scope["cidrs"],
        relay_ports=scope["ports"],
        relay_max=scope["max"],
        interval_seconds=scan_probes.fast_interval(intervals),
        intervals=intervals,
        ip_overrides=ip_overrides,
        force_scan=force_scan,
        agent_sha=_agent_sha(),
        dhcpd=dhcpd,
    )


class AgentReportItem(StrictModel):
    ip: str
    alive: bool = True
    mac: str | None = None
    rdns: str | None = None          # 反解 PTR 主機名稱
    netbios: str | None = None       # NetBIOS 名稱（nmblookup -A）
    mdns: str | None = None          # mDNS 名稱（avahi-resolve，.local）
    os_guess: str | None = None      # OS 偵測原始字串
    #: 代理 1.14.0 起：定期 OS 偵測的 nmap 結構化結果（埠、服務、banner、網頁標題、憑證、smb-os-discovery），
    #: 伺服器用 IP 探測同一套判讀推出 OS 與設備類型（services/device_identity）。過大就丟掉、只用 os_guess
    nmap: dict[str, Any] | None = None
    open_ports: list[int] | None = None
    probes_run: list[str] | None = None   # 這輪實際對此 IP 跑了哪些 probe（回填 last_run）
    # False＝背景重量探測（反解／NetBIOS／mDNS／OS）補的資料，不是上線證據：不更新最後出現時間、
    # 不自動新增 IP。反解是 DNS 回答的，不是主機本身；OS 指紋可能是幾分鐘前排進佇列的（代理 1.10.0 起）
    liveness: bool = True

    @field_validator("nmap")
    @classmethod
    def _cap_nmap(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        # 一台主機的精簡結果通常幾 KB；不正常的大小不拿來判讀，但也不讓整批回報失敗
        if v is None:
            return None
        try:
            return v if len(json.dumps(v, default=str)) <= _NMAP_REPORT_MAX else None
        except (TypeError, ValueError):
            return None


_NMAP_REPORT_MAX = 64 * 1024
_UNSET: Any = object()


async def _virtual_guests(session: AsyncSession, items: list[Any]) -> dict[str, str]:
    """這次回報裡、有 OS 偵測結果的位址中，哪些是虛擬化整合回報的虛擬機或容器網卡（依 IP 或 MAC）。

    虛擬化讓 nmap 的 TCP/IP 指紋失準（PVE 的 LXC 容器被判成 HP NAS，2026-10-02），判讀時不拿指紋的類別。
    一次查完：大量回報時不可以每台各查一次。"""
    from sqlalchemy import String, or_

    from app.models.virt import VMInterface
    from app.services.arp_evidence import normalize as norm_mac
    ips = {str(it.ip).split("/")[0] for it in items if it.nmap}
    if not ips:
        return set()
    macs = {m for it in items if it.nmap and (m := norm_mac(it.mac))}
    by_mac = {norm_mac(it.mac): str(it.ip).split("/")[0] for it in items if it.nmap and norm_mac(it.mac)}
    host = func.host(VMInterface.primary_ip)
    conds = [in_values(host, ips, type_=String())]
    if macs:
        conds.append(in_values(VMInterface.mac, macs))
    from app.models.virt import VirtualMachine
    from app.services.fw_lookup import PROXMOX_GUEST_OUI
    mac_of = {str(it.ip).split("/")[0]: norm_mac(it.mac) for it in items if it.nmap}
    out: dict[str, str] = {}
    rows = (await session.execute(select(host, VMInterface.mac, VirtualMachine.kind)
                                  .join(VirtualMachine, VirtualMachine.id == VMInterface.vm_id)
                                  .where(or_(*conds)))).all()
    for ip, mac, vm_kind in rows:
        kind = "ct" if vm_kind == "ct" else "vm"
        m = norm_mac(mac) if mac else None
        if m and m in by_mac:
            out[by_mac[m]] = kind
        # 只有 IP 對到、兩邊 MAC 都知道卻不同 → DHCP 位址換了主人，不是這台（同 fw_lookup.vm_match_for）
        elif ip and str(ip) in ips and not (m and mac_of.get(str(ip)) and mac_of[str(ip)] != m):
            out.setdefault(str(ip), kind)
    # Proxmox 指派的 MAC（bc:24:11）只會出現在 PVE 的虛擬機／容器上
    for ip, m in mac_of.items():
        if m and m.lower().startswith(PROXMOX_GUEST_OUI):
            out.setdefault(ip, "vm")
    return out


class AgentDHCPServer(StrictModel):
    """網段上回應 DHCPOFFER 的主機（agent 廣播 DHCPDISCOVER 觀測到的）。"""

    subnet_cidr: str
    server_ip: str
    from_ip: str | None = None
    mac: str | None = None
    offered_ip: str | None = None
    router: str | None = None
    netmask: str | None = None
    via_relay: bool = False


class AgentReportIn(StrictModel):
    results: Annotated[list[AgentReportItem], Field(max_length=100_000)]
    dhcp_servers: Annotated[list[AgentDHCPServer], Field(max_length=500)] = []
    # 一輪結束時附上的統計（耗時、逐子網路位址數／在線數、背景待辦量），存到 scan_agents.last_cycle
    cycle: dict[str, Any] | None = None


class DhcpdPool(StrictModel):
    subnet: Annotated[str | None, Field(max_length=64)] = None
    start: Annotated[str, Field(max_length=64)]
    end: Annotated[str, Field(max_length=64)]


class DhcpdHost(StrictModel):
    ip: Annotated[str, Field(max_length=64)]
    mac: Annotated[str | None, Field(max_length=64)] = None
    hostname: Annotated[str | None, Field(max_length=255)] = None
    ends: Annotated[str | None, Field(max_length=64)] = None


class DhcpdFile(StrictModel):
    path: Annotated[str | None, Field(max_length=512)] = None
    ok: bool = False
    error: Annotated[str | None, Field(max_length=512)] = None
    size: int | None = None
    mtime: int | None = None


class DhcpdReportIn(StrictModel):
    """代理讀 dhcpd.conf／dhcpd.leases 的結果（只有解析後的結構化資料，沒有檔案原文）。"""
    source_id: uuid.UUID
    pools: Annotated[list[DhcpdPool], Field(max_length=5000)] = Field(default_factory=list)
    reservations: Annotated[list[DhcpdHost], Field(max_length=20000)] = Field(default_factory=list)
    leases: Annotated[list[DhcpdHost], Field(max_length=50000)] = Field(default_factory=list)
    files: dict[str, DhcpdFile] = Field(default_factory=dict)


@router.post("/dhcpd-report")
async def agent_dhcpd_report(
    payload: DhcpdReportIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """獨立 ISC DHCP Server 的回報（issue #45）。只收指派給這台代理、而且啟用中的來源；
    別的來源一律 404（不透露存不存在）。"""
    from app.models.dhcp_standalone import IscDhcpServer
    from app.services.dhcp_standalone import ingest_isc_report

    agent = await _agent_from_key(session, x_agent_key)
    src = await session.get(IscDhcpServer, payload.source_id)
    if src is None or src.agent_id != agent.id:
        raise HTTPException(404, detail="Not found")
    if not src.enabled:
        return {"status": "disabled"}
    agent.last_seen_at = datetime.now(UTC)
    counts = await ingest_isc_report(session, src, payload.model_dump())
    sid, sname, serr = src.id, src.name, src.last_error   # commit 之後屬性會過期
    await session.commit()
    # 作業頁：每個 ISC 來源一列；檔案讀不到算失敗（原因跟整合頁的最後錯誤一樣）
    from app.services.background_tasks import upsert_scheduled_task
    await upsert_scheduled_task(
        session, kind="isc_dhcp.sync", target_type="isc_dhcp_server", target_id=sid,
        target_label=sname, ok=not serr, error=serr, summary=dict(counts))
    return {"status": "ok", **counts}


class SnmpReportIn(StrictModel):
    """代理的 SNMP 收集結果（只有解析後的列，沒有設備的原始輸出）。

    sections 逐區段：{"status": ok|partial|error|absent, "error", "elapsed_ms", "truncated", "rows": [...]}
    - arp.rows：[[ip, mac, ifIndex, state, type], ...]（state 只有 ipNetToPhysicalTable 有）
    - fdb.rows：[[mac, 埠名, vlan], ...]
    - interfaces.rows：[{if_index, name, descr, alias, type, mac, admin, oper}]；interfaces.addresses：[{ip, if_index}]
    - neighbors.rows：[{protocol, local_port, remote_sysname, remote_port, remote_chassis_id, remote_mac,
      remote_mgmt_address, remote_platform}]
    列數上限在 services/snmp_collect（MAX_ROWS）；這裡不逐列驗證（十萬列的 Pydantic 模型太慢）。
    """
    target_id: uuid.UUID
    job_id: uuid.UUID | None = None
    credential_id: uuid.UUID | None = None
    elapsed_ms: int | None = None
    system: dict[str, Any] | None = None
    error: Annotated[str | None, Field(max_length=1000)] = None
    error_code: Annotated[str | None, Field(max_length=64)] = None
    sections: dict[str, dict[str, Any]] = Field(default_factory=dict)


@router.post("/snmp-report")
async def agent_snmp_report(
    payload: SnmpReportIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """SNMP 收集的回報（0217）。只收**目前判定由這台代理收集**、而且啟用中的目標；別的一律 404（不透露存不存在）。
    比對與寫入全在伺服器（services/snmp_collect）。"""
    from app.models.snmp import SnmpTarget
    from app.services import snmp as snmp_svc
    from app.services.snmp_collect import ingest_report

    agent = await _agent_from_key(session, x_agent_key)
    target = await session.get(SnmpTarget, payload.target_id)
    if target is None:
        raise HTTPException(404, detail="Not found")
    res = await snmp_svc.resolve(session, target)
    if res.agent_id != agent.id:
        raise HTTPException(404, detail="Not found")
    if not target.enabled and payload.job_id is None:
        return {"status": "disabled"}
    # 跟掃描回報排同一個隊（同一台代理）：SNMP 的 ARP 與掃描結果會改到同一批 IP 記錄，交錯時會死結
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": report_lock_key(agent.id)})
    agent.last_seen_at = datetime.now(UTC)
    out = await ingest_report(session, agent, target, payload.model_dump(mode="json"))
    await session.commit()
    return {"status": "ok", "result": out["result"],
            "sections": {k: {kk: v.get(kk) for kk in ("status", "rows", "matched", "new", "removed")}
                         for k, v in out["sections"].items()}}


def report_lock_key(agent_id: Any) -> str:
    """同一台代理的回報在交易內排隊用的 advisory lock 鍵（見 agent_report）。"""
    return f"scan_agent_report:{agent_id}"


@router.post("/report")
async def agent_report(
    payload: AgentReportIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
) -> dict[str, int]:
    """Agent push 掃描結果：對有回應的 IP stamp last_seen_scanner（+補 MAC）。"""
    import ipaddress as _ipaddr
    agent = await _agent_from_key(session, x_agent_key)
    # 同一台代理的回報排隊處理：代理會同時送逐子網路回報與背景結果（逾時也會重送），兩筆交易涵蓋同一批 IP、
    # 卻在不同階段取得列鎖（ORM flush 一批、重算主機名稱再一批），交錯時互相死結（prod 兩週 28 次 500）。
    # 交易層的鎖，commit／rollback 時自動放開；不同代理不受影響
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": report_lock_key(agent.id)})
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
    skipped_not_in_ipam = 0
    # 沒開自動收錄時掃到的未登錄活位址：不建 IP 記錄，只記「看到過」（指示計顯示「未納管」、未授權 IP 偵測也看得到）
    unmanaged: dict[tuple[Any, str], dict[str, Any]] = {}
    skipped_no_subnet = 0
    from app.services.hostname_reports import HostnameRun
    hn_runs = {src: HostnameRun(session, source=src, origin=f"{src}:{agent.id}", peers=2)
               for src in ("scanner", "netbios", "mdns")}
    recog_matcher: Any = _UNSET            # 第一筆需要判讀時才載入（多數回報沒有 OS 偵測結果）
    vm_guests: dict[str, str] | None = None   # 虛擬機（"vm"）／容器（"ct"）的位址（同樣第一次需要時整批查）
    # 沒有指派任何子網路的代理不能碰任何位址。以前這裡是「有指派才過濾」，沒指派的代理因此
    # 跳過整道過濾、可以改全站任何一筆 IP 的 MAC／OS／主機名稱（2026-10-09 合規核對抓到）
    results = payload.results if agent_subnet_ids else []
    if not agent_subnet_ids:
        skipped_no_subnet = sum(1 for it in payload.results if it.alive)
    for item in results:
        if not item.alive:
            continue
        stmt = select(IPAddress).where(IPAddress.ip == item.ip,
                                       in_values(IPAddress.subnet_id, agent_subnet_ids))
        # 重疊網段下可能有多筆同 IP；限定 agent 子網路後通常唯一，取第一筆
        ipa = (await session.execute(stmt.limit(1))).scalar_one_or_none()
        if ipa is None and not item.liveness:
            continue            # 背景探測補的資料只補既有的 IP，不當作「發現新主機」
        if ipa is None:
            if not agent.auto_create_ips:
                # 沒開自動收錄 → 不建紀錄，但記下「看到過」（unmanaged_sightings）：指示計上顯示「未納管」，
                # 不再跟閒置一模一樣；「未授權 IP」偵測也讀得到。只記指派給這個代理、有開掃描的子網路內的位址。
                skipped_not_in_ipam += 1
                try:
                    aip_u = _ipaddr.ip_address(str(item.ip).split("/")[0])
                except ValueError:
                    continue
                sub_u = next((sid for net, sid in addable_nets if aip_u in net), None)
                if sub_u is not None:
                    unmanaged[(sub_u, str(aip_u))] = {
                        "mac": (item.mac or None) and str(item.mac)[:17],
                        "hostname": ((item.netbios or item.mdns or item.rdns) or None)
                        and str(item.netbios or item.mdns or item.rdns)[:255],
                    }
                continue
            # 掃描代理發現的新 IP → 自動加進它所屬（有開掃描）的子網路
            try:
                aip = _ipaddr.ip_address(str(item.ip).split("/")[0])
            except ValueError:
                continue
            sub_id = next((sid for net, sid in addable_nets if aip in net), None)
            if sub_id is None:
                # 開了自動收錄，卻沒有任何「指派給這個代理且有開掃描」的子網路包含它。
                # 這裡原本是靜靜 continue —— 使用者只會看到「掃了但什麼都沒發生」，
                # 而真正的原因（子網路沒指派給代理）畫面上完全沒有線索。
                skipped_no_subnet += 1
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
        elif item.liveness:
            ipa.last_seen_scanner = now
            # 掃描代理看到回應＝即時上線證據，立刻更新實際狀態（不必等 LibreNMS sync）
            from app.services.librenms import mark_scanner_seen
            await mark_scanner_seen(session, ipa, now)
        if item.mac:
            from app.services.arp_evidence import record_arp_observation
            from app.services.arp_precedence import consider_mac
            await consider_mac(session, ip=ipa, mac=item.mac, source="scanner")
            # IP 衝突偵測的依據：不管上面有沒有覆寫 IP 記錄上的 MAC 都要記
            # （被優先序擋下來的那個 MAC 正是衝突的另一方，issue #41）
            await record_arp_observation(session, ip=ipa, mac=item.mac, source="scanner",
                                         seen_at=now)
        # OS 偵測：有 nmap 結構化結果（代理 1.14.0 起）就用 IP 探測同一套判讀（含 Recog）推 OS、
        # 設備類型與廠牌型號；沒有就照舊存代理自己推的那行 OS（前端依 family 配 icon）
        if item.nmap or item.os_guess:
            from app.services.device_identity import apply_summary
            summary: dict[str, Any] = {}
            if item.nmap:
                from app.services.ip_identify import summarize
                if recog_matcher is _UNSET:
                    from app.services.recog import get_matcher
                    recog_matcher = await get_matcher(session)
                if vm_guests is None:
                    vm_guests = await _virtual_guests(session, payload.results)
                # 廠商用 IP 記錄上的 MAC 查 jt-ipam 自己的 OUI 表，與「探測」頁同一套。nmap 報的是當下回應 ARP 的
                # 那張網卡：雙網卡主機的兩個網段在同一個廣播網域時，Linux 會用任何一張網卡回答本機任何一個位址
                # （arp_ignore=0），SuperMicro 主機的位址曾由它的 HP 擴充網卡回答，畫面於是寫著「伺服器 · HP」
                from app.services.oui import vendor_for_mac
                vendor_mac = str(ipa.mac or item.mac) if (ipa.mac or item.mac) else None
                summary = summarize({"nmap": {"available": True, **item.nmap}}, recog=recog_matcher,
                                    mac_vendor=await vendor_for_mac(session, vendor_mac),
                                    virtual_guest=vm_guests.get(str(item.ip).split("/")[0]), mac=vendor_mac)
            await apply_summary(session, ipa, summary, fallback_os=item.os_guess)
        # 主機名稱觀測 → 走既有來源優先序（各來源獨立一筆，不會 thrash）。
        # rDNS 記 source=scanner；NetBIOS / mDNS 各自獨立來源，方便在優先序頁分別排序/停用。
        # 經 HostnameRun（逐代理記錄）：以前的 tiebreak_min 會讓改名成字典序較大的名字永遠不生效。
        # 只報「有名字」的：沒有名字可能只是那個探測這輪沒跑、或對方沒回應，不代表名字消失了。
        # 例外是反解：代理 1.8.1 起在 DNS 明確回答「沒有這筆 PTR」時送空字串（逾時、DNS 連不上
        # 仍然不送），那才是「名字真的沒了」→ 清掉這台代理先前回報的（以前永遠留著）
        for _src, _val in (("scanner", item.rdns), ("netbios", item.netbios), ("mdns", item.mdns)):
            if _val:
                hn_runs[_src].report(ipa, _val)
            elif _src == "scanner" and _val == "" and "rdns" in (item.probes_run or []):
                hn_runs[_src].report(ipa, None)
        # 記各 probe 上次執行時間（給「下次到期」顯示）
        if item.probes_run:
            lr = dict(ipa.probe_last_run or {})
            for p in scan_probes.normalize_probes(item.probes_run):
                lr[p] = now.isoformat()
            ipa.probe_last_run = lr
        updated += 1
    # 代理只回報活著的主機、而且是逐台回報，沒有「完整一輪」可言 → 不清（complete=False）
    for _run in hn_runs.values():
        await _run.finish(complete=False)
    dhcp_seen = await _record_dhcp_sightings(
        session, agent, agent_subnets, payload.dhcp_servers, now)

    if payload.cycle is not None:
        cyc = dict(payload.cycle)
        if isinstance(cyc.get("subnets"), list):
            cyc["subnets"] = cyc["subnets"][:1000]
        agent.last_cycle = {**cyc, "at": now.isoformat()}
        # 記下這一輪、評估負載；太重時通知管理員（開始與恢復各一次，見 services/scan_load）
        from app.services.scan_load import record as record_cycle
        await record_cycle(session, agent, cyc, now)
    if unmanaged:
        from app.services.unmanaged import record_sightings
        await record_sightings(session, source="scanner", source_id=agent.id, now=now, sightings=unmanaged)
    agent.last_seen_at = now
    agent.last_error = None
    await session.commit()
    # created / skipped_not_in_ipam 都要回報：使用者才看得出「掃到但沒收錄」有幾個，
    # 而不是以為掃描器什麼都沒發現（自動收錄預設關閉，這個數字通常就是差額）
    return {"received": len(payload.results), "updated": updated,
            "created": created, "skipped_not_in_ipam": skipped_not_in_ipam,
            "skipped_no_subnet": skipped_no_subnet,
            "dhcp_servers": dhcp_seen}


async def _record_dhcp_sightings(
    session: AsyncSession, agent: ScanAgent, agent_subnets: list[Any],
    servers: list[AgentDHCPServer], now: datetime,
) -> int:
    """把觀測到的 DHCP 伺服器記下來（同網段同伺服器只留一列，重複觀測更新時間）。

    這裡**不判斷合法與否** —— 那是拿 `ip_addresses.is_dhcp_server` 即時比對出來的。
    存成欄位的話，之後把某台標記成合法，舊記錄仍然寫著「非法」。
    """
    if not servers:
        return 0
    from app.models.dhcp_sighting import DHCPSighting

    # 只認指派給這個 agent 的子網路：agent 說它掃了哪個網段，不代表那網段歸它管
    by_cidr = {str(s.cidr): s.id for s in agent_subnets}
    seen = 0
    for srv in servers:
        subnet_id = by_cidr.get(srv.subnet_cidr)
        if subnet_id is None:
            continue
        row = (await session.execute(
            select(DHCPSighting).where(
                DHCPSighting.subnet_id == subnet_id,
                DHCPSighting.server_ip == srv.server_ip,
            ).limit(1)
        )).scalar_one_or_none()
        if row is None:
            row = DHCPSighting(subnet_id=subnet_id, server_ip=srv.server_ip,
                               first_seen_at=now)
            session.add(row)
        row.server_mac = srv.mac or row.server_mac
        row.offered_ip = srv.offered_ip
        row.router = srv.router
        row.via_relay = srv.via_relay
        row.agent_id = agent.id
        row.last_seen_at = now
        seen += 1
    return seen


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
    # 它寫進共用表的主機名稱／租約／固定分配／NAT／VPN 通道一併收回（沒有外鍵會跟著刪）
    from app.services.integration_cleanup import forget_instance
    await forget_instance(session, source="scanner", source_id=obj.id,
                          hostname_sources=("scanner", "netbios", "mdns"))
    await session.delete(obj)
    await session.commit()

@router.get("/{agent_id}/load", dependencies=[Depends(require_admin)])
async def agent_load(
    agent_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """負載面板：最近一輪的逐子網路細節、評估與建議，以及最近幾輪的耗時（趨勢）。"""
    from app.services import scan_load

    obj = await session.get(ScanAgent, agent_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Scan agent not found")
    rows = await scan_load.recent(session, obj.id)
    hist = [{"at": r.at, "duration_s": r.duration_s, "interval_s": r.interval_s,
             "heavy_backlog": r.heavy_backlog, "hosts": r.hosts, "alive": r.alive} for r in rows]
    from app.services.system_config import get_liveness_config
    online = int((await get_liveness_config(session))["minutes"])
    ev = scan_load.evaluate(obj.last_cycle, hist, online_minutes=online) if obj.last_cycle else None
    if ev is not None:
        # 帶上子網路 id（依 CIDR 對這台代理被指派的子網路），面板上才能直接「移到別的代理」
        ids = {str(c): i for i, c in (await session.execute(
            select(Subnet.id, Subnet.cidr).where(Subnet.scan_agent_id == obj.id))).all()}
        for sub in ev["subnets"]:
            sid = ids.get(str(sub.get("cidr")))
            sub["subnet_id"] = str(sid) if sid else None
    return {"agent_id": str(obj.id), "last_cycle": obj.last_cycle, "evaluation": ev, "history": hist}


# ─────────────────── 工具探測工作（代理端；X-Agent-Key 驗證）───────────────────
@router.get("/jobs", include_in_schema=False)
async def agent_take_jobs(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
    wait: int = Query(0, ge=0, le=25),
) -> dict[str, Any]:
    """代理領取待辦探測（長輪詢）。

    `wait` 秒內若沒有待辦就回空陣列。長輪詢讓「使用者按下按鈕」到「代理開始跑」只差不到一秒，
    卻不需要任何入站連線 —— 代理仍然只由內往外連。
    """
    from app.services.agent_probe import claim_jobs

    agent = await _agent_from_key(session, x_agent_key)
    deadline = time.monotonic() + wait
    # 代理已經斷線（重啟、網路中斷）就不要再替它領：否則工作被領走後回給一條死掉的連線，就此遺失
    # （代理重啟後的第一次主控台中繼因此乾等 15 秒）。
    # 不能用 request.is_disconnected()：它用預先取消的 scope 探測，在 BaseHTTPMiddleware（這裡有三層）底下
    # 永遠回 False（2026-10-02 實機抓到）。改由背景工作真的等 receive() 的斷線訊號。
    hung_up = asyncio.create_task(_wait_disconnect(request)) if wait else None
    try:
        while True:
            if hung_up is not None and hung_up.done():
                return {"jobs": []}
            jobs = await claim_jobs(session, agent_id=agent.id)
            if jobs and hung_up is not None and hung_up.done():
                await session.rollback()        # 領的當下才斷線：放回去，留給重新連上來的代理
                return {"jobs": []}
            out_jobs = await _expand_jobs(session, jobs) if jobs else []
            await session.commit()
            if out_jobs:
                return {"jobs": out_jobs}
            if time.monotonic() >= deadline:
                return {"jobs": []}
            await asyncio.sleep(1.0)
    finally:
        if hung_up is not None:
            hung_up.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await hung_up


async def _expand_jobs(session: AsyncSession, jobs: list[Any]) -> list[dict[str, Any]]:
    """領走的工作 → 給代理的內容。SNMP 的測試／立即收集在這裡才展開成目標＋解密後的憑證
    （資料庫裡的工作記錄只有 id）；目標已經不在、或已經不歸這台代理收 → 工作作廢、不交出去。"""
    from app.models.agent_probe_job import STATUS_FAILED
    from app.services import snmp as snmp_svc

    out: list[dict[str, Any]] = []
    for j in jobs:
        params = j.params
        if j.kind in snmp_svc.SNMP_JOB_KINDS:
            params = await snmp_svc.job_params(session, j)
            if params is None:
                j.status = STATUS_FAILED
                j.finished_at = datetime.now(UTC)
                j.error = "收集目標已不存在或已改由其他代理收集"
                from app.services.snmp_collect import on_job_finished
                await on_job_finished(session, j)
                continue
        out.append({"id": str(j.id), "kind": j.kind, "params": params})
    return out


async def _wait_disconnect(request: Request) -> None:
    """等到用戶端斷線才結束（第一則是請求本體，之後只會有斷線訊號）。"""
    while (await request.receive()).get("type") != "http.disconnect":
        pass


class _JobResultIn(StrictModel):
    result: Any = None
    error: str | None = None


class _JobProgressIn(StrictModel):
    progress: dict[str, Any]


@router.post("/jobs/{job_id}/progress", include_in_schema=False)
async def agent_job_progress(
    job_id: uuid.UUID, payload: _JobProgressIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """代理回報執行中的進度（IP「探測」頁顯示現在在做什麼）。只收自己領到、還在跑的工作。"""
    import json as _json

    from app.services.agent_probe import MAX_PROGRESS_BYTES, update_progress

    agent = await _agent_from_key(session, x_agent_key)
    if len(_json.dumps(payload.progress, ensure_ascii=False)) > MAX_PROGRESS_BYTES:
        raise HTTPException(status_code=413, detail="progress too large")
    ok = await update_progress(session, agent_id=agent.id, job_id=job_id, progress=payload.progress)
    await session.commit()
    if not ok:
        raise HTTPException(status_code=404, detail="job not found or not running")
    return {"ok": True}


@router.post("/jobs/{job_id}/result", include_in_schema=False)
async def agent_job_result(
    job_id: uuid.UUID, payload: _JobResultIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """代理回報結果。只能結束自己領到的工作（服務層會驗 agent_id）。"""
    from app.services.agent_probe import finish_job

    agent = await _agent_from_key(session, x_agent_key)
    ok = await finish_job(session, agent_id=agent.id, job_id=job_id,
                          result=payload.result, error=payload.error)
    await session.commit()
    if not ok:
        raise HTTPException(status_code=404, detail="job not found or already finished")
    return {"ok": True}
