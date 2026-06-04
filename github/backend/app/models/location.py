"""Location 與 Rack。"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Location(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "locations"

    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    description: Mapped[str | None] = mapped_column(Text)
    # 所屬單位 / 客戶（管理單位）
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        index=True,
    )
    # 機房平面圖底圖（上傳檔相對 upload_dir 的路徑）。Location 同時當「機房」用。
    floor_plan_path: Mapped[str | None] = mapped_column(Text)


class Rack(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "racks"

    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="SET NULL"),
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    u_height: Mapped[int] = mapped_column(Integer, default=42, nullable=False)
    # 實體尺寸（mm）：機房平面圖用來把機櫃方框依真實腳印按比例呈現。null = 用預設。
    width_mm: Mapped[int | None] = mapped_column(Integer)
    depth_mm: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    # 排序編號：多機櫃並排顯示時，編號小的排左邊（同編號再依名稱）。null 視為很大、排最後。
    seq: Mapped[int | None] = mapped_column(Integer, index=True)
    # U 編號方向：top-down＝最上面是最大 U（標準機櫃）；bottom-up＝最上面是 U1。
    numbering: Mapped[str] = mapped_column(
        String(16), default="top-down", server_default="top-down", nullable=False,
    )
    # 此機櫃示意圖呈現的面：front＝正面、rear＝背面。
    face: Mapped[str] = mapped_column(
        String(8), default="front", server_default="front", nullable=False,
    )
    # 在機房平面圖上的位置（0..1 的比例座標，與底圖解析度無關）。null = 尚未擺放。
    pos_x: Mapped[float | None] = mapped_column(Numeric(6, 5))
    pos_y: Mapped[float | None] = mapped_column(Numeric(6, 5))
    # 在平面圖上的旋轉角度（任意度數），讓機櫃方框對齊現場朝向。
    pos_rot: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    # 在平面圖上的方框大小（0..1 比例，相對底圖寬/高）；null = 用預設大小。
    pos_w: Mapped[float | None] = mapped_column(Numeric(6, 5))
    pos_h: Mapped[float | None] = mapped_column(Numeric(6, 5))
