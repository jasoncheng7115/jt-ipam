"""IP / CIDR 計算機與工具端點（純運算，無 DB 寫入）。

實際運算邏輯在 `app/services/nettools.py`，同一套同時供 MCP / AI chat 工具使用。
此處只負責：HTTP 參數驗證、把 `NetToolError` 翻成 400、套 response_model。

OWASP A05：所有輸入透過 stdlib `ipaddress` 解析（service 層），拒絕不合法 input。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.db import get_session
from app.schemas.base import StrictModel
from app.services import nettools
from app.services.nettools import NetToolError

router = APIRouter(prefix="/tools", tags=["tools"])


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


def _bad(exc: NetToolError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


# ─────────────────── Endpoints ───────────────────
@router.get("/ip-info", response_model=IPInfo)
async def ip_info(
    _user: CurrentUser,
    ip: Annotated[str, Query(min_length=2, max_length=64)],
) -> IPInfo:
    try:
        return IPInfo(**nettools.ip_info(ip))
    except NetToolError as exc:
        raise _bad(exc) from exc


@router.get("/cidr-info", response_model=CIDRInfo)
async def cidr_info(
    _user: CurrentUser,
    cidr: Annotated[str, Query(min_length=3, max_length=64)],
) -> CIDRInfo:
    try:
        return CIDRInfo(**nettools.cidr_info(cidr))
    except NetToolError as exc:
        raise _bad(exc) from exc


@router.get("/cidr-split", response_model=CIDRSplit)
async def cidr_split(
    _user: CurrentUser,
    cidr: Annotated[str, Query(min_length=3, max_length=64)],
    new_prefix: Annotated[int, Query(ge=0, le=128)],
) -> CIDRSplit:
    try:
        return CIDRSplit(**nettools.cidr_split(cidr, new_prefix))
    except NetToolError as exc:
        raise _bad(exc) from exc


@router.get("/eui64", response_model=EUI64Result)
async def eui64(
    _user: CurrentUser,
    mac: Annotated[str, Query(min_length=12, max_length=17)],
    prefix: Annotated[str, Query(min_length=3, max_length=64)],
) -> EUI64Result:
    """從 MAC 與 IPv6 prefix 產生 EUI-64 位址（RFC 4291）。prefix 應為 /64 或更短。"""
    try:
        return EUI64Result(**nettools.eui64(mac, prefix))
    except NetToolError as exc:
        raise _bad(exc) from exc


# ─────────────────── 更多 IP / CIDR / FQDN / DNS 工具 ───────────────────
@router.get("/ip-in-cidr")
async def ip_in_cidr(
    _user: CurrentUser,
    ip: Annotated[str, Query(min_length=2, max_length=64)],
    cidr: Annotated[str, Query(min_length=3, max_length=64)],
) -> dict[str, Any]:
    """判斷某 IP 是否落在某 CIDR 內。"""
    try:
        return nettools.ip_in_cidr(ip, cidr)
    except NetToolError as exc:
        raise _bad(exc) from exc


@router.get("/cidr-relation")
async def cidr_relation(
    _user: CurrentUser,
    a: Annotated[str, Query(min_length=3, max_length=64)],
    b: Annotated[str, Query(min_length=3, max_length=64)],
) -> dict[str, Any]:
    """兩個 CIDR 的關係：相等 / 包含 / 被包含 / 重疊 / 不相交。"""
    try:
        return nettools.cidr_relation(a, b)
    except NetToolError as exc:
        raise _bad(exc) from exc


@router.get("/range-to-cidr")
async def range_to_cidr(
    _user: CurrentUser,
    start: Annotated[str, Query(min_length=2, max_length=64)],
    end: Annotated[str, Query(min_length=2, max_length=64)],
) -> dict[str, Any]:
    """把起訖 IP 範圍轉成最少數量的 CIDR 區塊。"""
    try:
        return nettools.range_to_cidr(start, end)
    except NetToolError as exc:
        raise _bad(exc) from exc


@router.get("/cidr-to-range")
async def cidr_to_range(
    _user: CurrentUser,
    cidr: Annotated[str, Query(min_length=3, max_length=64)],
) -> dict[str, Any]:
    """CIDR → 起訖位址與總數。"""
    try:
        return nettools.cidr_to_range(cidr)
    except NetToolError as exc:
        raise _bad(exc) from exc


@router.get("/aggregate")
async def aggregate(
    _user: CurrentUser,
    cidrs: Annotated[str, Query(min_length=3, max_length=4096)],
) -> dict[str, Any]:
    """把多個 CIDR（逗號或空白分隔）聚合成最少數量的區塊。"""
    try:
        return nettools.aggregate(cidrs)
    except NetToolError as exc:
        raise _bad(exc) from exc


@router.get("/netmask")
async def netmask_convert(
    _user: CurrentUser,
    value: Annotated[str, Query(min_length=1, max_length=64)],
) -> dict[str, Any]:
    """首碼長度 (24 或 /24) 或網路遮罩 (255.255.255.0) 互轉，附 wildcard / hostmask。"""
    try:
        return nettools.netmask(value)
    except NetToolError as exc:
        raise _bad(exc) from exc


@router.get("/mac-format")
async def mac_format(
    _user: CurrentUser,
    mac: Annotated[str, Query(min_length=12, max_length=23)],
) -> dict[str, Any]:
    """MAC 正規化為各種常見格式。"""
    try:
        return nettools.mac_format(mac)
    except NetToolError as exc:
        raise _bad(exc) from exc


@router.get("/fqdn")
async def fqdn_parse(
    _user: CurrentUser,
    name: Annotated[str, Query(min_length=1, max_length=255)],
) -> dict[str, Any]:
    """解析 / 驗證 FQDN（RFC 1123）：labels、TLD、host、domain。"""
    return nettools.fqdn_parse(name)


@router.get("/dns-lookup")
async def dns_lookup(
    _user: CurrentUser,
    name: Annotated[str, Query(min_length=1, max_length=255)],
    type: Annotated[str, Query(pattern=r"^(A|AAAA|PTR|ANY)$")] = "ANY",
) -> dict[str, Any]:
    """以系統解析器查 A / AAAA / PTR（stdlib，無外部相依）。"""
    try:
        return await nettools.dns_lookup_live(name, type)
    except NetToolError as exc:
        # 逾時翻 504、其餘 400
        if "逾時" in str(exc):
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        raise _bad(exc) from exc


@router.get("/geoip")
async def geoip(
    _user: CurrentUser,
    ip: Annotated[str, Query(min_length=1, max_length=64)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """IP 地理位置查詢（MaxMind GeoLite2 web service；需管理者先在系統設定填憑證）。"""
    try:
        addr = nettools.parse_addr(ip)
    except NetToolError as exc:
        raise _bad(exc) from exc
    from app.services.geoip import geoip_lookup
    return await geoip_lookup(session, str(addr))


@router.get("/dns-mail")
async def dns_mail(
    _user: CurrentUser,
    domain: Annotated[str, Query(min_length=1, max_length=255)],
    dkim_selector: Annotated[str, Query(max_length=128)] = "",
) -> dict[str, Any]:
    """郵件相關 DNS 診斷：MX / SPF / DMARC / DKIM。"""
    try:
        return await nettools.dns_mail(domain, dkim_selector)
    except NetToolError as exc:
        if "逾時" in str(exc):
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        raise _bad(exc) from exc
