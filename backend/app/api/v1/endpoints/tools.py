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
