"""Checkmk 整合 model。

跟 Zabbix 同一個定位：**監控面補充**。Checkmk 給的是存活狀態、監控涵蓋落差、主機名稱與停機排程；
只讀監控端點（livestatus），不碰「設定」。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import DateTime as SADateTime
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CheckmkInstance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Checkmk 網站（site）的 REST API 連線。"""

    __tablename__ = "checkmk_instances"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    #: 網站網址（瀏覽器上看到的 https://主機/網站名稱）；API 路徑由程式補
    api_url: Mapped[str] = mapped_column(Text, nullable=False)
    #: 自動化帳號（automation user）；密碼是它的 automation secret，AES-GCM 加密
    api_user: Mapped[str] = mapped_column(String(128), nullable=False)
    api_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    api_secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: 重疊網段安全：限定比對範圍（空＝全域）。與其他整合同一個機制
    scope_subnet_ids: Mapped[list[Any] | None] = mapped_column(JSONB)
    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(SADateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)


class CheckmkHost(Base, UUIDPrimaryKeyMixin):
    """Checkmk 主機鏡像（可重新同步的資料）。"""

    __tablename__ = "checkmk_hosts"
    __table_args__ = (
        UniqueConstraint("instance_id", "host_name", name="uq_checkmk_host_instance_name"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("checkmk_instances.id", ondelete="CASCADE"),
        nullable=False, index=True)
    host_name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: 分散式監控時主機所在的網站
    site: Mapped[str | None] = mapped_column(String(64))
    alias: Mapped[str | None] = mapped_column(String(255))
    #: up | down | unreachable | pending（還沒檢查過）
    state: Mapped[str | None] = mapped_column(String(16))
    #: 主動檢查關掉時狀態會停在舊值，不能當成此刻可達
    checks_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: 停機排程（scheduled downtime）中
    downtime: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ip: Mapped[str | None] = mapped_column(INET)
    addresses: Mapped[list[Any] | None] = mapped_column(JSONB)
    folder: Mapped[str | None] = mapped_column(String(512))
    groups: Mapped[list[Any] | None] = mapped_column(JSONB)
    labels: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[str | None] = mapped_column(Text)
    last_check: Mapped[datetime | None] = mapped_column(SADateTime(timezone=True))
    jt_ipam_address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ip_addresses.id", ondelete="SET NULL"), index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(SADateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), server_default=func.now(), nullable=False)
