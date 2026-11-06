"""Device — 簡潔設備清單。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Device(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "devices"

    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    fqdn: Mapped[str | None] = mapped_column(Text)   # 完整網域名稱（如 sw1.dc.example.com）
    primary_ip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_addresses.id", ondelete="SET NULL", use_alter=True),
    )
    type: Mapped[str] = mapped_column(String(16), default="other", nullable=False)
    # 類型是誰定的：manual／import／librenms／proxmox／phpipam／auto（自動判斷）；NULL＝沒人定過（舊資料或預設值）。
    # 自動判斷（services/device_type_auto）只碰「other 且沒人定過」或「上次就是自動判斷」的裝置。
    type_source: Mapped[str | None] = mapped_column(String(16))
    vendor: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    serial: Mapped[str | None] = mapped_column(Text)
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="SET NULL"),
    )
    rack_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("racks.id", ondelete="SET NULL"),
    )
    u_position: Mapped[int | None] = mapped_column(Integer)
    u_size: Mapped[int | None] = mapped_column(Integer)
    rack_face: Mapped[str | None] = mapped_column(String(8))   # front / rear（裝在機櫃前面或後面）
    # 一個 U（層架則是一層）的橫向位置：起始格 + 跨幾格，網格共 RACK_SLOTS(60) 格
    # （issue #31 要 1/6、issue #30 的層架要 1/5 —— 60 是 1~6 的最小公倍數）。
    # 取代舊的 full/left/right —— 區間模型才表達得出任意並排。預設 (0, 60) 即整列全寬。
    rack_slot: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    rack_slot_span: Mapped[int] = mapped_column(
        Integer, default=60, server_default="60", nullable=False)
    # 一層**之內**的垂直位置：層架的一層淨空高常常放得下兩三台疊起來，而且不一定放滿。
    # 與橫向同一套區間模型（起始格 + 跨幾格），0 是**貼著層板**的那一側 —— 東西是放在
    # 板上的，所以由下往上長。機櫃類一律 (0, 60)＝整列佔滿，行為與改版前相同。
    rack_vslot: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    rack_vslot_span: Mapped[int] = mapped_column(
        Integer, default=60, server_default="60", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        index=True,
    )
    custom_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 裝置群組（堆疊／HA／叢集，0241）：在哪一組、角色、堆疊序號。見 models/device_group.py
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("device_groups.id", ondelete="SET NULL"), index=True)
    group_role: Mapped[str | None] = mapped_column(String(16))
    group_order: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint(
            "type IN ('server','switch','router','firewall','ap','storage','ipmi',"
            "'patch_panel','pdu','ups','workstation','other')",
            name="device_type_valid",
        ),
    )
