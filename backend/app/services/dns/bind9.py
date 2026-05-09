"""BIND 9 adapter — Phase 2：AXFR/IXFR + nsupdate（TSIG）。

依賴 dnspython。Phase 1 留 stub；Batch U 實作。
"""

from __future__ import annotations

from app.services.dns.base import DNSAdapter, DNSAdapterError, DNSRecordOp, DNSZoneInfo


class Bind9Adapter(DNSAdapter):
    type = "bind9"

    def __init__(
        self,
        *,
        server_address: str,
        tsig_keyname: str,
        tsig_secret: str | None,
        tsig_algorithm: str = "hmac-sha256",
        zones: list[str] | None = None,
    ) -> None:
        self.server_address = server_address
        self.tsig_keyname = tsig_keyname
        self.tsig_secret = tsig_secret
        self.tsig_algorithm = tsig_algorithm
        self.zones = zones or []

    async def healthcheck(self) -> dict[str, object]:
        raise DNSAdapterError("BIND 9 adapter scheduled for Batch U")

    async def list_zones(self) -> list[DNSZoneInfo]:
        raise DNSAdapterError("BIND 9 adapter scheduled for Batch U")

    async def list_records(self, zone_name: str) -> list[DNSRecordOp]:
        raise DNSAdapterError("BIND 9 adapter scheduled for Batch U")

    async def upsert_record(self, zone_name: str, op: DNSRecordOp) -> None:
        raise DNSAdapterError("BIND 9 adapter scheduled for Batch U")

    async def delete_record(self, zone_name: str, op: DNSRecordOp) -> None:
        raise DNSAdapterError("BIND 9 adapter scheduled for Batch U")
