"""防火牆整合 model（目前：OPNsense）。

- OPNsenseFirewall：OPNsense 實例（API key/secret 加密儲存，aad 綁 id）
- OPNsenseAliasMapping：把 jt-ipam 的 Section/Subnet/標籤 → OPNsense alias
  的對映；同步時把選定範圍的 IP 列表推到 alias content
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OPNsenseFirewall(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """OPNsense 防火牆實例。"""

    __tablename__ = "opnsense_firewalls"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    api_url: Mapped[str] = mapped_column(Text, nullable=False)

    api_key_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_key_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_secret_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_secret_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    description: Mapped[str | None] = mapped_column(Text)


class OPNsenseAliasMapping(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """jt-ipam 範圍 → OPNsense alias 的同步規則。

    `selector` 描述要送哪些 IP 進 alias，例如：
      {"type": "section", "section_id": "..."}
      {"type": "subnet", "subnet_id": "..."}
      {"type": "tag", "tag": "wifi-guest"}
      {"type": "custom_field", "field": "role", "value": "monitoring"}
    """

    __tablename__ = "opnsense_alias_mappings"

    firewall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opnsense_firewalls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias_name: Mapped[str] = mapped_column(String(64), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(32), default="host", nullable=False)
    selector: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]

    direction: Mapped[str] = mapped_column(String(8), default="push", nullable=False)
    # push  = jt-ipam 寫到 OPNsense
    # pull  = 把 OPNsense alias 讀回 jt-ipam（紀錄用）
    # both

    last_alias_uuid: Mapped[str | None] = mapped_column(String(64))   # OPNsense alias uuid
    last_synced_count: Mapped[int | None] = mapped_column(Integer)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "firewall_id", "alias_name", name="opnsense_alias_mapping_unique",
        ),
    )
