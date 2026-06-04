"""DHCP 發放範圍（pool range）— 從 DHCP server（OPNsense Kea）同步回來。

一個子網路可能設定多段 pool，故以 (firewall, 起, 迄) 多列儲存。
IP 落在任一範圍內 → 在 IP 清單標示為 DHCP（動態發放區）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DHCPPoolRange(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "dhcp_pool_ranges"

    firewall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opnsense_firewalls.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    subnet_cidr: Mapped[str] = mapped_column(String(64), nullable=False)   # 來源子網路
    start_ip: Mapped[str] = mapped_column(String(64), nullable=False)      # 範圍起（含）
    end_ip: Mapped[str] = mapped_column(String(64), nullable=False)        # 範圍迄（含）
    family: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="kea", nullable=False)  # kea / isc
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
