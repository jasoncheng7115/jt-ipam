"""NAT 模組（phpIPAM 招牌）。"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class NATTranslation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "nat_translations"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # one_to_one / many_to_one / port_forward
    src_ip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_addresses.id", ondelete="SET NULL"),
    )
    dst_ip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_addresses.id", ondelete="SET NULL"),
    )
    src_port: Mapped[int | None] = mapped_column(Integer)
    dst_port: Mapped[int | None] = mapped_column(Integer)
    # 來源介面（OPNsense NAT 規則套用的 interface，如 wan/lan）
    src_interface: Mapped[str | None] = mapped_column(String(64))
    protocol: Mapped[str] = mapped_column(String(8), default="any", nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
    )
    description: Mapped[str | None] = mapped_column(Text)

    # ── OPNsense 規則完整欄位（對齊防火牆 NAT port-forward）──
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
    no_rdr: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
    ip_version: Mapped[str] = mapped_column(String(8), default="inet", nullable=False, server_default="inet")  # inet / inet6
    src_not: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
    dst_not: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
    src_port_to: Mapped[int | None] = mapped_column(Integer)   # 來源埠範圍結尾（src_port 為起）
    dst_port_to: Mapped[int | None] = mapped_column(Integer)   # 目的埠範圍結尾
    log: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
    category: Mapped[str | None] = mapped_column(String(128))
    nat_reflection: Mapped[str | None] = mapped_column(String(16))   # default / enable / disable
    pool_options: Mapped[str | None] = mapped_column(String(32))
    filter_rule: Mapped[str | None] = mapped_column(String(128))     # 關聯的 filter rule 名稱 / id
    # alias 參考（OPNsense 規則常以 alias 名稱表示來源/目的/埠）→ 存名稱以便連到 alias 內容
    src_alias: Mapped[str | None] = mapped_column(String(64))
    dst_alias: Mapped[str | None] = mapped_column(String(64))
    src_port_alias: Mapped[str | None] = mapped_column(String(64))
    dst_port_alias: Mapped[str | None] = mapped_column(String(64))
    redirect_alias: Mapped[str | None] = mapped_column(String(64))

    # 來源追蹤：來自哪個外部系統（同步用 upsert 鍵 + 表示「OPNsense 蓋舊的」）
    # source_origin 形式：
    #   "manual"             — UI 手動建的
    #   "phpipam"            — phpIPAM 匯入
    #   "opnsense:<fw_uuid>" — 從特定 OPNsense 防火牆拉的
    source_origin: Mapped[str | None] = mapped_column(String(64), index=True)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)

    __table_args__ = (
        CheckConstraint(
            "type IN ('one_to_one','many_to_one','port_forward')",
            name="nat_type_valid",
        ),
        CheckConstraint(
            "protocol IN ('tcp','udp','any','icmp','esp','gre','tcp/udp')",
            name="nat_protocol_valid",
        ),
        CheckConstraint(
            "(src_port IS NULL OR src_port BETWEEN 1 AND 65535) "
            "AND (dst_port IS NULL OR dst_port BETWEEN 1 AND 65535)",
            name="nat_port_range",
        ),
        # 同源 + 同 external_id 唯一（同步 upsert key）；origin/external 都允許 NULL 用於 manual
        UniqueConstraint("source_origin", "external_id", name="nat_origin_external_unique"),
    )
