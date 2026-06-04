"""IEEE OUI vendor 對照表（給 MAC 自動帶廠商用）。

資料來源：Wireshark 維護的 `manuf` 檔（每月更新一次即可）。
key 是把 MAC 前 24 bits 正規化成 6 位大寫 hex（無分隔符），長度永遠 6。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OUIVendor(Base):
    __tablename__ = "oui_vendors"

    # 前 24 bits 大寫 hex，e.g. "001122"。也涵蓋 MA-L (24-bit) OUI
    prefix: Mapped[str] = mapped_column(String(6), primary_key=True)
    # 短名（e.g. "Cisco"）
    short_name: Mapped[str | None] = mapped_column(Text)
    # 全名（e.g. "Cisco Systems, Inc"）
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # 來源（"wireshark" / "ieee_oui" / manual）
    source: Mapped[str] = mapped_column(String(32), default="wireshark", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_oui_vendors_short_name", "short_name"),
    )
