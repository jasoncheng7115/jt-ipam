"""DNS provider abstractions + 各家實作。

使用方式：
    from app.services.dns import get_adapter
    adapter = await get_adapter(session, server)
    zones = await adapter.list_zones()
    await adapter.upsert_record(zone, name="host01", type="A", value="10.0.0.1", ttl=300)
"""

from app.services.dns.base import DNSAdapter, DNSAdapterError, DNSRecordOp
from app.services.dns.factory import get_adapter

__all__ = ["DNSAdapter", "DNSAdapterError", "DNSRecordOp", "get_adapter"]
