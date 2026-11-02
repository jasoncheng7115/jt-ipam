"""對外路徑上手動插入的中間設備（客戶許願清單 2026-10-28）。

WAN IP → 防火牆 → 交換器 → 內部主機這條路由系統自動推出（NAT 規則、交換器 MAC 表）；
WAF、負載平衡器、反向代理這類推不出來的設備，讓使用者插在防火牆與交換器之間。掛在**內部主機的 IP** 上，
這個 IP 的每一條對外路徑都會經過它們。
"""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

HOP_KINDS = ("firewall", "waf", "load_balancer", "proxy", "router", "switch", "other")


class IPPathHop(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ip_path_hops"

    address_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ip_addresses.id", ondelete="CASCADE"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="other")
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), index=True)
    label: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("kind IN ('firewall','waf','load_balancer','proxy','router','switch','other')",
                        name="ck_ip_path_hops_kind"),
        CheckConstraint("device_id IS NOT NULL OR label IS NOT NULL", name="ck_ip_path_hops_what"),
    )
