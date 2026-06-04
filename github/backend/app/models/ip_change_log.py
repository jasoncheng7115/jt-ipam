"""IP 位址異動記錄（規格：每筆 IP 的 hostname / mac / arp / 上下線 / 人為編輯都留痕）。

刻意「不」進 audit_log 的 SHA-256 雜湊鏈（app/core/audit.py）：sync 來的 ARP /
上下線是高頻系統事件，塞進雜湊鏈會讓 advisory-lock 序列化變瓶頸、鏈暴漲。
這裡是純查詢用的高頻事件表，靠 index 撐搜尋/篩選。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin

# 事件類型（前端篩選 dropdown 用同一組）
EVENT_TYPES = (
    "created",          # IP 建立
    "deleted",          # IP 刪除
    "hostname_changed",
    "mac_changed",
    "state_changed",
    "online",           # 失聯→上線
    "offline",          # 上線→失聯
    "arp_changed",      # ARP 對應到的 MAC 變了
    "edited",           # 其它人為欄位編輯（description/owner/switch_port/note...）
)

# 來源（與 IPAddress.discovery_source enum 對齊，外加 system）
CHANGE_SOURCES = (
    "manual", "scanner", "librenms", "dns", "proxmox", "opnsense", "system",
)


class IPChangeLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ip_change_log"

    # IP 刪除後仍想保留歷史 → SET NULL，並另存 ip_text / subnet_id 快照
    ip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_addresses.id", ondelete="SET NULL"),
        index=True,
    )
    subnet_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subnets.id", ondelete="SET NULL"),
        index=True,
    )
    ip_text: Mapped[str] = mapped_column(Text, nullable=False)  # host(ip) 快照

    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    field: Mapped[str | None] = mapped_column(String(32))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="system", nullable=False)

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        # 單一 IP 的歷史依時間倒序撈
        Index("ix_ip_change_log_ip_created", "ip_id", "created_at"),
    )
