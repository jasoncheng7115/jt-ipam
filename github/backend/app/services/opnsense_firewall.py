"""OPNsense Firewall API client + alias 同步邏輯。

OPNsense API 文件：https://docs.opnsense.org/development/api.html
Firewall alias：https://docs.opnsense.org/development/api/core/firewall.html#aliases

主要 endpoints：
  GET  /api/firewall/alias/get                  讀全部 alias / 設定
  GET  /api/firewall/alias/getItem/{uuid}       讀單筆
  POST /api/firewall/alias/addItem              新增
  POST /api/firewall/alias/setItem/{uuid}       修改
  POST /api/firewall/alias/delItem/{uuid}       刪除
  POST /api/firewall/alias/reconfigure          套用變更
  POST /api/firewall/alias_util/list/{name}     看 alias 解析後的成員（runtime view）

OWASP：
- A02：API key/secret 雙欄 AES-GCM，aad 綁 instance id
- A05：所有對外請求走 safe_http；timeout 必填
- A09：每次同步寫 audit
"""

from __future__ import annotations

import base64
import ipaddress
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.core.security import decrypt_secret, encrypt_secret
from app.models.address import IPAddress
from app.models.firewall import OPNsenseAliasMapping, OPNsenseFirewall
from app.models.subnet import Subnet
from app.services.hostname import apply_observation


class OPNsenseError(RuntimeError):
    pass


# ─────────────────── 加解密 ───────────────────


def _aad_key(instance_id) -> bytes:  # type: ignore[no-untyped-def]
    return f"opnsense_firewall:{instance_id}:api_key".encode()


def _aad_secret(instance_id) -> bytes:  # type: ignore[no-untyped-def]
    return f"opnsense_firewall:{instance_id}:api_secret".encode()


def encrypt_credentials(
    instance_id: uuid.UUID, api_key: str, api_secret: str,
) -> dict[str, bytes]:
    k_ct, k_nc = encrypt_secret(api_key, aad=_aad_key(instance_id))
    s_ct, s_nc = encrypt_secret(api_secret, aad=_aad_secret(instance_id))
    return {
        "api_key_enc": k_ct, "api_key_nonce": k_nc,
        "api_secret_enc": s_ct, "api_secret_nonce": s_nc,
    }


def _decrypt_creds(fw: OPNsenseFirewall) -> tuple[str, str]:
    key = decrypt_secret(fw.api_key_enc, fw.api_key_nonce, aad=_aad_key(fw.id)).decode("utf-8")
    secret = decrypt_secret(
        fw.api_secret_enc, fw.api_secret_nonce, aad=_aad_secret(fw.id)
    ).decode("utf-8")
    return key, secret


# ─────────────────── 低階 HTTP ───────────────────


def _basic_auth_header(api_key: str, api_secret: str) -> dict[str, str]:
    token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}", "Accept": "application/json"}


async def _api_get(fw: OPNsenseFirewall, path: str, *, timeout: float = 15.0) -> dict[str, Any]:
    url = f"{fw.api_url.rstrip('/')}{path}"
    key, secret = _decrypt_creds(fw)
    try:
        resp = await safe_request(
            "GET", url, headers=_basic_auth_header(key, secret),
            timeout=timeout, verify=fw.verify_tls,
        )
    except UnsafeOutboundURL as exc:
        raise OPNsenseError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise OPNsenseError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise OPNsenseError(f"OPNsense GET {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json()  # type: ignore[no-any-return]


async def _iface_name_map(fw: OPNsenseFirewall) -> dict[str, str]:
    """OPNsense 介面內部識別碼(identifier) → 顯示名稱(description)。

    例：opt2→WAN2、wan→WAN、opt1→wgopnsitea。資料取自 interfaces/overview/export
    （每筆含 identifier + description）；老版本/不可用時回空 dict（沿用原始 key）。
    """
    out: dict[str, str] = {}
    try:
        data = await _api_get(fw, "/api/interfaces/overview/export")
    except OPNsenseError:
        data = None
    if isinstance(data, list):
        for it in data:
            if not isinstance(it, dict):
                continue
            ident = str(it.get("identifier") or "").strip()
            descr = str(it.get("description") or "").strip()
            if ident and descr and descr != "Unassigned Interface":
                out[ident] = descr
    # 補上 export 沒列出的群組介面（如 wireguard 群組）：getInterfaceNames {key: descr}
    try:
        names = await _api_get(fw, "/api/diagnostics/interface/getInterfaceNames")
    except OPNsenseError:
        names = None
    if isinstance(names, dict):
        for k, v in names.items():
            if k not in out and isinstance(v, str) and v.strip():
                out[str(k)] = v.strip()
    return out


def _resolve_iface(raw: str | None, ifmap: dict[str, str]) -> str | None:
    """把規則的 interface 內部 key（可能逗號分隔多個）對應成顯示名稱。"""
    if not raw:
        return raw
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return raw
    return ", ".join(ifmap.get(p, p) for p in parts)


async def _api_post(
    fw: OPNsenseFirewall, path: str, body: dict[str, Any] | None = None, *, timeout: float = 15.0,
) -> dict[str, Any]:
    url = f"{fw.api_url.rstrip('/')}{path}"
    key, secret = _decrypt_creds(fw)
    try:
        resp = await safe_request(
            "POST", url,
            headers={**_basic_auth_header(key, secret), "Content-Type": "application/json"},
            json=body or {}, timeout=timeout, verify=fw.verify_tls,
        )
    except UnsafeOutboundURL as exc:
        raise OPNsenseError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise OPNsenseError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code not in (200, 201):
        raise OPNsenseError(f"OPNsense POST {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json()  # type: ignore[no-any-return]


# ─────────────────── 高階 API ───────────────────


async def healthcheck(fw: OPNsenseFirewall) -> dict[str, Any]:
    return await _api_get(fw, "/api/firewall/alias/get", timeout=8.0)


async def list_aliases(fw: OPNsenseFirewall) -> list[dict[str, Any]]:
    """讀 OPNsense 上所有 alias（attribute view）。"""
    data = await _api_get(fw, "/api/firewall/alias/get")
    aliases_obj = data.get("alias", {}).get("aliases", {}).get("alias", {})
    out: list[dict[str, Any]] = []
    if isinstance(aliases_obj, dict):
        for uuid_, info in aliases_obj.items():
            row = {"uuid": uuid_}
            row.update(info if isinstance(info, dict) else {})
            out.append(row)
    return out


async def find_alias_uuid(fw: OPNsenseFirewall, name: str) -> str | None:
    for a in await list_aliases(fw):
        if a.get("name") == name:
            return str(a["uuid"])
    return None


async def list_alias_members(fw: OPNsenseFirewall, alias_name: str) -> list[str]:
    """OPNsense alias 解析後的成員列表（runtime）。"""
    data = await _api_post(fw, f"/api/firewall/alias_util/list/{alias_name}")
    rows = data.get("rows") or []
    return [str(r.get("ip") or r.get("entry") or "") for r in rows if r]


async def upsert_alias(
    fw: OPNsenseFirewall, *, name: str, alias_type: str, content: list[str],
    description: str | None = None,
) -> str:
    """
    新增或更新 alias，回傳 uuid。OPNsense alias.content 是 \\n 分隔字串。
    """
    body = {
        "alias": {
            "name": name,
            "type": alias_type,
            "description": description or "managed by jt-ipam",
            "content": "\n".join(content),
            "enabled": "1",
        }
    }
    existing_uuid = await find_alias_uuid(fw, name)
    if existing_uuid:
        await _api_post(fw, f"/api/firewall/alias/setItem/{existing_uuid}", body)
        await _api_post(fw, "/api/firewall/alias/reconfigure")
        return existing_uuid

    resp = await _api_post(fw, "/api/firewall/alias/addItem", body)
    new_uuid = str(resp.get("uuid") or "")
    if not new_uuid:
        raise OPNsenseError(f"addItem returned no uuid: {resp}")
    await _api_post(fw, "/api/firewall/alias/reconfigure")
    return new_uuid


async def delete_alias(fw: OPNsenseFirewall, name: str) -> bool:
    uuid_ = await find_alias_uuid(fw, name)
    if not uuid_:
        return False
    await _api_post(fw, f"/api/firewall/alias/delItem/{uuid_}")
    await _api_post(fw, "/api/firewall/alias/reconfigure")
    return True


# ─────────────────── 同步邏輯 ───────────────────


async def _resolve_selector_ips(
    session: AsyncSession, selector: dict[str, Any],
) -> list[str]:
    """根據 selector 抓出要送進 alias 的 IP 列表。"""
    sel_type = selector.get("type")
    stmt = None
    if sel_type == "subnet":
        sub_id = selector.get("subnet_id")
        if not sub_id:
            return []
        stmt = select(IPAddress.ip).where(IPAddress.subnet_id == sub_id)
    elif sel_type == "section":
        sec_id = selector.get("section_id")
        if not sec_id:
            return []
        stmt = (
            select(IPAddress.ip)
            .join(Subnet, Subnet.id == IPAddress.subnet_id)
            .where(Subnet.section_id == sec_id)
        )
    elif sel_type == "tag":
        # IPAddress.tags 是 ARRAY(String)（可能無；schema 沒有就忽略）
        # 退而求其次：用 custom_fields["tag"] 比對
        stmt = select(IPAddress.ip).where(
            IPAddress.custom_fields["tag"].astext == selector.get("tag")
        )
    elif sel_type == "custom_field":
        field = selector.get("field")
        value = selector.get("value")
        if not field or value is None:
            return []
        stmt = select(IPAddress.ip).where(
            IPAddress.custom_fields[field].astext == str(value)
        )
    else:
        raise OPNsenseError(f"unsupported selector type: {sel_type}")

    rows = (await session.execute(stmt)).scalars().all()
    out: list[str] = []
    for r in rows:
        # asyncpg 給 inet 物件，str() 會帶 prefix；alias 要單一 IP
        s = str(r).split("/", 1)[0]
        try:
            ipaddress.ip_address(s)
        except ValueError:
            continue
        out.append(s)
    # 去重 + 排序穩定 diff
    return sorted(set(out))


async def sync_mapping(
    session: AsyncSession, mapping: OPNsenseAliasMapping,
) -> dict[str, Any]:
    """執行單一 mapping 的同步；回傳 summary。"""
    fw = (
        await session.execute(
            select(OPNsenseFirewall).where(OPNsenseFirewall.id == mapping.firewall_id)
        )
    ).scalar_one()
    if not fw.enabled:
        raise OPNsenseError(f"firewall {fw.name!r} disabled")

    summary: dict[str, Any] = {"alias": mapping.alias_name, "direction": mapping.direction}

    try:
        if mapping.direction in ("push", "both"):
            ips = await _resolve_selector_ips(session, mapping.selector)
            uuid_ = await upsert_alias(
                fw, name=mapping.alias_name, alias_type=mapping.alias_type,
                content=ips, description=f"jt-ipam → {mapping.alias_name}",
            )
            mapping.last_alias_uuid = uuid_
            mapping.last_synced_count = len(ips)
            summary["pushed"] = len(ips)
        if mapping.direction in ("pull", "both"):
            members = await list_alias_members(fw, mapping.alias_name)
            summary["pulled"] = len(members)
            summary["pull_sample"] = members[:5]
        mapping.last_sync_at = datetime.now(UTC)
        mapping.last_error = None
    except OPNsenseError as exc:
        mapping.last_error = str(exc)
        raise

    return summary


async def _stamp_ip_seen(
    session: AsyncSession, ip: str,
    *, mac: str | None = None, hostname: str | None = None,
) -> bool:
    """找到 jt-ipam IPAddress 就 stamp last_seen_scanner，回傳是否找到。

    OPNsense 給的資料相當於我們自己 scanner 的證據，所以塞 last_seen_scanner。
    DHCP/ARP 是當下事實 → 有 MAC / hostname 就覆寫舊值。
    """
    if not ip:
        return False
    ipa = (
        await session.execute(select(IPAddress).where(IPAddress.ip == ip))
    ).scalar_one_or_none()
    if ipa is None:
        return False
    ipa.last_seen_scanner = datetime.now(UTC)
    if mac:
        from app.services.arp_precedence import consider_mac
        await consider_mac(session, ip=ipa, mac=mac, source="opnsense")
    if hostname:
        await apply_observation(session, ip=ipa, source="opnsense", hostname=hostname)
    return True


async def sync_dhcp_ranges(
    session: AsyncSession, fw: OPNsenseFirewall,
) -> dict[str, int]:
    """從 DHCP server 取得「發放範圍（pool）」並鏡像進 dhcp_pool_ranges（多段都抓）。

    目前支援 Kea（/api/kea/dhcpv{4,6}/searchSubnet），每個 subnet 的 `pools` 欄位是
    換行（或逗號）分隔的 `from-to`。ISC dhcpd 未提供對應 API → 該防火牆無範圍可同步。
    """
    from app.models.dhcp import DHCPPoolRange

    now = datetime.now(UTC)
    parsed: list[tuple[str, str, str, int]] = []
    for path, family in (("/api/kea/dhcpv4/searchSubnet", 4), ("/api/kea/dhcpv6/searchSubnet", 6)):
        try:
            data = await _api_get(fw, path, timeout=8.0)
        except OPNsenseError:
            continue
        for row in (data.get("rows") or []):
            cidr = row.get("subnet")
            pools = row.get("pools") or ""
            if not cidr or not pools:
                continue
            for line in str(pools).replace(",", "\n").splitlines():
                line = line.strip()
                if not line or "-" not in line:
                    continue
                a, _, b = line.partition("-")
                a, b = a.strip(), b.strip()
                if a and b:
                    parsed.append((str(cidr), a, b, family))

    # 鏡像同步：清掉此防火牆既有範圍後重建
    await session.execute(delete(DHCPPoolRange).where(DHCPPoolRange.firewall_id == fw.id))
    for cidr, a, b, fam in parsed:
        session.add(DHCPPoolRange(
            firewall_id=fw.id, subnet_cidr=cidr, start_ip=a, end_ip=b,
            family=fam, source="kea", synced_at=now,
        ))
    return {"ranges": len(parsed)}


async def sync_dhcp_leases(
    session: AsyncSession, fw: OPNsenseFirewall,
) -> dict[str, int]:
    """拉 OPNsense DHCP leases（多路 try：Kea v4/v6 → ISC v4/v6 → dnsmasq）。

    OPNsense 內建 ISC 已被官方標停，新版改用 Kea；社群也有人用 dnsmasq。
    """
    # (path, kind) — 依序試；每個成功的都會 merge 進來
    sources = [
        # Kea: 24.7+ 提供 leases4/leases6
        ("/api/kea/leases4/search", "kea4"),
        ("/api/kea/leases6/search", "kea6"),
        # ISC dhcpd (legacy)
        ("/api/dhcpv4/leases/searchLease", "isc4"),
        ("/api/dhcpv6/leases/searchLease", "isc6"),
        # dnsmasq plugin
        ("/api/dnsmasq/leases/search", "dnsmasq"),
    ]

    seen = matched = 0
    used_sources: list[str] = []
    errors: list[str] = []

    for path, kind in sources:
        try:
            data = await _api_get(fw, path)
        except OPNsenseError as exc:
            # 404 / endpoint not found 是正常 (那台沒裝該 plugin)，記下但不擋
            if "404" not in str(exc):
                errors.append(f"{path}: {str(exc)[:80]}")
            continue
        rows = (data or {}).get("rows") or (data or {}).get("leases") or []
        if not rows:
            continue
        used_sources.append(kind)
        for r in rows:
            ip = (r.get("address") or r.get("ip") or "").strip()
            mac = (r.get("hwaddr") or r.get("mac") or "").strip() or None
            host = (
                r.get("hostname") or r.get("client-hostname") or r.get("client_hostname")
                or ""
            ).strip() or None
            if not ip:
                continue
            seen += 1
            if await _stamp_ip_seen(session, ip, mac=mac, hostname=host):
                matched += 1

    out: dict[str, int] = {"seen": seen, "matched": matched}
    if used_sources:
        out["sources"] = ",".join(used_sources)  # type: ignore[assignment]
    if errors:
        out["endpoint_errors"] = errors  # type: ignore[assignment]
    return out


async def sync_arp_table(
    session: AsyncSession, fw: OPNsenseFirewall,
) -> dict[str, int]:
    """拉 OPNsense ARP table。

    Endpoint: GET /api/diagnostics/interface/searchArp（OPNsense ≥ 22）；
    舊版可能是 /api/diagnostics/interface/getArp。
    """
    try:
        data = await _api_get(fw, "/api/diagnostics/interface/searchArp")
    except OPNsenseError:
        data = await _api_get(fw, "/api/diagnostics/interface/getArp")
    rows = data.get("rows") or data if isinstance(data, list) else data.get("rows") or []
    if isinstance(data, list):
        rows = data
    seen = matched = 0
    for r in rows:
        ip = (r.get("ip") or "").strip()
        mac = (r.get("mac") or "").strip() or None
        if not ip:
            continue
        seen += 1
        if await _stamp_ip_seen(session, ip, mac=mac):
            matched += 1
    return {"seen": seen, "matched": matched}


async def _fetch_legacy_nat_rules(fw: OPNsenseFirewall) -> list[dict[str, Any]]:
    """OPNsense 的 legacy NAT (Firewall: NAT: Port Forward) 沒 REST API；
    只能下載 config.xml 直接 parse <nat><rule>。

    回傳 dict 列表，欄位對齊 new-API（uuid / description / protocol /
    target / target_port / destination_port / interface）。
    """
    import xml.etree.ElementTree as ET

    from defusedxml.ElementTree import fromstring as _safe_xml_fromstring

    url = f"{fw.api_url.rstrip('/')}/api/core/backup/download/this"
    key, secret = _decrypt_creds(fw)
    try:
        resp = await safe_request(
            "GET", url, headers=_basic_auth_header(key, secret),
            timeout=30.0, verify=fw.verify_tls,
        )
    except (UnsafeOutboundURL, httpx.HTTPError) as exc:
        raise OPNsenseError(f"legacy config download: {exc}") from exc
    if resp.status_code != 200 or "<opnsense>" not in resp.text:
        raise OPNsenseError(
            f"legacy config download: HTTP {resp.status_code}, "
            f"len={len(resp.text)}"
        )

    try:
        # defusedxml：擋 XXE / billion-laughs（外部 entity / DTD）
        root = _safe_xml_fromstring(resp.text)
    except ET.ParseError as exc:
        raise OPNsenseError(f"config.xml parse error: {exc}") from exc

    out: list[dict[str, Any]] = []
    nat_node = root.find("nat")
    if nat_node is None:
        return out

    def _text(n: ET.Element | None) -> str | None:
        if n is None:
            return None
        t = (n.text or "").strip()
        return t or None

    for idx, rule in enumerate(nat_node.findall("rule")):
        # 不再跳過 disabled rule —— 改用 disabled 欄位呈現（legacy 把 disabled 當 element 存在）
        rule_id = _text(rule.find("associated-rule-id")) or f"legacy-{idx}"
        dest = rule.find("destination")
        src = rule.find("source")
        out.append({
            "uuid": rule_id,
            "description": _text(rule.find("descr")),
            "protocol": _text(rule.find("protocol")) or "any",
            "ipprotocol": _text(rule.find("ipprotocol")),
            "target": _text(rule.find("target")),
            "target_port": _text(rule.find("local-port")),
            "destination_port": _text(dest.find("port")) if dest is not None else None,
            "destination_net": (_text(dest.find("network")) or _text(dest.find("address"))
                                if dest is not None else None),
            "destination_not": (dest.find("not") is not None) if dest is not None else False,
            "source_port": _text(src.find("port")) if src is not None else None,
            "source_net": (_text(src.find("network")) or _text(src.find("address"))
                           if src is not None else None),
            "source_not": (src.find("not") is not None) if src is not None else False,
            "interface": _text(rule.find("interface")),
            "disabled": rule.find("disabled") is not None,
            "nordr": rule.find("nordr") is not None,
            "log": rule.find("log") is not None,
            "category": _text(rule.find("category")),
            "natreflection": _text(rule.find("natreflection")),
            "poolopts": _text(rule.find("poolopts")),
            "filter_rule": _text(rule.find("associated-rule-id")),
            "_legacy": True,
        })
    return out


async def sync_nat_rules(
    session: AsyncSession, fw: OPNsenseFirewall,
) -> dict[str, int]:
    """從 OPNsense 拉 NAT 規則進 jt-ipam nat_translations 表。

    優先用 core REST API（一條條 endpoint 試）：
      - /api/firewall/d_nat/searchRule        — Port Forward (新 Automation 引擎；舊版 OPNsense 404)
      - /api/firewall/one_to_one/searchRule   — 1:1 NAT
      - /api/firewall/source_nat/searchRule   — Outbound NAT

    對舊版 OPNsense / 仍在 legacy Firewall: NAT: Port Forward 的環境，
    legacy 沒 REST API，改下載 config.xml parse `<nat><rule>` 補上 port_forward。

    `source_origin = "opnsense:<fw_id>"`、`external_id = OPNsense rule uuid 或
    legacy associated-rule-id` 為唯一鍵。
    OPNsense 端被刪的 rule → jt-ipam 這邊也刪（鏡像）。
    """
    import ipaddress as _ip

    from sqlalchemy import func

    from app.models.address import IPAddress
    from app.models.nat import NATTranslation

    origin = f"opnsense:{fw.id}"

    # 此防火牆對應的 device：NAT 規則的 dst_ip 沒掛 device 時，fallback 用防火牆本身的
    # device，讓「裝置」欄位至少指到這條規則所在的防火牆。
    # 解析順序：API host IP 對到的 IPAddress.device → 名為該 IP 的 device → 名為防火牆名稱的 device。
    from urllib.parse import urlsplit

    from app.models.device import Device

    _fw_host = (urlsplit(fw.api_url).hostname or "").strip()
    fw_device_id = None
    if _fw_host:
        fw_device_id = (
            await session.execute(
                select(IPAddress.device_id)
                .where(func.host(IPAddress.ip) == _fw_host, IPAddress.device_id.isnot(None))
                .limit(1)
            )
        ).scalar_one_or_none()
        if not fw_device_id:
            fw_device_id = (
                await session.execute(
                    select(Device.id).where(func.lower(Device.name) == _fw_host.lower()).limit(1)
                )
            ).scalar_one_or_none()
    if not fw_device_id:
        fw_device_id = (
            await session.execute(
                select(Device.id).where(func.lower(Device.name) == fw.name.lower()).limit(1)
            )
        ).scalar_one_or_none()

    endpoints = [
        ("/api/firewall/d_nat/searchRule", "port_forward"),
        ("/api/firewall/one_to_one/searchRule", "one_to_one"),
        ("/api/firewall/source_nat/searchRule", "many_to_one"),
    ]

    # IP 文字 → jt-ipam ip uuid 的小 cache
    _ip_cache: dict[str, str | None] = {}

    async def _resolve_ip_id(ip_text: str | None) -> str | None:
        if not ip_text:
            return None
        s = ip_text.strip()
        # 排除 alias / 字串（"wanip", "allowlist_xxx"）
        try:
            addr = _ip.ip_address(s)
        except ValueError:
            return None
        key = str(addr)
        if key in _ip_cache:
            return _ip_cache[key]
        row = (
            await session.execute(
                select(IPAddress.id).where(func.host(IPAddress.ip) == key).limit(1)
            )
        ).scalar_one_or_none()
        _ip_cache[key] = str(row) if row else None
        return _ip_cache[key]

    nat_ifmap = await _iface_name_map(fw)   # opt2→WAN2 等介面顯示名
    seen_uuids: set[str] = set()
    inserted = updated = 0
    errors: list[str] = []
    legacy_fallback_used = False

    def _port(v) -> int | None:  # type: ignore[no-untyped-def]
        try:
            n = int(str(v).strip())
            return n if 1 <= n <= 65535 else None
        except (TypeError, ValueError):
            return None

    async def _upsert(r: dict[str, Any], default_type: str) -> None:
        nonlocal inserted, updated
        ruuid = (r.get("uuid") or "").strip()
        if not ruuid:
            return
        seen_uuids.add(ruuid)

        proto = (r.get("protocol") or "any").strip().lower()
        if proto not in ("tcp", "udp", "any", "icmp", "esp", "gre", "tcp/udp"):
            proto = "any"

        def _is_ipish(s: object) -> bool:
            s = str(s or "")
            return bool(s) and any(c.isdigit() for c in s) and ("." in s or ":" in s)

        ipv = (r.get("ipprotocol") or "inet").strip().lower()
        if ipv not in ("inet", "inet6"):
            ipv = "inet"
        # 來源 / 目的 / 埠：非數字 / 非 IP 的值 → 視為 alias 名稱（可連到 alias 內容）
        sp_raw = r.get("destination_port") if default_type == "port_forward" else r.get("source_port")
        dp_raw = r.get("target_port")
        src_net = r.get("source_net")
        dst_net = r.get("destination_net")
        tgt = r.get("target")
        extra = {
            "disabled": bool(r.get("disabled")),
            "no_rdr": bool(r.get("nordr")),
            "ip_version": ipv,
            "src_not": bool(r.get("source_not")),
            "dst_not": bool(r.get("destination_not")),
            "log": bool(r.get("log")),
            "category": (str(r.get("category"))[:128] if r.get("category") else None),
            "nat_reflection": (str(r.get("natreflection"))[:16] if r.get("natreflection") else None),
            "pool_options": (str(r.get("poolopts"))[:32] if r.get("poolopts") else None),
            "filter_rule": (str(r.get("filter_rule"))[:128] if r.get("filter_rule") else None),
            "src_port_alias": (str(sp_raw)[:64] if sp_raw and _port(sp_raw) is None else None),
            "dst_port_alias": (str(dp_raw)[:64] if dp_raw and _port(dp_raw) is None else None),
            "src_alias": (str(src_net)[:64] if src_net and not _is_ipish(src_net) and str(src_net).lower() != "any" else None),
            "dst_alias": (str(dst_net)[:64] if dst_net and not _is_ipish(dst_net) and str(dst_net).lower() != "any" else None),
            "redirect_alias": (str(tgt)[:64] if tgt and not _is_ipish(tgt) else None),
        }

        name = (
            r.get("description") or r.get("descr")
            or f"opnsense-{default_type}-{ruuid[:8]}"
        )

        # port 對應：對 port_forward —
        #   src_port = 外部 port (destination.port / destination_port)
        #   dst_port = 內部 port (target_port / local-port)
        # searchRule 各版本 key 名不一：redirect 目的埠可能叫 target_port / local_port / local-port
        ext_port_raw = r.get("destination_port") or r.get("dstport") or r.get("dst_port")
        redir_port_raw = (r.get("target_port") or r.get("local_port")
                          or r.get("local-port") or r.get("localport"))
        src_port = _port(r.get("source_port") or r.get("srcport") or ext_port_raw)
        dst_port = _port(redir_port_raw or ext_port_raw)
        if default_type == "port_forward":
            src_port = _port(ext_port_raw)
            dst_port = _port(redir_port_raw)
            # 反向代理等「外部=內部」port-forward，legacy config 有時只存 local-port；
            # 外部埠沒抓到時退用 redirect 埠，來源埠才不會空白。
            if src_port is None and dst_port is not None:
                src_port = dst_port

        # 目的 IP（port_forward 的 target = 內部 IP）
        dst_ip_id = await _resolve_ip_id(r.get("target"))

        # 從 dst_ip 反查 owner device（IPAddress.device_id）；沒有就 fallback 到防火牆 device
        device_id = None
        if dst_ip_id:
            device_id = (
                await session.execute(
                    select(IPAddress.device_id).where(IPAddress.id == dst_ip_id)
                )
            ).scalar_one_or_none()
        if not device_id:
            device_id = fw_device_id

        existing = (
            await session.execute(
                select(NATTranslation).where(
                    NATTranslation.source_origin == origin,
                    NATTranslation.external_id == ruuid,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            obj = NATTranslation(
                name=name[:200],
                type=default_type,
                protocol=proto,
                src_port=src_port,
                dst_port=dst_port,
                src_interface=_resolve_iface(r.get("interface"), nat_ifmap),
                dst_ip_id=dst_ip_id,
                device_id=device_id,
                description=(r.get("description") or r.get("descr") or None),
                source_origin=origin,
                external_id=ruuid,
                **extra,
            )
            session.add(obj)
            inserted += 1
        else:
            existing.name = name[:200]
            existing.type = default_type
            existing.protocol = proto
            existing.src_port = src_port
            existing.dst_port = dst_port
            existing.src_interface = _resolve_iface(r.get("interface"), nat_ifmap)
            existing.dst_ip_id = dst_ip_id  # type: ignore[assignment]
            existing.device_id = device_id
            existing.description = r.get("description") or r.get("descr") or None
            for _k, _v in extra.items():
                setattr(existing, _k, _v)
            updated += 1

    for path, default_type in endpoints:
        try:
            data = await _api_post(fw, path, {"current": 1, "rowCount": 1000})
        except OPNsenseError as exc:
            msg = str(exc)
            errors.append(f"{path}: {msg[:80]}")
            # d_nat 404 → 用 legacy XML fallback 補 port_forward
            if default_type == "port_forward" and "404" in msg:
                try:
                    legacy_rules = await _fetch_legacy_nat_rules(fw)
                    legacy_fallback_used = True
                    for r in legacy_rules:
                        await _upsert(r, "port_forward")
                except OPNsenseError as exc2:
                    errors.append(f"legacy fallback: {str(exc2)[:80]}")
            continue
        for r in (data or {}).get("rows") or []:
            await _upsert(r, default_type)

    # 刪掉已不存在的（鏡像同步）
    removed = 0
    all_for_fw = (
        await session.execute(
            select(NATTranslation).where(NATTranslation.source_origin == origin)
        )
    ).scalars().all()
    for obj in all_for_fw:
        if obj.external_id not in seen_uuids:
            await session.delete(obj)
            removed += 1

    out: dict[str, int] = {
        "seen": len(seen_uuids),
        "inserted": inserted,
        "updated": updated,
        "removed": removed,
    }
    if legacy_fallback_used:
        out["legacy_xml_fallback"] = 1
    if errors:
        out["endpoint_errors"] = errors  # type: ignore[assignment]
    return out


async def sync_filter_rules(
    session: AsyncSession, fw: OPNsenseFirewall,
) -> dict[str, int]:
    """拉 OPNsense filter rules（防火牆規則）。

    Endpoint: POST /api/firewall/filter/searchRule（OPNsense ≥ 22.7 firewall plugin）。
    回傳每條 rule 的關鍵欄位 + 原始 raw 存 jsonb。對既有 rule 用 (firewall_id, uuid)
    upsert，沒在這次回傳的 rule 視為被刪 → 從 jt-ipam 那邊清掉（保持鏡像一致）。
    """
    from datetime import UTC
    from datetime import datetime as dt

    from app.models.firewall_rule import OPNsenseRule

    try:
        data = await _api_post(fw, "/api/firewall/filter/searchRule",
                                {"current": 1, "rowCount": 1000})
    except OPNsenseError:
        # 老版本端點可能在 firewall_filter 而非 filter；先 fallback 試
        try:
            data = await _api_get(fw, "/api/firewall/filter/get")
        except OPNsenseError as exc:
            return {"seen": 0, "inserted": 0, "updated": 0, "removed": 0,
                    "error": str(exc)[:120]}  # type: ignore[dict-item]

    rows: list[dict[str, Any]] = []
    if isinstance(data, dict):
        rows = data.get("rows") or []
        # /filter/get 那個型態是巢狀 {filter: {rules: {...}}}，盡量解
        if not rows and isinstance(data.get("filter"), dict):
            rule_obj = data["filter"].get("rules") or {}
            if isinstance(rule_obj, dict):
                rows = [{"uuid": uid, **v} for uid, v in rule_obj.items()
                        if isinstance(v, dict)]

    now = dt.now(UTC)
    seen_uuids: set[str] = set()
    inserted = updated = 0
    ifmap = await _iface_name_map(fw)   # opt2→WAN2、wgX→友善名

    for r in rows:
        ruuid = (r.get("uuid") or "").strip()
        if not ruuid:
            continue
        seen_uuids.add(ruuid)

        # OPNsense 回傳的欄位可能是 "1"/"0" 字串、或 {"value": "..."} 結構
        def _v(field: str, r: Any = r) -> str | None:   # r=r：綁定當前迴圈值（B023）
            x = r.get(field)
            if isinstance(x, dict):
                # selected 結構：{"key": {"value": "...", "selected": 1}, ...} 取 selected=1 的 key
                for k, vv in x.items():
                    if isinstance(vv, dict) and vv.get("selected"):
                        return str(k)
                return None
            if x in (None, ""):
                return None
            return str(x)

        enabled_raw = r.get("enabled")
        enabled = enabled_raw in (1, "1", True, "yes")

        existing = (
            await session.execute(
                select(OPNsenseRule).where(
                    OPNsenseRule.firewall_id == fw.id,
                    OPNsenseRule.legacy_uuid == ruuid,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            obj = OPNsenseRule(
                firewall_id=fw.id,
                legacy_uuid=ruuid,
                enabled=enabled,
                sequence=_int_or_none(r.get("sequence")),
                action=_v("action"),
                interface=_resolve_iface(_v("interface"), ifmap),
                direction=_v("direction"),
                protocol=_v("protocol") or _v("ipprotocol"),
                source_net=_v("source_net") or _v("from") or _v("src"),
                source_port=_v("source_port") or _v("srcport"),
                destination_net=_v("destination_net") or _v("to") or _v("dst"),
                destination_port=_v("destination_port") or _v("dstport"),
                description=_v("description") or _v("descr"),
                raw=r,
                last_synced_at=now,
            )
            session.add(obj)
            inserted += 1
        else:
            existing.enabled = enabled
            existing.sequence = _int_or_none(r.get("sequence"))
            existing.action = _v("action")
            existing.interface = _resolve_iface(_v("interface"), ifmap)
            existing.direction = _v("direction")
            existing.protocol = _v("protocol") or _v("ipprotocol")
            existing.source_net = _v("source_net") or _v("from") or _v("src")
            existing.source_port = _v("source_port") or _v("srcport")
            existing.destination_net = _v("destination_net") or _v("to") or _v("dst")
            existing.destination_port = _v("destination_port") or _v("dstport")
            existing.description = _v("description") or _v("descr")
            existing.raw = r
            existing.last_synced_at = now
            updated += 1

    # 清掉這次沒回傳的（已從 OPNsense 移除）
    removed = 0
    existing_all = (
        await session.execute(
            select(OPNsenseRule).where(OPNsenseRule.firewall_id == fw.id)
        )
    ).scalars().all()
    for obj in existing_all:
        if obj.legacy_uuid not in seen_uuids:
            await session.delete(obj)
            removed += 1

    return {"seen": len(rows), "inserted": inserted, "updated": updated, "removed": removed}


def _int_or_none(v: Any) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


async def sync_openvpn_sessions(
    session: AsyncSession, fw: OPNsenseFirewall,
) -> dict[str, int]:
    """拉 OpenVPN 目前連線 session。

    Endpoint: GET /api/openvpn/service/searchSessions（OPNsense ≥ 23）
    每 session 通常有 `virtual_addr`（tunnel IP）跟 `real_address`。
    """
    try:
        data = await _api_get(fw, "/api/openvpn/service/searchSessions")
    except OPNsenseError:
        return {"seen": 0, "matched": 0, "error": "endpoint not available"}  # type: ignore[dict-item]
    rows = data.get("rows") or []
    seen = matched = 0
    for r in rows:
        # 用 virtual_addr 對到 jt-ipam IPAddress（tunnel 內的 IP）
        ip = (r.get("virtual_addr") or r.get("vpn_ip") or "").strip()
        if not ip:
            continue
        seen += 1
        if await _stamp_ip_seen(session, ip):
            matched += 1
    return {"seen": seen, "matched": matched}


async def _resolve_fw_device_id(session: AsyncSession, fw: OPNsenseFirewall):  # type: ignore[no-untyped-def]
    """防火牆對應的 jt-ipam device：API host IP 對到的 IPAddress.device → 名為該 IP 的
    device → 名為防火牆名稱的 device。"""
    from urllib.parse import urlsplit

    from sqlalchemy import func

    from app.models.address import IPAddress
    from app.models.device import Device

    host = (urlsplit(fw.api_url).hostname or "").strip()
    if host:
        did = (await session.execute(
            select(IPAddress.device_id).where(
                func.host(IPAddress.ip) == host, IPAddress.device_id.isnot(None)).limit(1)
        )).scalar_one_or_none()
        if did:
            return did
        did = (await session.execute(
            select(Device.id).where(func.lower(Device.name) == host.lower()).limit(1)
        )).scalar_one_or_none()
        if did:
            return did
    return (await session.execute(
        select(Device.id).where(func.lower(Device.name) == fw.name.lower()).limit(1)
    )).scalar_one_or_none()


async def sync_vpn_tunnels(
    session: AsyncSession, fw: OPNsenseFirewall,
) -> dict[str, int]:
    """從 OPNsense 拉 site-to-site VPN（WireGuard / IPsec）進 vpn_tunnels。

    a 端 = 此防火牆的 device；b 端為外部站點（b_device_id 留空，b_endpoint = 對端位址），
    拓樸圖會以「遠端站點」節點呈現。以 name 首碼 `<fw.name>/` 為唯一鍵做鏡像同步。
    """
    from urllib.parse import urlsplit

    from app.models.physical import VPNTunnel

    fw_dev = await _resolve_fw_device_id(session, fw)
    host = urlsplit(fw.api_url).hostname or None
    seen: set[str] = set()
    inserted = updated = 0

    def _on(v) -> bool:  # type: ignore[no-untyped-def]
        return str(v).lower() in ("1", "true", "yes", "on")

    async def _upsert(label: str, vtype: str, b_endpoint: str | None, status: str, desc: str,
                      local_pk: str | None = None, peer_pk: str | None = None) -> None:
        nonlocal inserted, updated
        full = f"{fw.name}/{label}"[:128]
        seen.add(full)
        obj = (await session.execute(
            select(VPNTunnel).where(VPNTunnel.name == full)
        )).scalar_one_or_none()
        if obj is None:
            session.add(VPNTunnel(
                name=full, type=vtype, status=status, a_device_id=fw_dev,
                a_endpoint=host, b_endpoint=b_endpoint or None, description=desc or None,
                local_public_key=local_pk or None, peer_public_key=peer_pk or None,
            ))
            inserted += 1
        else:
            obj.type, obj.status = vtype, status
            obj.a_device_id, obj.a_endpoint = fw_dev, host
            obj.b_endpoint, obj.description = (b_endpoint or None), (desc or None)
            obj.local_public_key = local_pk or None
            obj.peer_public_key = peer_pk or None
            updated += 1

    def _pk(d: dict[str, Any]) -> str:  # 容錯不同欄位命名
        for k in ("pubkey", "publickey", "public_key", "pubKey"):
            v = (d.get(k) or "").strip()
            if v:
                return v
        return ""

    # ── WireGuard：先抓本地 server（instance）公鑰，client = 對端 peer ──
    try:
        # 本地 instance 公鑰；單一 server 時直接當 local_public_key，
        # 多 server 時用 peers(含 client uuid) 對應到正確的 server。
        servers: list[dict[str, Any]] = []
        try:
            sdata = await _api_get(fw, "/api/wireguard/server/searchServer")
            servers = list(sdata.get("rows") or [])
        except OPNsenseError:
            servers = []
        local_pks = [_pk(s) for s in servers if _pk(s)]
        single_local_pk = local_pks[0] if len(local_pks) == 1 else None

        def _server_pk_for_client(client_uuid: str) -> str | None:
            if single_local_pk:
                return single_local_pk
            for s in servers:
                peers = s.get("peers") or s.get("clients") or ""
                if client_uuid and client_uuid in str(peers):
                    return _pk(s) or None
            return single_local_pk

        data = await _api_get(fw, "/api/wireguard/client/searchClient")
        for r in (data.get("rows") or []):
            nm = (r.get("name") or str(r.get("uuid", ""))[:8]).strip()
            remote = (r.get("serveraddress") or r.get("endpoint") or "").strip()
            allowed = (r.get("tunneladdress") or "").strip()
            peer_pk = _pk(r)                                   # 對端公鑰
            local_pk = _server_pk_for_client(str(r.get("uuid", "")))  # 本地公鑰
            await _upsert(f"wg/{nm}", "wireguard", remote,
                          "active" if _on(r.get("enabled")) else "offline",
                          f"allowed-ips: {allowed}" if allowed else "",
                          local_pk=local_pk, peer_pk=peer_pk)
    except OPNsenseError:
        pass

    # ── IPsec connections（swanctl 新版 API）──
    try:
        data = await _api_post(fw, "/api/ipsec/connections/search_connection",
                               {"current": 1, "rowCount": 500})
        for r in (data.get("rows") or []):
            nm = (r.get("description") or r.get("name") or str(r.get("uuid", ""))[:8]).strip()
            remote = (r.get("remote_addrs") or r.get("remote_addr") or "").strip()
            await _upsert(f"ipsec/{nm}", "ipsec_ikev2", remote,
                          "active" if _on(r.get("enabled")) else "offline", "")
    except OPNsenseError:
        pass

    # 鏡像刪除：此防火牆來源、這次沒看到的
    existing = (await session.execute(
        select(VPNTunnel).where(VPNTunnel.name.like(f"{fw.name}/%"))
    )).scalars().all()
    removed = 0
    for o in existing:
        if o.name not in seen:
            await session.delete(o)
            removed += 1

    await session.flush()
    paired = await link_wireguard_peers(session)
    paired += await link_ipsec_peers(session)
    return {"seen": len(seen), "inserted": inserted, "updated": updated,
            "removed": removed, "paired": paired}


async def link_wireguard_peers(session: AsyncSession) -> int:
    """跨防火牆偵測 WireGuard 對接：A.peer_public_key == B.local_public_key
    ⟹ A 的對端就是 B 的防火牆 device，設 A.b_device_id = B.a_device_id。

    雙向都成立時兩條 tunnel 互指對方 device，拓樸圖即可把兩台連起來
    （重複邊由 topology 以 device-pair 去重）。回傳本次新建立的對接數。
    """
    from app.models.physical import VPNTunnel

    tunnels = list((await session.execute(
        select(VPNTunnel).where(VPNTunnel.type == "wireguard")
    )).scalars().all())

    # local_public_key → (tunnel 的 a_device_id) + 反向對應整條 tunnel（拿對端記錄的我方 WAN）
    by_local: dict[str, uuid.UUID] = {}
    by_local_tunnel: dict[str, Any] = {}
    for t in tunnels:
        if t.local_public_key and t.a_device_id:
            by_local.setdefault(t.local_public_key, t.a_device_id)
            by_local_tunnel.setdefault(t.local_public_key, t)

    linked = 0
    for t in tunnels:
        if not t.peer_public_key:
            continue
        peer_dev = by_local.get(t.peer_public_key)
        # 不要連到自己（同台 fw 的 server/client）
        if peer_dev and peer_dev != t.a_device_id:
            if t.b_device_id != peer_dev:
                t.b_device_id = peer_dev
                t.pairing_method = "wireguard_pubkey"   # 公鑰配對 → 可靠
                linked += 1
            # 本端 a_endpoint 常是 LAN/管理 IP；對端 tunnel 記錄的 b_endpoint
            # 正是「對端看到的我方位址」＝我方 WAN 公網 IP → 拿來補正
            recip = by_local_tunnel.get(t.peer_public_key)
            if recip is not None and recip.b_endpoint and t.a_endpoint != recip.b_endpoint:
                t.a_endpoint = recip.b_endpoint
        elif peer_dev is None and t.b_device_id is not None:
            # 對端 fw 已不再宣告此公鑰 → 還原成遠端站點節點
            t.b_device_id = None
            t.pairing_method = None
            linked += 1
    return linked


async def link_ipsec_peers(session: AsyncSession) -> int:
    """偵測 IPsec site-to-site 對接（best-effort，用端點位址比對）：
    若某條 IPsec tunnel 的對端閘道位址（b_endpoint）正好等於另一台已知防火牆的位址，
    就把 b_device_id 指到那台防火牆。WireGuard 有公鑰可信賴；IPsec 沒有，
    故只在「remote 位址精準命中另一台 fw 位址」時才連，降低誤判。回傳新連數。
    """
    from urllib.parse import urlsplit

    from app.models.firewall import OPNsenseFirewall
    from app.models.physical import VPNTunnel

    fws = list((await session.execute(select(OPNsenseFirewall))).scalars().all())
    # 位址 → 防火牆 device_id。多訊號彙整以提高命中率：
    #   1) 防火牆 API host（可能是 mgmt 或 WAN）
    #   2) 各防火牆自己 VPN tunnel 的本地端點 a_endpoint（通常正是該台 WAN/閘道）
    addr_to_dev: dict[str, uuid.UUID] = {}
    for fw in fws:
        dev = await _resolve_fw_device_id(session, fw)
        host = (urlsplit(fw.api_url).hostname or "").strip().lower()
        if dev and host:
            addr_to_dev.setdefault(host, dev)

    all_tunnels = list((await session.execute(select(VPNTunnel))).scalars().all())
    for t in all_tunnels:
        if t.a_device_id and t.a_endpoint:
            for a in t.a_endpoint.replace(";", ",").split(","):
                a = a.strip().lower()
                if a:
                    addr_to_dev.setdefault(a, t.a_device_id)

    tunnels = [t for t in all_tunnels if t.type in ("ipsec_ikev1", "ipsec_ikev2")]

    linked = 0
    for t in tunnels:
        # b_endpoint 可能是 "1.2.3.4" 或 "1.2.3.4,5.6.7.8"
        remotes = [a.strip().lower() for a in (t.b_endpoint or "").replace(";", ",").split(",") if a.strip()]
        peer_dev = next((addr_to_dev[a] for a in remotes if a in addr_to_dev and addr_to_dev[a] != t.a_device_id), None)
        if peer_dev:
            if t.b_device_id != peer_dev:
                t.b_device_id = peer_dev
                t.pairing_method = "ipsec_endpoint"   # 端點比對 → best-effort（無加密身分）
                linked += 1
        elif t.b_device_id is not None:
            # 之前連的對端位址已不再對應任何防火牆 → 還原
            t.b_device_id = None
            t.pairing_method = None
            linked += 1
    return linked


def _opn_selected(v: Any) -> str:
    """OPNsense get-view 的 select 欄位 → 取被選中的 key（逗號分隔）；純量原樣回字串。"""
    if isinstance(v, dict):
        sel = [k for k, o in v.items()
               if isinstance(o, dict) and str(o.get("selected")) in ("1", "true", "True")]
        return ",".join(sel)
    return "" if v is None else str(v)


def _opn_members(content: Any) -> list[str]:
    """OPNsense alias content → 成員字串列表。"""
    if isinstance(content, dict):
        sel = [k for k, o in content.items()
               if isinstance(o, dict) and str(o.get("selected")) in ("1", "true", "True")]
        return sel or list(content.keys())
    if isinstance(content, str):
        return [x.strip() for x in content.replace(",", "\n").split("\n") if x.strip()]
    if isinstance(content, list):
        return [str(x) for x in content]
    return []


async def sync_aliases(session: AsyncSession, fw: OPNsenseFirewall) -> dict[str, Any]:
    """把 OPNsense 上的 alias 定義拉回 jt-ipam（唯讀檢視用）。"""
    from app.models.firewall import OPNsenseSyncedAlias

    aliases = await list_aliases(fw)
    now = datetime.now(UTC)
    existing = {
        a.name: a for a in (await session.execute(
            select(OPNsenseSyncedAlias).where(OPNsenseSyncedAlias.firewall_id == fw.id)
        )).scalars().all()
    }
    seen: set[str] = set()
    for a in aliases:
        raw_name = a.get("name")
        name = raw_name if isinstance(raw_name, str) and raw_name else _opn_selected(raw_name)
        if not name:
            continue
        seen.add(name)
        desc = a.get("description")
        desc = desc if isinstance(desc, str) else _opn_selected(desc)
        en_raw = a.get("enabled")
        en = _opn_selected(en_raw) if isinstance(en_raw, dict) else str(en_raw if en_raw is not None else "1")
        members = _opn_members(a.get("content"))
        row = existing.get(name)
        if row is None:
            row = OPNsenseSyncedAlias(firewall_id=fw.id, name=name)
            session.add(row)
        row.alias_type = (_opn_selected(a.get("type")) or None)
        row.description = desc or None
        row.enabled = en in ("1", "true", "True")
        row.content = members
        row.member_count = len(members)
        row.opn_uuid = str(a.get("uuid")) if a.get("uuid") else None
        row.last_synced_at = now
    for name, row in existing.items():
        if name not in seen:
            await session.delete(row)
    return {"count": len(seen)}


async def sync_all_for_firewall(
    session: AsyncSession, fw: OPNsenseFirewall,
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(OPNsenseAliasMapping).where(OPNsenseAliasMapping.firewall_id == fw.id)
        )
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for m in rows:
        try:
            out.append(await sync_mapping(session, m))
        except OPNsenseError as exc:
            out.append({
                "alias": m.alias_name, "error": str(exc), "direction": m.direction,
            })

    # 擴展同步（DHCP / ARP / OpenVPN）— 各自獨立 try，失敗只記錄不中斷
    if fw.sync_dhcp:
        try:
            out.append({"task": "dhcp", **(await sync_dhcp_leases(session, fw))})
        except OPNsenseError as exc:
            out.append({"task": "dhcp", "error": str(exc)})
    if fw.sync_arp:
        try:
            out.append({"task": "arp", **(await sync_arp_table(session, fw))})
        except OPNsenseError as exc:
            out.append({"task": "arp", "error": str(exc)})
    if fw.sync_openvpn:
        try:
            out.append({"task": "openvpn", **(await sync_openvpn_sessions(session, fw))})
        except OPNsenseError as exc:
            out.append({"task": "openvpn", "error": str(exc)})
    if fw.sync_rules:
        try:
            out.append({"task": "rules", **(await sync_filter_rules(session, fw))})
        except OPNsenseError as exc:
            out.append({"task": "rules", "error": str(exc)})
    if fw.sync_nat:
        try:
            out.append({"task": "nat", **(await sync_nat_rules(session, fw))})
        except OPNsenseError as exc:
            out.append({"task": "nat", "error": str(exc)})
    # alias 定義（唯讀檢視）；可由 sync_aliases 開關控制；失敗只記錄
    if fw.sync_aliases:
        try:
            out.append({"task": "aliases", **(await sync_aliases(session, fw))})
        except OPNsenseError as exc:
            out.append({"task": "aliases", "error": str(exc)})
    # DHCP 發放範圍（Kea pools，多段都抓）；失敗只記錄
    try:
        out.append({"task": "dhcp_ranges", **(await sync_dhcp_ranges(session, fw))})
    except OPNsenseError as exc:
        out.append({"task": "dhcp_ranges", "error": str(exc)})
    # VPN（site-to-site WireGuard / IPsec）一律嘗試；外掛 / API 不存在會被容錯吞掉
    try:
        out.append({"task": "vpn", **(await sync_vpn_tunnels(session, fw))})
    except OPNsenseError as exc:
        out.append({"task": "vpn", "error": str(exc)})

    fw.last_sync_at = datetime.now(UTC)
    fw.last_error = None
    return out
