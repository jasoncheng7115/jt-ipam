"""Customer / 管理單位 — 用於把資源歸屬到客戶 / 部門 / 租戶。

對齊 phpIPAM 的 customers 概念，但 jt-ipam 同時允許掛到
subnet / section / ip_address / device（phpIPAM 只支援 section/subnet）。
"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Customer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "customers"

    # 內部唯一 slug；建議用英文短名
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # 顯示用全名（"AAA 股份有限公司"）
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    # 聯絡資訊
    contact: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
