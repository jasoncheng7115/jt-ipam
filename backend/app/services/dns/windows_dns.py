"""Windows DNS（WinRM + PowerShell）adapter — Phase 2 / Batch U。"""

from __future__ import annotations

from app.services.dns.base import DNSAdapter, DNSAdapterError, DNSRecordOp, DNSZoneInfo


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
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_ssl = use_ssl

    async def healthcheck(self) -> dict[str, object]:
        raise DNSAdapterError("Windows DNS adapter scheduled for Batch U")

    async def list_zones(self) -> list[DNSZoneInfo]:
        raise DNSAdapterError("Windows DNS adapter scheduled for Batch U")

    async def list_records(self, zone_name: str) -> list[DNSRecordOp]:
        raise DNSAdapterError("Windows DNS adapter scheduled for Batch U")

    async def upsert_record(self, zone_name: str, op: DNSRecordOp) -> None:
        raise DNSAdapterError("Windows DNS adapter scheduled for Batch U")

    async def delete_record(self, zone_name: str, op: DNSRecordOp) -> None:
        raise DNSAdapterError("Windows DNS adapter scheduled for Batch U")
