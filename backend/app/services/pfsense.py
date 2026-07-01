"""pfSense 同步服務（Phase 1：DHCP / ARP / 別名）。

走第三方 pfSense-pkg-RESTAPI（pfrest.org）：base path /api/v2、X-API-Key 標頭認證、
回應外層為 {code,status,data,...}（結果在 data）。

重疊網段安全：所有 IP 比對都 scope 到 fw.scope_subnet_ids（留空＝全域）+ .limit(1).first()，
不用 .scalar_one_or_none()（多客戶共用 RFC1918 同 IP 會 MultipleResultsFound 炸整批 sync）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.core.security import decrypt_secret, encrypt_secret
from app.models.address import IPAddress
from app.models.pfsense import PfSenseFirewall, PfSenseSyncedAlias
from app.services.hostname import apply_observation

# pfSense-pkg-RESTAPI v2 端點（如不同版本路徑有異，於此集中調整）
EP_VERSION = "/api/v2/system/version"
EP_DHCP_LEASES = "/api/v2/status/dhcp_server/leases"
EP_ARP_TABLE = "/api/v2/diagnostics/arp_table"
EP_ALIASES = "/api/v2/firewall/aliases"
EP_RULES = "/api/v2/firewall/rules"
EP_NAT_PF = "/api/v2/firewall/nat/port_forwards"
EP_NAT_OUT = "/api/v2/firewall/nat/outbound/mappings"


class PfSenseError(Exception):
    pass


# ─────────────────── 認證 / 請求 ───────────────────
def _aad(fw_id: uuid.UUID) -> bytes:
    return f"pfsense_firewall:{fw_id}:api_key".encode()


def encrypt_api_key(fw_id: uuid.UUID, api_key: str) -> tuple[bytes, bytes]:
    return encrypt_secret(api_key, aad=_aad(fw_id))


def _decrypt_key(fw: PfSenseFirewall) -> str:
    return decrypt_secret(fw.api_key_enc, fw.api_key_nonce, aad=_aad(fw.id)).decode("utf-8")


def _headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key, "Accept": "application/json"}


async def _api_get(fw: PfSenseFirewall, path: str, *, timeout: float = 15.0) -> Any:
    url = f"{fw.api_url.rstrip('/')}{path}"
    key = _decrypt_key(fw)
    try:
        resp = await safe_request(
            "GET", url, headers=_headers(key), timeout=timeout, verify=fw.verify_tls,
        )
    except UnsafeOutboundURL as exc:
        raise PfSenseError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise PfSenseError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise PfSenseError(f"pfSense GET {path}: {resp.status_code} {resp.text[:200]}")
    body = resp.json()
    if isinstance(body, dict) and "data" in body:  # pfrest 外層 envelope
        return body["data"]
    return body


async def test_connection(fw: PfSenseFirewall) -> dict[str, Any]:
    """驗證 API key + 連線；回傳 pfSense 版本資訊（若有）。"""
    data = await _api_get(fw, EP_VERSION, timeout=10.0)
    return data if isinstance(data, dict) else {"raw": data}


# ─────────────────── IP stamp（重疊網段安全）───────────────────
async def _stamp_ip_seen(
    session: AsyncSession, ip: str, *, mac: str | None = None, hostname: str | None = None,
    subnet_ids: list[uuid.UUID] | None = None, dhcp: bool = False,
) -> bool:
    """找到 jt-ipam IPAddress 就 stamp last_seen_scanner（pfSense 證據等同 scanner）。"""
    ip = _valid_ip(ip)
    if ip is None:
        return False
    stmt = select(IPAddress).where(IPAddress.ip == ip)
    if subnet_ids:
        stmt = stmt.where(IPAddress.subnet_id.in_(subnet_ids))
    ipa = (await session.execute(stmt.limit(1))).scalars().first()
    if ipa is None:
        return False
    ipa.last_seen_scanner = datetime.now(UTC)
    if dhcp:
        ipa.in_dhcp_lease = True
    if mac:
        from app.services.arp_precedence import consider_mac
        await consider_mac(session, ip=ipa, mac=mac, source="pfsense")
    if hostname:
        await apply_observation(session, ip=ipa, source="pfsense", hostname=hostname)
    return True


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _ip_of(d: dict[str, Any]) -> Any:
    return _first(d, "ip_address", "ip", "address")


def _as_text(v: object) -> str | None:
    """pfSense 某些欄位（如別名的 detail）會回 list；存進 Text 欄位前先攤平成字串。"""
    if v is None:
        return None
    if isinstance(v, list):
        return "; ".join(str(x) for x in v if x not in (None, "")) or None
    return str(v)


def _valid_ip(v: object) -> str | None:
    """回傳去掉首碼後的合法 IP 字串；不是 IP（例如別名名稱 Web_Test）就回 None。"""
    import ipaddress
    if not v:
        return None
    s = str(v).split("/")[0].strip()
    try:
        ipaddress.ip_address(s)
    except ValueError:
        return None
    return s


def _mac_of(d: dict[str, Any]) -> Any:
    return _first(d, "mac_address", "mac", "mac_addr")


def _host_of(d: dict[str, Any]) -> str | None:
    h = _first(d, "hostname", "host", "client-hostname")
    h = str(h).strip() if h else ""
    return h or None if h not in ("?", "-") else None


# ─────────────────── 同步：DHCP / ARP / 別名 ───────────────────
async def sync_dhcp_leases(session: AsyncSession, fw: PfSenseFirewall) -> int:
    rows = await _api_get(fw, EP_DHCP_LEASES)
    if not isinstance(rows, list):
        return 0
    scope_ids = list(fw.scope_subnet_ids) if fw.scope_subnet_ids else None
    seen = 0
    leased_ips: set[str] = set()
    for d in rows:
        if not isinstance(d, dict):
            continue
        ip = _ip_of(d)
        if not ip:
            continue
        ipstr = str(ip).split("/")[0]
        leased_ips.add(ipstr)
        if await _stamp_ip_seen(session, ipstr, mac=_mac_of(d), hostname=_host_of(d),
                                subnet_ids=scope_ids, dhcp=True):
            seen += 1
    # 撤銷：scope 內原本標 in_dhcp_lease、但這次租約已消失的 IP（只在有設 scope 時做，
    # 避免多台 pfSense/OPNsense 全域範圍互相清掉對方的標記）。
    if scope_ids:
        stmt = sa_update(IPAddress).where(
            IPAddress.subnet_id.in_(scope_ids), IPAddress.in_dhcp_lease.is_(True))
        if leased_ips:
            stmt = stmt.where(func.host(IPAddress.ip).notin_(leased_ips))
        await session.execute(stmt.values(in_dhcp_lease=False))
    return seen


async def sync_arp_table(session: AsyncSession, fw: PfSenseFirewall) -> int:
    rows = await _api_get(fw, EP_ARP_TABLE)
    if not isinstance(rows, list):
        return 0
    scope_ids = list(fw.scope_subnet_ids) if fw.scope_subnet_ids else None
    seen = 0
    for d in rows:
        if not isinstance(d, dict):
            continue
        ip = _ip_of(d)
        if not ip:
            continue
        if await _stamp_ip_seen(session, str(ip).split("/")[0], mac=_mac_of(d),
                                hostname=_host_of(d), subnet_ids=scope_ids):
            seen += 1
    return seen


async def sync_aliases(session: AsyncSession, fw: PfSenseFirewall) -> int:
    rows = await _api_get(fw, EP_ALIASES)
    if not isinstance(rows, list):
        return 0
    await session.execute(
        delete(PfSenseSyncedAlias).where(PfSenseSyncedAlias.firewall_id == fw.id))
    now = datetime.now(UTC)
    n = 0
    for d in rows:
        if not isinstance(d, dict):
            continue
        name = _first(d, "name")
        if not name:
            continue
        addr = _first(d, "address", "content", "members")
        if isinstance(addr, str):
            members = [x for x in addr.replace(",", " ").split() if x]
        elif isinstance(addr, list):
            members = addr
        else:
            members = []
        session.add(PfSenseSyncedAlias(
            firewall_id=fw.id, name=str(name)[:128],
            alias_type=str(_first(d, "type") or "")[:32] or None,
            members=members, descr=_as_text(_first(d, "descr", "detail")), last_sync_at=now,
        ))
        n += 1
    return n


async def sync_rules(session: AsyncSession, fw: PfSenseFirewall) -> int:
    """同步防火牆規則為精簡清單存進 fw.rules（檢視 + tracker→descr DSV）。"""
    rows = await _api_get(fw, EP_RULES)
    if not isinstance(rows, list):
        fw.rules = []
        return 0
    compact: list[dict[str, Any]] = []
    for d in rows:
        if not isinstance(d, dict):
            continue
        iface = d.get("interface")
        compact.append({
            "tracker": d.get("tracker"),
            "type": d.get("type"),
            "interface": ",".join(iface) if isinstance(iface, list) else (iface or ""),
            "protocol": d.get("protocol"),
            "source": d.get("source"),
            "destination": d.get("destination"),
            "destination_port": d.get("destination_port"),
            "descr": _as_text(d.get("descr")) or "",
            "disabled": bool(d.get("disabled")),
        })
    fw.rules = compact
    return len(compact)


async def fetch_nat(fw: PfSenseFirewall) -> dict[str, list]:
    """即時抓 NAT（檢視用，不儲存）：port forward + outbound mappings。"""
    pf = await _api_get(fw, EP_NAT_PF)
    out = await _api_get(fw, EP_NAT_OUT)
    return {
        "port_forwards": pf if isinstance(pf, list) else [],
        "outbound": out if isinstance(out, list) else [],
    }


def _to_port(v: object) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(str(v).split("-")[0].split(":")[-1].strip())
    except (ValueError, TypeError):
        return None


async def sync_nat(session: AsyncSession, fw: PfSenseFirewall) -> int:
    """同步 pfSense NAT port forward → nat_translations（source_origin=pfsense:<id>）。

    與 OPNsense 並列出現在「NAT 規則」頁，可用「來源＝pfSense」篩選。delete+reinsert 該防火牆範圍，
    不動其他來源。欄位防禦式解析（pfSense-pkg-RESTAPI 各版本欄名略有差異）。
    """
    from sqlalchemy import delete as _delete

    from app.models.nat import NATTranslation
    origin = f"pfsense:{fw.id}"
    await session.execute(_delete(NATTranslation).where(NATTranslation.source_origin == origin))
    rows = await _api_get(fw, EP_NAT_PF)
    if not isinstance(rows, list):
        return 0
    # 欄名以 pfSense-pkg-RESTAPI 實機回應為準（已對照）：interface / protocol / source /
    # source_port / destination / destination_port / target（內部 IP）/ local_port / disabled / descr。
    scope_ids = list(fw.scope_subnet_ids) if fw.scope_subnet_ids else None
    n = 0
    for d in rows:
        if not isinstance(d, dict):
            continue
        # target（轉發到的內部 IP）→ 連到 jt-ipam IPAddress（scope + limit(1) 防重疊網段）
        # target 可能是別名（如 Web_Test）而非實際 IP → 只在是合法 IP 時才解析，否則留 None
        target_ip = _valid_ip(_first(d, "target", "local_ip"))
        dst_ip_id = None
        if target_ip:
            stmt = select(IPAddress.id).where(IPAddress.ip == target_ip)
            if scope_ids:
                stmt = stmt.where(IPAddress.subnet_id.in_(scope_ids))
            dst_ip_id = (await session.execute(stmt.limit(1))).scalars().first()
        session.add(NATTranslation(
            name=str(_first(d, "descr", "name") or "port forward")[:200],
            type="port_forward",
            protocol=str(d.get("protocol") or "any")[:8],
            src_interface=(str(d.get("interface"))[:64] if d.get("interface") else None),
            dst_ip_id=dst_ip_id,
            dst_port=_to_port(_first(d, "destination_port", "local_port", "dst_port")),
            src_port=_to_port(_first(d, "source_port", "src_port")),
            description=_as_text(_first(d, "descr", "description")),
            source_origin=origin,
            disabled=bool(d.get("disabled")),
        ))
        n += 1
    return n


async def sync_instance(session: AsyncSession, fw: PfSenseFirewall) -> dict[str, int]:
    """跑此實例所有啟用的同步；設定 last_sync_at / last_error。"""
    counts: dict[str, int] = {}
    if fw.sync_dhcp:
        counts["dhcp"] = await sync_dhcp_leases(session, fw)
    if fw.sync_arp:
        counts["arp"] = await sync_arp_table(session, fw)
    if fw.sync_aliases:
        counts["aliases"] = await sync_aliases(session, fw)
    if fw.sync_rules:
        counts["rules"] = await sync_rules(session, fw)
        counts["nat"] = await sync_nat(session, fw)
    fw.last_sync_at = datetime.now(UTC)
    fw.last_error = None
    return counts
