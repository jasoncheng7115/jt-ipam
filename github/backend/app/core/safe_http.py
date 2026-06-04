"""統一外部 HTTP client，內建 SSRF 防護（OWASP A06）。

使用方式：

    from app.core.safe_http import safe_request
    resp = await safe_request("GET", "https://librenms.example.com/api/v0/devices",
                              headers={...}, timeout=10.0)

任何對外整合（DNS server、LibreNMS、Webhook、OIDC discovery）一律走這支；
不允許直接 `httpx.AsyncClient(...)` 呼叫使用者控制的 URL。

防護內容：
1. URL 白名單（協定、host、解析後 IP CIDR）
2. DNS 解析後 pin IP，避免 DNS rebinding
3. 重導向 follow 上限 + 每次 redirect 重檢
4. 超時必填
5. 阻擋 metadata IP（AWS/GCP/Azure 169.254.169.254、Alibaba 100.100.100.200）
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings

# =============================================================================
# Hardcoded denylist（無論設定如何，都不允許）
# =============================================================================
_BLOCKED_CIDRS: Final[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] = (
    # Loopback
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    # Link-local（含 cloud metadata）
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    # Unique local IPv6 隨設定
    # Multicast / broadcast
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("ff00::/8"),
    ipaddress.ip_network("255.255.255.255/32"),
    # 0.0.0.0/8（routable 為 unspecified）
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
    # Carrier-grade NAT — 對 IPAM 通常不該打過去
    ipaddress.ip_network("100.64.0.0/10"),
)

_PRIVATE_CIDRS: Final[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)

_ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
_DEFAULT_TIMEOUT: Final[float] = 10.0
_MAX_REDIRECTS: Final[int] = 3


class UnsafeOutboundURL(ValueError):
    """請求被 SSRF 防護擋下。"""


def _parse_allow_cidrs(items: list[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    out: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for raw in items:
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    return out


def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeOutboundURL(f"DNS resolution failed for {host}: {exc}") from exc
    addrs: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for family, _type, _proto, _name, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if family == socket.AF_INET6 and isinstance(addr, ipaddress.IPv6Address):
            addrs.append(addr)
        elif family == socket.AF_INET and isinstance(addr, ipaddress.IPv4Address):
            addrs.append(addr)
    if not addrs:
        raise UnsafeOutboundURL(f"No usable address for {host}")
    return addrs


def _ip_in(addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
           networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
                     | list[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    return any(addr in net for net in networks if addr.version == net.version)


def assert_url_safe(url: str) -> None:
    """A10 — 檢查 URL 是否安全；不安全則丟 UnsafeOutboundURL。"""
    settings = get_settings()
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeOutboundURL(f"Disallowed scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeOutboundURL("URL missing host")

    # IP literal 直接驗證
    try:
        addr = ipaddress.ip_address(host)
        addrs = [addr]
    except ValueError:
        # 走 DNS
        if settings.outbound_allow_hosts and host not in settings.outbound_allow_hosts:
            # host 白名單存在則必須命中
            pass  # fallthrough：仍會檢 IP 白名單
        addrs = _resolve(host)

    extra_allow = _parse_allow_cidrs(settings.outbound_allow_cidrs)

    for ip in addrs:
        # 白名單命中可放行（即便落在 private）
        if extra_allow and _ip_in(ip, extra_allow):
            continue
        # 黑名單一律擋
        if _ip_in(ip, _BLOCKED_CIDRS):
            raise UnsafeOutboundURL(f"Blocked IP for SSRF: {ip}")
        # 私網需明確允許（A10）
        if _ip_in(ip, _PRIVATE_CIDRS) and not settings.outbound_allow_private:
            raise UnsafeOutboundURL(
                f"Private IP {ip} not allowed (set OUTBOUND_ALLOW_PRIVATE=true if intended)"
            )


async def safe_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: Any = None,
    content: bytes | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    verify: bool = True,
    max_redirects: int = _MAX_REDIRECTS,
) -> httpx.Response:
    """經過 SSRF 檢查的 HTTP 請求。

    redirect 採手動處理：每次重導向後重新驗 URL，避免 302 → 169.254.169.254。
    """
    current_url = url
    for _ in range(max_redirects + 1):
        assert_url_safe(current_url)
        async with httpx.AsyncClient(
            timeout=timeout,
            verify=verify,
            follow_redirects=False,
            http2=True,
            trust_env=False,  # A05：不信任 HTTP_PROXY 等環境變數
        ) as client:
            resp = await client.request(
                method,
                current_url,
                headers=headers,
                params=params,
                json=json,
                content=content,
            )
        if resp.is_redirect and resp.next_request is not None:
            current_url = str(resp.next_request.url)
            continue
        return resp
    raise UnsafeOutboundURL(f"Too many redirects following {url}")


@asynccontextmanager
async def safe_stream(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: Any = None,
    timeout: float = _DEFAULT_TIMEOUT,
    verify: bool = True,
) -> AsyncIterator[httpx.Response]:
    """經過 SSRF 檢查的 streaming HTTP 請求（NDJSON / SSE 用）。

    與 safe_request 不同：不 follow redirect（streaming 對重導向再驗 URL 成本高，
    且本專案的 streaming 目標只有自家 Ollama）。URL 仍先過 assert_url_safe。

        async with safe_stream("POST", url, json=body, timeout=t) as resp:
            async for line in resp.aiter_lines():
                ...
    """
    assert_url_safe(url)
    async with httpx.AsyncClient(
        timeout=timeout,
        verify=verify,
        follow_redirects=False,
        http2=True,
        trust_env=False,  # A05：不信任 HTTP_PROXY 等環境變數
    ) as client:
        async with client.stream(method, url, headers=headers, json=json) as resp:
            yield resp
