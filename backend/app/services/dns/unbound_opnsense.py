"""OPNsense Unbound REST API adapter — Phase 2 / Batch U。"""

from __future__ import annotations

from app.services.dns.base import DNSAdapter, DNSAdapterError, DNSRecordOp, DNSZoneInfo


class UnboundOPNsenseAdapter(DNSAdapter):
    type = "unbound_opnsense"

    def __init__(self, *, api_url: str, api_key: str, api_secret: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret

    async def healthcheck(self) -> dict[str, object]:
        raise DNSAdapterError("Unbound (OPNsense) adapter scheduled for Batch U")

    async def list_zones(self) -> list[DNSZoneInfo]:
        raise DNSAdapterError("Unbound (OPNsense) adapter scheduled for Batch U")

    async def list_records(self, zone_name: str) -> list[DNSRecordOp]:
        raise DNSAdapterError("Unbound (OPNsense) adapter scheduled for Batch U")

    async def upsert_record(self, zone_name: str, op: DNSRecordOp) -> None:
        raise DNSAdapterError("Unbound (OPNsense) adapter scheduled for Batch U")

    async def delete_record(self, zone_name: str, op: DNSRecordOp) -> None:
        raise DNSAdapterError("Unbound (OPNsense) adapter scheduled for Batch U")
