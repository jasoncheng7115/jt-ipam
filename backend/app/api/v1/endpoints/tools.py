"""IP / CIDR 計算機與工具端點（純運算，無 DB 寫入）。

OWASP A05：所有輸入透過 stdlib `ipaddress` 解析，拒絕不合法 input。
"""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.v1.dependencies import CurrentUser
from app.schemas.base import StrictModel

router = APIRouter(prefix="/tools", tags=["tools"])

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$|^[0-9A-Fa-f]{12}$")


# ─────────────────── Schemas ───────────────────
class IPInfo(StrictModel):
    ip: str
    version: int
    is_private: bool
    is_global: bool
    is_reserved: bool
    is_multicast: bool
    is_loopback: bool
    is_link_local: bool
    decimal: str  # int as string（避免 JS 大整數精度）
    hex: str
    reverse_pointer: str
    binary: str | None  # IPv4 32-bit binary；IPv6 因長度太長 → None


class CIDRInfo(StrictModel):
    cidr: str
    version: int
    network_address: str
    broadcast_address: str | None  # IPv6 沒有 broadcast 概念
    netmask: str
    hostmask: str
    prefixlen: int
    num_addresses: str  # int as string
    host_count: str
    first_host: str | None
    last_host: str | None
    is_private: bool


class CIDRSplit(StrictModel):
    cidr: str
    new_prefix: int
    subnets: list[str]
    count: int


class EUI64Result(StrictModel):
    mac: str
    prefix: str
    address: str


# ─────────────────── Helpers ───────────────────
def _net_or_400(cidr: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    try:
        return ipaddress.ip_network(cidr, strict=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid CIDR: {exc}") from exc


def _addr_or_400(ip: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        return ipaddress.ip_address(ip)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid IP: {exc}") from exc


def _normalise_mac(mac: str) -> str:
    """轉成連續 12 位 hex（小寫）。"""
    cleaned = mac.replace(":", "").replace("-", "").lower()
    if len(cleaned) != 12 or not all(c in "0123456789abcdef" for c in cleaned):
        raise HTTPException(status_code=400, detail=f"Invalid MAC: {mac}")
    return cleaned


# ─────────────────── Endpoints ───────────────────
@router.get("/ip-info", response_model=IPInfo)
async def ip_info(
    _user: CurrentUser,
    ip: Annotated[str, Query(min_length=2, max_length=64)],
) -> IPInfo:
    addr = _addr_or_400(ip)
    binary = None
    if isinstance(addr, ipaddress.IPv4Address):
        binary = format(int(addr), "032b")
    return IPInfo(
        ip=str(addr),
        version=addr.version,
        is_private=addr.is_private,
        is_global=addr.is_global,
        is_reserved=addr.is_reserved,
        is_multicast=addr.is_multicast,
        is_loopback=addr.is_loopback,
        is_link_local=addr.is_link_local,
        decimal=str(int(addr)),
        hex="0x" + format(int(addr), "x"),
        reverse_pointer=addr.reverse_pointer,
        binary=binary,
    )


@router.get("/cidr-info", response_model=CIDRInfo)
async def cidr_info(
    _user: CurrentUser,
    cidr: Annotated[str, Query(min_length=3, max_length=64)],
) -> CIDRInfo:
    net = _net_or_400(cidr)
    is_v4 = isinstance(net, ipaddress.IPv4Network)
    if is_v4:
        if net.prefixlen >= 31:
            host_count = net.num_addresses
            first_host = str(net.network_address)
            last_host = str(net.broadcast_address)
        else:
            host_count = net.num_addresses - 2
            first_host = str(net.network_address + 1)
            last_host = str(net.broadcast_address - 1)
        broadcast = str(net.broadcast_address)
    else:
        host_count = net.num_addresses
        first_host = str(net.network_address) if net.num_addresses > 0 else None
        last_host = str(net.broadcast_address) if net.num_addresses > 0 else None
        broadcast = None

    return CIDRInfo(
        cidr=str(net),
        version=net.version,
        network_address=str(net.network_address),
        broadcast_address=broadcast,
        netmask=str(net.netmask),
        hostmask=str(net.hostmask),
        prefixlen=net.prefixlen,
        num_addresses=str(net.num_addresses),
        host_count=str(host_count),
        first_host=first_host,
        last_host=last_host,
        is_private=net.is_private,
    )


@router.get("/cidr-split", response_model=CIDRSplit)
async def cidr_split(
    _user: CurrentUser,
    cidr: Annotated[str, Query(min_length=3, max_length=64)],
    new_prefix: Annotated[int, Query(ge=0, le=128)],
) -> CIDRSplit:
    net = _net_or_400(cidr)
    if new_prefix < net.prefixlen:
        raise HTTPException(
            status_code=400,
            detail=f"new_prefix {new_prefix} must be >= existing /{net.prefixlen}",
        )
    if (net.version == 4 and new_prefix > 32) or (net.version == 6 and new_prefix > 128):
        raise HTTPException(status_code=400, detail="prefix out of range for this address family")
    # A04：阻擋過大切割（避免 OOM）
    bits = new_prefix - net.prefixlen
    if bits > 16:
        raise HTTPException(
            status_code=400,
            detail=f"refusing to split into {1 << bits} subnets; bits delta must be <= 16",
        )
    subs = [str(s) for s in net.subnets(new_prefix=new_prefix)]
    return CIDRSplit(cidr=str(net), new_prefix=new_prefix, subnets=subs, count=len(subs))


@router.get("/eui64", response_model=EUI64Result)
async def eui64(
    _user: CurrentUser,
    mac: Annotated[str, Query(min_length=12, max_length=17)],
    prefix: Annotated[str, Query(min_length=3, max_length=64)],
) -> EUI64Result:
    """從 MAC 與 IPv6 prefix 產生 EUI-64 位址（RFC 4291）。

    `prefix` 應為 /64 或更短（例：2001:db8::/64）。
    """
    cleaned = _normalise_mac(mac)
    try:
        net = ipaddress.IPv6Network(prefix, strict=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid IPv6 prefix: {exc}") from exc
    if net.prefixlen > 64:
        raise HTTPException(status_code=400, detail="EUI-64 requires prefix length <= 64")

    # EUI-64：插入 fffe；翻轉 U/L bit（第 7 位）
    first = int(cleaned[0:2], 16) ^ 0x02
    iid_hex = f"{first:02x}{cleaned[2:6]}fffe{cleaned[6:12]}"
    # 拼成 IPv6
    iid_int = int(iid_hex, 16)
    addr = ipaddress.IPv6Address(int(net.network_address) + iid_int)
    return EUI64Result(mac=cleaned, prefix=str(net), address=str(addr))


# ─────────────────── 更多 IP / CIDR / FQDN / DNS 工具 ───────────────────

@router.get("/ip-in-cidr")
async def ip_in_cidr(
    _user: CurrentUser,
    ip: Annotated[str, Query(min_length=2, max_length=64)],
    cidr: Annotated[str, Query(min_length=3, max_length=64)],
) -> dict:
    """判斷某 IP 是否落在某 CIDR 內。"""
    addr = _addr_or_400(ip)
    net = _net_or_400(cidr)
    if addr.version != net.version:
        raise HTTPException(status_code=400, detail="IP 與 CIDR 的位址家族不一致 (IPv4/IPv6)")
    return {
        "ip": str(addr), "cidr": str(net), "contains": addr in net,
        "network_address": str(net.network_address),
        "is_network_address": addr == net.network_address,
        "is_broadcast": net.version == 4 and addr == net.broadcast_address,
    }


@router.get("/cidr-relation")
async def cidr_relation(
    _user: CurrentUser,
    a: Annotated[str, Query(min_length=3, max_length=64)],
    b: Annotated[str, Query(min_length=3, max_length=64)],
) -> dict:
    """兩個 CIDR 的關係：相等 / 包含 / 被包含 / 重疊 / 不相交。"""
    na, nb = _net_or_400(a), _net_or_400(b)
    if na.version != nb.version:
        raise HTTPException(status_code=400, detail="兩個 CIDR 位址家族不一致")
    if na == nb:
        rel = "equal"
    elif na.supernet_of(nb):
        rel = "a_contains_b"
    elif na.subnet_of(nb):
        rel = "a_within_b"
    elif na.overlaps(nb):
        rel = "overlap"
    else:
        rel = "disjoint"
    return {"a": str(na), "b": str(nb), "relation": rel, "overlaps": na.overlaps(nb)}


@router.get("/range-to-cidr")
async def range_to_cidr(
    _user: CurrentUser,
    start: Annotated[str, Query(min_length=2, max_length=64)],
    end: Annotated[str, Query(min_length=2, max_length=64)],
) -> dict:
    """把起訖 IP 範圍轉成最少數量的 CIDR 區塊。"""
    s, e = _addr_or_400(start), _addr_or_400(end)
    if s.version != e.version:
        raise HTTPException(status_code=400, detail="起訖 IP 位址家族不一致")
    if int(s) > int(e):
        raise HTTPException(status_code=400, detail="起始 IP 不可大於結束 IP")
    try:
        cidrs = [str(n) for n in ipaddress.summarize_address_range(s, e)]
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"start": str(s), "end": str(e), "cidrs": cidrs, "count": len(cidrs),
            "total_addresses": str(int(e) - int(s) + 1)}


@router.get("/cidr-to-range")
async def cidr_to_range(
    _user: CurrentUser,
    cidr: Annotated[str, Query(min_length=3, max_length=64)],
) -> dict:
    """CIDR → 起訖位址與總數。"""
    net = _net_or_400(cidr)
    return {
        "cidr": str(net), "first": str(net[0]), "last": str(net[-1]),
        "num_addresses": str(net.num_addresses), "version": net.version,
    }


@router.get("/aggregate")
async def aggregate(
    _user: CurrentUser,
    cidrs: Annotated[str, Query(min_length=3, max_length=4096)],
) -> dict:
    """把多個 CIDR（逗號或空白分隔）聚合成最少數量的區塊。"""
    parts = [p for p in re.split(r"[,\s]+", cidrs.strip()) if p]
    nets = []
    for p in parts:
        nets.append(_net_or_400(p))
    try:
        collapsed = [str(n) for n in ipaddress.collapse_addresses(nets)]
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"input_count": len(nets), "aggregated": collapsed, "count": len(collapsed)}


@router.get("/netmask")
async def netmask_convert(
    _user: CurrentUser,
    value: Annotated[str, Query(min_length=1, max_length=64)],
) -> dict:
    """首碼長度 (24 或 /24) 或網路遮罩 (255.255.255.0) 互轉，附 wildcard / hostmask。"""
    v = value.strip().lstrip("/")
    try:
        if "." in v:                       # 點分十進位遮罩，如 255.255.255.0
            net = ipaddress.ip_network(f"0.0.0.0/{v}", strict=False)
        elif ":" in v:                     # IPv6 遮罩字串
            net = ipaddress.ip_network(f"::/{v}", strict=False)
        else:                              # 純首碼長度
            plen = int(v)
            base = "0.0.0.0" if plen <= 32 else "::"
            net = ipaddress.ip_network(f"{base}/{plen}", strict=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"無法解析遮罩/首碼：{exc}") from exc
    return {
        "prefixlen": net.prefixlen,
        "netmask": str(net.netmask),
        "hostmask": str(net.hostmask),
        "wildcard": str(net.hostmask),
        "version": net.version,
    }


@router.get("/mac-format")
async def mac_format(
    _user: CurrentUser,
    mac: Annotated[str, Query(min_length=12, max_length=23)],
) -> dict:
    """MAC 正規化為各種常見格式。"""
    h = _normalise_mac(mac)
    colon = ":".join(h[i:i+2] for i in range(0, 12, 2))
    dash = "-".join(h[i:i+2] for i in range(0, 12, 2))
    dot = ".".join(h[i:i+4] for i in range(0, 12, 4))
    return {
        "bare": h, "colon": colon, "dash": dash, "cisco_dot": dot,
        "upper_colon": colon.upper(),
        "oui": h[:6], "nic": h[6:],
        "is_local": bool(int(h[1], 16) & 0x2),
        "is_multicast": bool(int(h[1], 16) & 0x1),
    }


_FQDN_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


@router.get("/fqdn")
async def fqdn_parse(
    _user: CurrentUser,
    name: Annotated[str, Query(min_length=1, max_length=255)],
) -> dict:
    """解析 / 驗證 FQDN（RFC 1123）：labels、TLD、host、domain。"""
    n = name.strip().rstrip(".")
    labels = n.split(".")
    valid = len(n) <= 253 and all(_FQDN_LABEL.match(lbl) for lbl in labels) and len(labels) >= 1
    return {
        "input": name, "normalised": n, "labels": labels,
        "valid": valid,
        "is_fqdn": valid and len(labels) >= 2,
        "host": labels[0] if labels else "",
        "domain": ".".join(labels[1:]) if len(labels) > 1 else None,
        "tld": labels[-1] if len(labels) > 1 else None,
    }


@router.get("/dns-lookup")
async def dns_lookup(
    _user: CurrentUser,
    name: Annotated[str, Query(min_length=1, max_length=255)],
    type: Annotated[str, Query(pattern=r"^(A|AAAA|PTR|ANY)$")] = "ANY",
) -> dict:
    """以系統解析器查 A / AAAA / PTR（stdlib，無外部相依）。"""
    import asyncio
    import socket

    name = name.strip().rstrip(".")
    out: dict = {"name": name, "type": type}
    try:
        if type == "PTR":
            addr = _addr_or_400(name)
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
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="DNS 查詢逾時") from None
    except (socket.gaierror, socket.herror) as exc:
        out["error"] = f"解析失敗：{exc}"
    return out


@router.get("/dns-mail")
async def dns_mail(
    _user: CurrentUser,
    domain: Annotated[str, Query(min_length=1, max_length=255)],
    dkim_selector: Annotated[str, Query(max_length=128)] = "",
) -> dict:
    """郵件相關 DNS 診斷：MX / SPF (TXT v=spf1) / DMARC (_dmarc TXT) /
    DKIM (<selector>._domainkey TXT)。用 dnspython 查。"""
    import asyncio

    import dns.resolver

    domain = domain.strip().rstrip(".")
    out: dict = {"domain": domain}

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
        except Exception as exc:  # noqa: BLE001 — 回報而非中斷
            return [f"__error__:{type(exc).__name__}"]

    def _work() -> dict:
        mx = _q(domain, "MX")
        txt = _q(domain, "TXT")
        spf = [t for t in txt if t.lower().startswith("v=spf1")]
        dmarc = _q(f"_dmarc.{domain}", "TXT")
        result = {"mx": mx, "txt": txt, "spf": spf, "dmarc": dmarc}
        if dkim_selector.strip():
            sel = dkim_selector.strip()
            result["dkim"] = _q(f"{sel}._domainkey.{domain}", "TXT")
            result["dkim_selector"] = sel
        return result

    try:
        out.update(await asyncio.wait_for(asyncio.to_thread(_work), timeout=20))
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="DNS 查詢逾時") from None
    return out
