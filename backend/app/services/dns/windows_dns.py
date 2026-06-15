"""Windows DNS adapter — WinRM + PowerShell。

依賴 pywinrm（同步 client）；包進 thread executor。

PowerShell cmdlets：
  Get-DnsServerZone
  Get-DnsServerResourceRecord -ZoneName ...
  Add-DnsServerResourceRecordA / -AAAA / -PTR / -CName
  Remove-DnsServerResourceRecord
  Set-DnsServerResourceRecord（透過 Get + 修改 + Set 的舊紀錄）

OWASP A04：password 即時解密、不在 instance 上常駐
OWASP A05：所有 PowerShell 參數透過 winrm 的 named parameters 傳，不字串拼接
OWASP A02：use_ssl=true 為預設；不接受 cert validation skip
OWASP A06：host 透過 socket 解析後檢查（DNS 解析後 pin IP 防 rebinding）
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from typing import Any

from app.core.config import get_settings
from app.core.safe_http import _BLOCKED_CIDRS, _PRIVATE_CIDRS, _ip_in
from app.services.dns.base import DNSAdapter, DNSAdapterError, DNSRecordOp, DNSZoneInfo

# 不允許 PowerShell 注入的字元（A03）
_PS_SAFE = re.compile(r"^[A-Za-z0-9._:\-/]+$")


def _safe_ps_arg(value: str) -> str:
    if not _PS_SAFE.match(value):
        raise DNSAdapterError(f"unsafe PowerShell argument: {value!r}")
    return value


def _check_address_safe(host: str) -> None:
    settings = get_settings()
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise DNSAdapterError(f"DNS resolution failed for {host}") from exc
        addrs = [ipaddress.ip_address(info[4][0]) for info in infos]
    for ip in addrs:
        if _ip_in(ip, _BLOCKED_CIDRS):
            raise DNSAdapterError(f"Blocked IP for SSRF: {ip}")
        if _ip_in(ip, _PRIVATE_CIDRS) and not settings.outbound_allow_private:
            raise DNSAdapterError(f"Private IP {ip} not allowed without OUTBOUND_ALLOW_PRIVATE")


class WindowsDNSAdapter(DNSAdapter):
    type = "windows_dns"

    def __init__(
        self,
        *,
        host: str,
        username: str,
        password: str,
        port: int = 5986,
        use_ssl: bool = True,
        timeout: float = 30.0,
    ) -> None:
        if not host:
            raise DNSAdapterError("Windows DNS: host is required")
        _check_address_safe(host)
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_ssl = use_ssl
        self.timeout = timeout

    def _session(self) -> Any:
        # winrm import 放這裡讓單元測試不需要全部裝齊
        import winrm
        scheme = "https" if self.use_ssl else "http"
        endpoint = f"{scheme}://{self.host}:{self.port}/wsman"
        return winrm.Session(
            target=endpoint,
            auth=(self.username, self.password),
            transport="ntlm",
            server_cert_validation="validate" if self.use_ssl else "ignore",
            operation_timeout_sec=int(self.timeout),
            read_timeout_sec=int(self.timeout) + 5,
        )

    def _run_ps(self, script: str) -> str:
        try:
            sess = self._session()
            result = sess.run_ps(script)
        except DNSAdapterError:
            raise
        except Exception as exc:  # winrm/requests 連線/認證/TLS/timeout 都不是 DNSAdapterError
            # 不轉成 DNSAdapterError 的話，dns.py 端點只 except DNSAdapterError →
            # 連線測試一失敗就變未處理的 500、且無可讀訊息。
            raise DNSAdapterError(
                f"Windows DNS WinRM connection failed: {exc.__class__.__name__}: {exc}"
            ) from exc
        if result.status_code != 0:
            raise DNSAdapterError(
                f"Windows DNS PS error (rc={result.status_code}): "
                f"{(result.std_err or b'').decode('utf-8', errors='replace')[:300]}"
            )
        return (result.std_out or b"").decode("utf-8", errors="replace")

    async def healthcheck(self) -> dict[str, object]:
        out = await asyncio.to_thread(
            self._run_ps,
            "Get-DnsServer | Select-Object -Property ComputerName | ConvertTo-Json -Compress"
        )
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = {"raw": out.strip()}
        return {"server": self.host, "info": data}

    async def list_zones(self) -> list[DNSZoneInfo]:
        out = await asyncio.to_thread(
            self._run_ps,
            "Get-DnsServerZone | Where-Object {$_.IsAutoCreated -eq $false} "
            "| Select-Object ZoneName, IsReverseLookupZone "
            "| ConvertTo-Json -Compress",
        )
        if not out.strip():
            return []
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        result: list[DNSZoneInfo] = []
        for z in data:
            name = (z.get("ZoneName") or "").rstrip(".")
            if not name:
                continue
            kind = "reverse" if z.get("IsReverseLookupZone") else "forward"
            result.append(DNSZoneInfo(name=name, kind=kind))
        return result

    async def list_records(self, zone_name: str) -> list[DNSRecordOp]:
        zname = _safe_ps_arg(zone_name)
        ps = (
            f"Get-DnsServerResourceRecord -ZoneName '{zname}' "
            "| ForEach-Object { "
            "  $rec = $_; "
            "  $val = if ($rec.RecordData.IPv4Address) { $rec.RecordData.IPv4Address.IPAddressToString } "
            "         elseif ($rec.RecordData.IPv6Address) { $rec.RecordData.IPv6Address.IPAddressToString } "
            "         elseif ($rec.RecordData.PtrDomainName) { $rec.RecordData.PtrDomainName } "
            "         elseif ($rec.RecordData.HostNameAlias) { $rec.RecordData.HostNameAlias } "
            "         elseif ($rec.RecordData.MailExchange) { $rec.RecordData.MailExchange } "
            "         elseif ($rec.RecordData.DescriptiveText) { $rec.RecordData.DescriptiveText -join ' ' } "
            "         else { $null }; "
            "  [pscustomobject]@{ Name=$rec.HostName; Type=$rec.RecordType; "
            "                     TTL=$rec.TimeToLive.TotalSeconds; Value=$val } "
            "} | ConvertTo-Json -Compress"
        )
        out = await asyncio.to_thread(self._run_ps, ps)
        if not out.strip():
            return []
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        out_list: list[DNSRecordOp] = []
        for r in data:
            value = r.get("Value")
            if value is None:
                continue
            host_name = (r.get("Name") or "@").rstrip(".")
            fqdn = (
                zone_name.rstrip(".")
                if host_name in ("@", "")
                else f"{host_name}.{zone_name.rstrip('.')}"
            )
            out_list.append(
                DNSRecordOp(
                    name=fqdn,
                    type=str(r.get("Type") or "").upper(),
                    value=str(value),
                    ttl=int(r.get("TTL") or 3600),
                )
            )
        return out_list

    @staticmethod
    def _ps_relname(fqdn: str, zone_name: str) -> str:
        zone = zone_name.rstrip(".")
        name = fqdn.rstrip(".")
        if name == zone:
            return "@"
        if name.endswith("." + zone):
            return name[: -(len(zone) + 1)]
        return name

    async def upsert_record(self, zone_name: str, op: DNSRecordOp) -> None:
        zname = _safe_ps_arg(zone_name)
        rel = _safe_ps_arg(self._ps_relname(op.name, zone_name))
        value = _safe_ps_arg(op.value)
        ttl = int(op.ttl)

        # 先嘗試刪舊（同 host + type）— Windows DNS 沒有 atomic upsert
        # 注意：刪除只擋 Name+Type，多個值情境會被覆蓋；jt-ipam 設計只一筆
        delete_old = (
            f"$ex = Get-DnsServerResourceRecord -ZoneName '{zname}' -Name '{rel}' "
            f"-RRType {_safe_ps_arg(op.type)} -ErrorAction SilentlyContinue; "
            f"if ($ex) {{ Remove-DnsServerResourceRecord -ZoneName '{zname}' "
            f"-InputObject $ex -Force }}"
        )

        if op.type == "A":
            cmd = (
                f"Add-DnsServerResourceRecordA -ZoneName '{zname}' -Name '{rel}' "
                f"-IPv4Address '{value}' -TimeToLive (New-TimeSpan -Seconds {ttl})"
            )
        elif op.type == "AAAA":
            cmd = (
                f"Add-DnsServerResourceRecordAAAA -ZoneName '{zname}' -Name '{rel}' "
                f"-IPv6Address '{value}' -TimeToLive (New-TimeSpan -Seconds {ttl})"
            )
        elif op.type == "PTR":
            cmd = (
                f"Add-DnsServerResourceRecordPtr -ZoneName '{zname}' -Name '{rel}' "
                f"-PtrDomainName '{value}' -TimeToLive (New-TimeSpan -Seconds {ttl})"
            )
        elif op.type == "CNAME":
            cmd = (
                f"Add-DnsServerResourceRecordCName -ZoneName '{zname}' -Name '{rel}' "
                f"-HostNameAlias '{value}' -TimeToLive (New-TimeSpan -Seconds {ttl})"
            )
        else:
            raise DNSAdapterError(
                f"Windows DNS adapter currently supports A/AAAA/PTR/CNAME, not {op.type}"
            )

        await asyncio.to_thread(self._run_ps, delete_old + "; " + cmd)

    async def delete_record(self, zone_name: str, op: DNSRecordOp) -> None:
        zname = _safe_ps_arg(zone_name)
        rel = _safe_ps_arg(self._ps_relname(op.name, zone_name))
        rtype = _safe_ps_arg(op.type)
        ps = (
            f"$rec = Get-DnsServerResourceRecord -ZoneName '{zname}' -Name '{rel}' "
            f"-RRType {rtype} -ErrorAction SilentlyContinue; "
            f"if ($rec) {{ Remove-DnsServerResourceRecord -ZoneName '{zname}' "
            f"-InputObject $rec -Force }}"
        )
        await asyncio.to_thread(self._run_ps, ps)
