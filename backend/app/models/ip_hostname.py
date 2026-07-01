"""IP hostname 多來源觀測（feature A）。

每個來源（manual/scanner/librenms/dns/proxmox/opnsense）對同一個 IP 各存一筆
hostname；IPAddress.hostname 是依「全域優先序 + 單 IP pin」解析後的有效值。
解析邏輯與優先序設定在 app/services/hostname.py。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin

# 主機名稱觀測來源（此表 source 欄 String(16)、無 CHECK；非 IPAddress.discovery_source）。
# netbios / mdns 由掃描代理分別以 nmblookup / avahi-resolve 取得，是獨立於 scanner(rDNS) 的來源。
HOSTNAME_SOURCES = ("manual", "scanner", "librenms", "dns", "proxmox", "opnsense", "pfsense", "wazuh", "adguard", "netbios", "mdns")


class IPHostnameObservation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ip_hostname_observations"

    ip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_addresses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    hostname: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("ip_id", "source", name="uq_ip_hostname_obs_ip_source"),
    )
