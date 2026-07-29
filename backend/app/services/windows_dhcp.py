"""Windows DHCP Server 同步（Beta）—— WinRM + PowerShell，唯讀。

與 Windows DNS 共用同一套安全作法：
- `_check_address_safe`：SSRF 防護（封鎖 metadata/link-local；私網需 OUTBOUND_ALLOW_PRIVATE）
- `_safe_ps_arg`：PowerShell 參數白名單，杜絕指令注入（A03）
- 阻塞式 pywinrm 包進 `asyncio.to_thread`，不卡住 event loop

只做 GET 類的 cmdlet（Get-*），絕不改 Windows DHCP 任何設定。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.safe_http import _BLOCKED_CIDRS, _PRIVATE_CIDRS, _ip_in
from app.core.security import decrypt_secret, encrypt_secret
from app.models.address import IPAddress
from app.models.windows_dhcp import WindowsDhcpServer

_PS_SAFE = re.compile(r"^[A-Za-z0-9._:\-/]+$")


class WindowsDhcpError(Exception):
    pass


def _aad(instance_id: Any) -> bytes:
    return f"windows_dhcp_server:{instance_id}:password".encode()


def encrypt_password(instance_id: Any, raw: str) -> tuple[bytes, bytes]:
    return encrypt_secret(raw, aad=_aad(instance_id))


def _decrypt_password(inst: WindowsDhcpServer) -> str:
    return decrypt_secret(inst.password_enc, inst.password_nonce, aad=_aad(inst.id)).decode("utf-8")


def _safe_ps_arg(value: str) -> str:
    if not _PS_SAFE.match(value):
        raise WindowsDhcpError(f"unsafe PowerShell argument: {value!r}")
    return value


def _check_address_safe(host: str) -> None:
    settings = get_settings()
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise WindowsDhcpError(f"DNS resolution failed for {host}") from exc
        addrs = [ipaddress.ip_address(info[4][0]) for info in infos]
    for ip in addrs:
        if _ip_in(ip, _BLOCKED_CIDRS):
            raise WindowsDhcpError(f"Blocked IP for SSRF: {ip}")
        if _ip_in(ip, _PRIVATE_CIDRS) and not settings.outbound_allow_private:
            raise WindowsDhcpError(f"Private IP {ip} not allowed without OUTBOUND_ALLOW_PRIVATE")


class WindowsDhcpClient:
    """對單一台 Windows DHCP Server 的唯讀 WinRM 用戶端。"""

    def __init__(self, *, host: str, username: str, password: str,
                 port: int = 5986, use_ssl: bool = True, verify_tls: bool = True,
                 timeout: float = 30.0) -> None:
        if not host:
            raise WindowsDhcpError("Windows DHCP: host is required")
        _check_address_safe(host)
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_ssl = use_ssl
        self.verify_tls = verify_tls
        self.timeout = timeout

    def _session(self) -> Any:
        # winrm import 放這裡，讓單元測試不必裝齊全部相依
        import winrm
        scheme = "https" if self.use_ssl else "http"
        return winrm.Session(
            target=f"{scheme}://{self.host}:{self.port}/wsman",
            auth=(self.username, self.password),
            transport="ntlm",
            server_cert_validation="validate" if (self.use_ssl and self.verify_tls) else "ignore",
            operation_timeout_sec=int(self.timeout),
            read_timeout_sec=int(self.timeout) + 5,
        )

    def _run_ps(self, script: str) -> str:
        try:
            result = self._session().run_ps(script)
        except WindowsDhcpError:
            raise
        except Exception as exc:   # winrm/requests 的連線/認證/TLS/timeout 都不是我們的例外型別
            raise WindowsDhcpError(
                f"WinRM connection failed: {exc.__class__.__name__}: {exc}"
            ) from exc
        if result.status_code != 0:
            raise WindowsDhcpError(
                f"PowerShell error (rc={result.status_code}): "
                f"{(result.std_err or b'').decode('utf-8', errors='replace')[:300]}"
            )
        return (result.std_out or b"").decode("utf-8", errors="replace")

    def _run_json(self, script: str) -> list[dict[str, Any]]:
        """跑 PowerShell 並解析 JSON；單筆物件也正規化成 list。"""
        out = self._run_ps(script).strip()
        if not out:
            return []
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise WindowsDhcpError(f"unexpected PowerShell output: {out[:200]}") from exc
        if isinstance(data, dict):
            return [data]
        return [d for d in data if isinstance(d, dict)]

    # ── 唯讀查詢 ──
    def get_scopes(self) -> list[dict[str, Any]]:
        return self._run_json(
            "Get-DhcpServerv4Scope | Select-Object ScopeId,SubnetMask,StartRange,EndRange,Name,State "
            "| ConvertTo-Json -Depth 4 -Compress"
        )

    def get_leases(self, scope_id: str) -> list[dict[str, Any]]:
        sid = _safe_ps_arg(scope_id)
        return self._run_json(
            f"Get-DhcpServerv4Lease -ScopeId {sid} "
            "| Select-Object IPAddress,ClientId,HostName,AddressState "
            "| ConvertTo-Json -Depth 4 -Compress"
        )


def _client(inst: WindowsDhcpServer) -> WindowsDhcpClient:
    return WindowsDhcpClient(
        host=inst.host, username=inst.username, password=_decrypt_password(inst),
        port=inst.port, use_ssl=inst.use_ssl, verify_tls=inst.verify_tls,
    )


def _as_str(v: Any) -> str | None:
    """PowerShell 的 IP 物件序列化後可能是 {'IPAddressToString': '10.0.0.1'} 這種形狀。"""
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, dict):
        for k in ("IPAddressToString", "Address", "Value"):
            got = v.get(k)
            if isinstance(got, str) and got.strip():
                return got.strip()
    return None


def _norm_mac(v: Any) -> str | None:
    """Windows 的 ClientId 常見為 aa-bb-cc-dd-ee-ff → 正規化成冒號分隔。"""
    s = _as_str(v)
    if not s:
        return None
    s = s.replace("-", ":").lower()
    return s if re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", s) else None


async def healthcheck(inst: WindowsDhcpServer) -> dict[str, Any]:
    """測試連線：能列出 scope 就代表 WinRM 認證與 DHCP 權限都通。"""
    cli = _client(inst)
    scopes = await asyncio.to_thread(cli.get_scopes)
    return {"host": inst.host, "scopes": len(scopes)}


async def sync_scopes(session: AsyncSession, inst: WindowsDhcpServer) -> int:
    """把 Windows DHCP 的 scope 鏡像進 dhcp_pool_ranges（只清自己的列）。"""
    from app.models.dhcp import DHCPPoolRange

    cli = _client(inst)
    scopes = await asyncio.to_thread(cli.get_scopes)
    now = datetime.now(UTC)

    rows: list[tuple[str | None, str, str]] = []
    for sc in scopes:
        if str(sc.get("State") or "").lower() == "inactive":
            continue   # 停用的 scope 不會發放
        start, end = _as_str(sc.get("StartRange")), _as_str(sc.get("EndRange"))
        if not start or not end:
            continue
        scope_id, mask = _as_str(sc.get("ScopeId")), _as_str(sc.get("SubnetMask"))
        cidr = None
        if scope_id and mask:
            try:
                cidr = str(ipaddress.ip_network(f"{scope_id}/{mask}", strict=False))
            except ValueError:
                cidr = scope_id
        rows.append((cidr, start, end))

    await session.execute(delete(DHCPPoolRange).where(
        DHCPPoolRange.source_type == "windows_dhcp", DHCPPoolRange.source_id == inst.id,
    ))
    for cidr, start, end in rows:
        session.add(DHCPPoolRange(
            source_type="windows_dhcp", source_id=inst.id, source_name=inst.name,
            subnet_cidr=cidr, start_ip=start, end_ip=end,
            family=6 if ":" in start else 4, source="windows", synced_at=now,
        ))
    return len(rows)


async def sync_leases(session: AsyncSession, inst: WindowsDhcpServer) -> int:
    """把租約標記到「既有」IP 上（in_dhcp_lease + MAC/主機名稱），**不自動新建 IP**。

    與 OPNsense / pfSense 的行為一致；撤銷同樣只在有設 scope_subnet_ids 時做，
    避免多台 DHCP 在全域範圍互相清掉對方的標記。
    """
    from app.services.arp_precedence import consider_mac
    from app.services.hostname import apply_observation

    cli = _client(inst)
    scopes = await asyncio.to_thread(cli.get_scopes)
    scope_ids = list(inst.scope_subnet_ids) if inst.scope_subnet_ids else None

    seen = 0
    leased: set[str] = set()
    for sc in scopes:
        sid = _as_str(sc.get("ScopeId"))
        if not sid:
            continue
        try:
            leases = await asyncio.to_thread(cli.get_leases, sid)
        except WindowsDhcpError:
            continue   # 單一 scope 失敗不拖垮整批
        for ls in leases:
            ip_text = _as_str(ls.get("IPAddress"))
            if not ip_text:
                continue
            leased.add(ip_text)
            stmt = select(IPAddress).where(func.host(IPAddress.ip) == ip_text)
            if scope_ids:
                stmt = stmt.where(IPAddress.subnet_id.in_(scope_ids))
            # 重疊網段同 IP 可能多筆 → 取一筆即可（見已知地雷 #7）
            ipa = (await session.execute(stmt.limit(1))).scalars().first()
            if ipa is None:
                continue
            ipa.in_dhcp_lease = True
            mac = _norm_mac(ls.get("ClientId"))
            if mac:
                await consider_mac(session, ip=ipa, mac=mac, source="windows_dhcp")
            hostname = _as_str(ls.get("HostName"))
            if hostname:
                await apply_observation(
                    session, ip=ipa, source="windows_dhcp", hostname=hostname.split(".")[0],
                )
            seen += 1

    if scope_ids:
        stmt2 = sa_update(IPAddress).where(
            IPAddress.subnet_id.in_(scope_ids), IPAddress.in_dhcp_lease.is_(True))
        if leased:
            stmt2 = stmt2.where(func.host(IPAddress.ip).notin_(leased))
        await session.execute(stmt2.values(in_dhcp_lease=False))
    return seen


async def sync_instance(session: AsyncSession, inst: WindowsDhcpServer) -> dict[str, int]:
    """跑此實例所有啟用的同步；設定 last_sync_at / last_error。"""
    counts: dict[str, int] = {}
    if inst.sync_scopes:
        counts["scopes"] = await sync_scopes(session, inst)
    if inst.sync_leases:
        counts["leases"] = await sync_leases(session, inst)
    inst.last_sync_at = datetime.now(UTC)
    inst.last_error = None
    return counts
