"""DNS adapter 抽象介面。

每個 provider 需實作相同 interface：list_zones / list_records / upsert_record /
delete_record。所有對外請求一律走 safe_http（A10 SSRF 守門）；秘密欄位透過
core.security.decrypt_secret 即時解密，不在 instance 上常駐。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class DNSRecordOp:
    name: str
    type: str
    value: str
    ttl: int = 3600


@dataclass
class DNSZoneInfo:
    name: str
    kind: str  # forward / reverse


class DNSAdapterError(Exception):
    """所有 adapter 失敗的基底例外。"""


class DNSAdapter(abc.ABC):
    """所有 DNS provider 的共同介面。"""

    type: str = "abstract"

    @abc.abstractmethod
    async def healthcheck(self) -> dict[str, object]:
        """連線 / 認證測試；成功回傳 server 摘要資訊。"""

    @abc.abstractmethod
    async def list_zones(self) -> list[DNSZoneInfo]:
        ...

    @abc.abstractmethod
    async def list_records(self, zone_name: str) -> list[DNSRecordOp]:
        ...

    @abc.abstractmethod
    async def upsert_record(self, zone_name: str, op: DNSRecordOp) -> None:
        ...

    @abc.abstractmethod
    async def delete_record(self, zone_name: str, op: DNSRecordOp) -> None:
        ...

    async def close(self) -> None:
        """釋放連線/檔案資源；預設 no-op。"""
        return None
