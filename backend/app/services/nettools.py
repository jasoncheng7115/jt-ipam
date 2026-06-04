"""網路工具純運算邏輯（IP / CIDR / MAC / FQDN / DNS / 電力）。

同一套實作同時給：
- HTTP 端點 `app/api/v1/endpoints/tools.py`（前端「網路工具」頁）
- MCP / AI chat 工具 `app/mcp/tools.py`

設計原則：
- 純函式、無 DB（geoip 例外，需憑證 → 留在呼叫端帶 session）
- 輸入一律經 stdlib `ipaddress` / 正規表示式驗證（OWASP A03）
- 失敗時 raise `NetToolError`（ValueError 子類）；呼叫端各自翻成 HTTP 400 或工具錯誤
- 回傳皆為 JSON-serialisable dict（大整數一律轉 str，避免 JS 精度問題）
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from typing import Any

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$|^[0-9A-Fa-f]{12}$")
_FQDN_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddr = ipaddress.IPv4Address | ipaddress.IPv6Address


class NetToolError(ValueError):
    """輸入不合法 / 無法計算（人讀訊息）。"""


# ─────────────────── 解析 helper ───────────────────
def parse_net(cidr: str) -> IPNetwork:
    try:
        return ipaddress.ip_network(cidr, strict=False)
    except ValueError as exc:
        raise NetToolError(f"Invalid CIDR: {exc}") from exc


def parse_addr(ip: str) -> IPAddr:
    try:
        return ipaddress.ip_address(ip)
    except ValueError as exc:
        raise NetToolError(f"Invalid IP: {exc}") from exc


def normalise_mac(mac: str) -> str:
    """轉成連續 12 位 hex（小寫）。"""
    cleaned = mac.replace(":", "").replace("-", "").replace(".", "").lower()
    if len(cleaned) != 12 or not all(c in "0123456789abcdef" for c in cleaned):
        raise NetToolError(f"Invalid MAC: {mac}")
    return cleaned


# ─────────────────── IP / CIDR ───────────────────
def ip_info(ip: str) -> dict[str, Any]:
    addr = parse_addr(ip)
    binary = format(int(addr), "032b") if isinstance(addr, ipaddress.IPv4Address) else None
    return {
        "ip": str(addr),
        "version": addr.version,
        "is_private": addr.is_private,
        "is_global": addr.is_global,
        "is_reserved": addr.is_reserved,
        "is_multicast": addr.is_multicast,
        "is_loopback": addr.is_loopback,
        "is_link_local": addr.is_link_local,
        "decimal": str(int(addr)),
        "hex": "0x" + format(int(addr), "x"),
        "reverse_pointer": addr.reverse_pointer,
        "binary": binary,
    }


def cidr_info(cidr: str) -> dict[str, Any]:
    net = parse_net(cidr)
    is_v4 = isinstance(net, ipaddress.IPv4Network)
    first_host: str | None
    last_host: str | None
    if is_v4:
        if net.prefixlen >= 31:
            host_count = net.num_addresses
            first_host = str(net.network_address)
            last_host = str(net.broadcast_address)
        else:
            host_count = net.num_addresses - 2
            first_host = str(net.network_address + 1)
            last_host = str(net.broadcast_address - 1)
        broadcast: str | None = str(net.broadcast_address)
    else:
        host_count = net.num_addresses
        first_host = str(net.network_address) if net.num_addresses > 0 else None
        last_host = str(net.broadcast_address) if net.num_addresses > 0 else None
        broadcast = None
    return {
        "cidr": str(net),
        "version": net.version,
        "network_address": str(net.network_address),
        "broadcast_address": broadcast,
        "netmask": str(net.netmask),
        "hostmask": str(net.hostmask),
        "prefixlen": net.prefixlen,
        "num_addresses": str(net.num_addresses),
        "host_count": str(host_count),
        "first_host": first_host,
        "last_host": last_host,
        "is_private": net.is_private,
    }


def cidr_split(cidr: str, new_prefix: int) -> dict[str, Any]:
    net = parse_net(cidr)
    if new_prefix < net.prefixlen:
        raise NetToolError(f"new_prefix {new_prefix} must be >= existing /{net.prefixlen}")
    if (net.version == 4 and new_prefix > 32) or (net.version == 6 and new_prefix > 128):
        raise NetToolError("prefix out of range for this address family")
    bits = new_prefix - net.prefixlen
    if bits > 16:  # A04：阻擋過大切割（避免 OOM）
        raise NetToolError(f"refusing to split into {1 << bits} subnets; bits delta must be <= 16")
    subs = [str(s) for s in net.subnets(new_prefix=new_prefix)]
    return {"cidr": str(net), "new_prefix": new_prefix, "subnets": subs, "count": len(subs)}


def eui64(mac: str, prefix: str) -> dict[str, Any]:
    """從 MAC 與 IPv6 prefix 產生 EUI-64 位址（RFC 4291）。"""
    cleaned = normalise_mac(mac)
    try:
        net = ipaddress.IPv6Network(prefix, strict=False)
    except ValueError as exc:
        raise NetToolError(f"Invalid IPv6 prefix: {exc}") from exc
    if net.prefixlen > 64:
        raise NetToolError("EUI-64 requires prefix length <= 64")
    first = int(cleaned[0:2], 16) ^ 0x02  # 翻轉 U/L bit
    iid_hex = f"{first:02x}{cleaned[2:6]}fffe{cleaned[6:12]}"  # 插入 fffe
    addr = ipaddress.IPv6Address(int(net.network_address) + int(iid_hex, 16))
    return {"mac": cleaned, "prefix": str(net), "address": str(addr)}


def ip_in_cidr(ip: str, cidr: str) -> dict[str, Any]:
    addr = parse_addr(ip)
    net = parse_net(cidr)
    if addr.version != net.version:
        raise NetToolError("IP 與 CIDR 的位址家族不一致 (IPv4/IPv6)")
    return {
        "ip": str(addr), "cidr": str(net), "contains": addr in net,
        "network_address": str(net.network_address),
        "is_network_address": addr == net.network_address,
        "is_broadcast": net.version == 4 and addr == net.broadcast_address,
    }


def cidr_relation(a: str, b: str) -> dict[str, Any]:
    na, nb = parse_net(a), parse_net(b)
    if na.version != nb.version:
        raise NetToolError("兩個 CIDR 位址家族不一致")
    if na == nb:
        rel = "equal"
    elif na.supernet_of(nb):  # type: ignore[arg-type]  # 同版本已保證同型
        rel = "a_contains_b"
    elif na.subnet_of(nb):  # type: ignore[arg-type]
        rel = "a_within_b"
    elif na.overlaps(nb):
        rel = "overlap"
    else:
        rel = "disjoint"
    return {"a": str(na), "b": str(nb), "relation": rel, "overlaps": na.overlaps(nb)}


def range_to_cidr(start: str, end: str) -> dict[str, Any]:
    s, e = parse_addr(start), parse_addr(end)
    if s.version != e.version:
        raise NetToolError("起訖 IP 位址家族不一致")
    if int(s) > int(e):
        raise NetToolError("起始 IP 不可大於結束 IP")
    try:
        cidrs = [str(n) for n in ipaddress.summarize_address_range(s, e)]
    except (ValueError, TypeError) as exc:
        raise NetToolError(str(exc)) from exc
    return {"start": str(s), "end": str(e), "cidrs": cidrs, "count": len(cidrs),
            "total_addresses": str(int(e) - int(s) + 1)}


def cidr_to_range(cidr: str) -> dict[str, Any]:
    net = parse_net(cidr)
    return {
        "cidr": str(net), "first": str(net[0]), "last": str(net[-1]),
        "num_addresses": str(net.num_addresses), "version": net.version,
    }


def aggregate(cidrs: str) -> dict[str, Any]:
    parts = [p for p in re.split(r"[,\s]+", cidrs.strip()) if p]
    if not parts:
        raise NetToolError("未提供任何 CIDR")
    nets = [parse_net(p) for p in parts]
    try:
        collapsed = [str(n) for n in ipaddress.collapse_addresses(nets)]  # type: ignore[type-var]
    except (ValueError, TypeError) as exc:
        raise NetToolError(str(exc)) from exc
    return {"input_count": len(nets), "aggregated": collapsed, "count": len(collapsed)}


def netmask(value: str) -> dict[str, Any]:
    """首碼長度 (24 或 /24) 或網路遮罩 (255.255.255.0) 互轉。"""
    v = value.strip().lstrip("/")
    try:
        if "." in v:                       # 點分十進位遮罩
            net = ipaddress.ip_network(f"0.0.0.0/{v}", strict=False)
        elif ":" in v:                     # IPv6 遮罩字串
            net = ipaddress.ip_network(f"::/{v}", strict=False)
        else:                              # 純首碼長度
            plen = int(v)
            base = "0.0.0.0" if plen <= 32 else "::"  # noqa: S104  # nosec B104 — 子網計算用字串
            net = ipaddress.ip_network(f"{base}/{plen}", strict=False)
    except ValueError as exc:
        raise NetToolError(f"無法解析遮罩/首碼：{exc}") from exc
    return {
        "prefixlen": net.prefixlen, "netmask": str(net.netmask),
        "hostmask": str(net.hostmask), "wildcard": str(net.hostmask), "version": net.version,
    }


def mac_format(mac: str) -> dict[str, Any]:
    h = normalise_mac(mac)
    colon = ":".join(h[i:i+2] for i in range(0, 12, 2))
    dash = "-".join(h[i:i+2] for i in range(0, 12, 2))
    dot = ".".join(h[i:i+4] for i in range(0, 12, 4))
    return {
        "bare": h, "colon": colon, "dash": dash, "cisco_dot": dot,
        "upper_colon": colon.upper(), "oui": h[:6], "nic": h[6:],
        "is_local": bool(int(h[1], 16) & 0x2),
        "is_multicast": bool(int(h[1], 16) & 0x1),
    }


def fqdn_parse(name: str) -> dict[str, Any]:
    """解析 / 驗證 FQDN（RFC 1123）。"""
    n = name.strip().rstrip(".")
    labels = n.split(".")
    valid = len(n) <= 253 and all(_FQDN_LABEL.match(lbl) for lbl in labels) and len(labels) >= 1
    return {
        "input": name, "normalised": n, "labels": labels, "valid": valid,
        "is_fqdn": valid and len(labels) >= 2,
        "host": labels[0] if labels else "",
        "domain": ".".join(labels[1:]) if len(labels) > 1 else None,
        "tld": labels[-1] if len(labels) > 1 else None,
    }


# ─────────────────── DNS（即時查詢）───────────────────
async def dns_lookup_live(name: str, type: str = "ANY") -> dict[str, Any]:
    """以系統解析器查 A / AAAA / PTR（stdlib，無外部相依）。"""
    type = type.upper()
    if type not in ("A", "AAAA", "PTR", "ANY"):
        raise NetToolError("type 須為 A / AAAA / PTR / ANY")
    name = name.strip().rstrip(".")
    out: dict[str, Any] = {"name": name, "type": type}
    try:
        if type == "PTR":
            addr = parse_addr(name)
            host, aliases, _ = await asyncio.wait_for(
                asyncio.to_thread(socket.gethostbyaddr, str(addr)), timeout=5,
            )
            out["ptr"] = [host, *aliases]
        else:
            infos = await asyncio.wait_for(
                asyncio.to_thread(socket.getaddrinfo, name, None), timeout=5,
            )
            a, aaaa = [], []
            for fam, _t, _p, _c, sa in infos:
                ipv = sa[0]
                if fam == socket.AF_INET and ipv not in a:
                    a.append(ipv)
                elif fam == socket.AF_INET6 and ipv not in aaaa:
                    aaaa.append(ipv)
            if type in ("A", "ANY"):
                out["A"] = a
            if type in ("AAAA", "ANY"):
                out["AAAA"] = aaaa
    except TimeoutError:
        raise NetToolError("DNS 查詢逾時") from None
    except (socket.gaierror, socket.herror) as exc:
        out["error"] = f"解析失敗：{exc}"
    return out


async def dns_mail(domain: str, dkim_selector: str = "") -> dict[str, Any]:
    """郵件相關 DNS 診斷：MX / SPF / DMARC / DKIM（dnspython）。"""
    import dns.resolver

    domain = domain.strip().rstrip(".")
    out: dict[str, Any] = {"domain": domain}

    def _q(name: str, rdtype: str) -> list[str]:
        try:
            res = dns.resolver.Resolver()
            res.lifetime = 5.0
            res.timeout = 5.0
            ans = res.resolve(name, rdtype)
            if rdtype == "MX":
                return sorted(f"{r.preference} {r.exchange.to_text().rstrip('.')}" for r in ans)
            return ["".join(s.decode() if isinstance(s, bytes) else s for s in r.strings)
                    if hasattr(r, "strings") else r.to_text().strip('"') for r in ans]
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except Exception:
            return []

    def _work() -> dict[str, Any]:
        mx = _q(domain, "MX")
        txt = _q(domain, "TXT")
        spf = [t for t in txt if t.lower().startswith("v=spf1")]
        dmarc = _q(f"_dmarc.{domain}", "TXT")
        result: dict[str, Any] = {"mx": mx, "txt": txt, "spf": spf, "dmarc": dmarc}
        if dkim_selector.strip():
            sel = dkim_selector.strip()
            result["dkim"] = _q(f"{sel}._domainkey.{domain}", "TXT")
            result["dkim_selector"] = sel
        return result

    try:
        out.update(await asyncio.wait_for(asyncio.to_thread(_work), timeout=20))
    except TimeoutError:
        raise NetToolError("DNS 查詢逾時") from None
    return out


# ─────────────────── 機房電力試算 ───────────────────
def power_calc(
    *,
    volts: float = 220,
    amps: float = 16,
    phase: str = "1",
    pf: float = 0.95,
    heat_watts: float | None = None,
    batt_wh: float | None = None,
    load_w: float | None = None,
    pdu_a: float | None = None,
) -> dict[str, Any]:
    """機房常用電力換算（與前端「機房電力試算」同公式）：

    - load_watts：V × A × PF（三相再乘 √3）
    - btu_per_hr：heat_watts × 3.412（散熱量；預設用 load_watts）
    - ups_minutes：batt_wh / load_w × 60（UPS 概略續航）
    - pdu_safe_amps：pdu_a × 0.8（PDU 80% 安全載量）
    """
    if str(phase) not in ("1", "3"):
        raise NetToolError("phase 須為 '1'（單相）或 '3'（三相）")
    factor = 3 ** 0.5 if str(phase) == "3" else 1.0
    load_watts = round(factor * (volts or 0) * (amps or 0) * (pf or 0))
    heat = heat_watts if heat_watts is not None else load_watts
    out: dict[str, Any] = {
        "load_watts": load_watts,
        "load_kw": round(load_watts / 1000, 3),
        "btu_per_hr": round((heat or 0) * 3.412),
    }
    if batt_wh is not None and load_w is not None and load_w > 0:
        out["ups_minutes"] = round(batt_wh / load_w * 60)
    if pdu_a is not None:
        out["pdu_safe_amps"] = round(pdu_a * 0.8, 1)
    return out
