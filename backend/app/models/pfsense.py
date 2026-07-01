"""pfSense 整合 model（與 OPNsense 分開，獨立資料表 / 設定）。

pfSense CE 2.8.0 無官方內建 REST API；本整合走第三方 pfSense-pkg-RESTAPI 套件
（pfrest.org，base path /api/v2，X-API-Key 標頭認證）。API key 以 AES-GCM 加密（aad 綁 id）。

Phase 1（核心同步）：DHCP leases / ARP → 在關聯子網路內 stamp IP 存活 / MAC / 主機名稱；
別名（alias）唯讀同步。後續再加防火牆規則 / NAT。
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
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PfSenseFirewall(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """pfSense 防火牆實例（pfSense-pkg-RESTAPI / X-API-Key）。"""

    __tablename__ = "pfsense_firewalls"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    api_url: Mapped[str] = mapped_column(Text, nullable=False)  # 例：https://192.0.2.1
    # X-API-Key（AES-GCM 加密；aad 綁實例 id）
    api_key_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_key_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    # 各同步開關（DHCP 預設關：若該台沒開 DHCP、或避免與區網內其他 DHCP 衝突）
    sync_dhcp: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_arp: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_aliases: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_rules: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 對外提供 Graylog DSV（別名→成員、tracker→規則說明）
    expose_dsv: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 同步進來的精簡防火牆規則（檢視 + tracker→descr DSV）
    rules: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]

    # 關聯子網路範圍（留空＝全域 IP 字串比對）；重疊網段時限定比對範圍
    scope_subnet_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text)


class PfSenseSyncedAlias(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """pfSense 別名唯讀快取（給檢視 / 後續 DSV 用）。"""

    __tablename__ = "pfsense_synced_aliases"

    firewall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pfsense_firewalls.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    alias_type: Mapped[str | None] = mapped_column(String(32))   # host / network / port / url …
    members: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    descr: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
