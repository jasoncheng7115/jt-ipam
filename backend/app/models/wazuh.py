"""Wazuh 整合 model（XDR / SIEM agent inventory）。

- WazuhInstance：Wazuh manager / API 實例（雙欄密碼 AES-GCM）
- WazuhAgent：每次 sync 從 /agents 抓的代理；IP 對映到 jt-ipam IPAddress
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WazuhInstance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "wazuh_instances"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    api_url: Mapped[str] = mapped_column(Text, nullable=False)

    api_user: Mapped[str] = mapped_column(String(128), nullable=False)
    api_password_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_password_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    description: Mapped[str | None] = mapped_column(Text)


class WazuhAgent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "wazuh_agents"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wazuh_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[str] = mapped_column(String(16), nullable=False)   # Wazuh ID（zero-padded "001"）

    name: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET, index=True)
    register_ip: Mapped[str | None] = mapped_column(INET)
    status: Mapped[str | None] = mapped_column(String(32))   # active / disconnected / pending / never_connected
    os_platform: Mapped[str | None] = mapped_column(String(64))
    os_version: Mapped[str | None] = mapped_column(String(64))
    agent_version: Mapped[str | None] = mapped_column(String(64))
    group: Mapped[str | None] = mapped_column(Text)
    node_name: Mapped[str | None] = mapped_column(String(64))

    last_keep_alive: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 對映到 jt-ipam IPAddress（如 IP 對得上）
    jt_ipam_address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_addresses.id", ondelete="SET NULL"),
    )

    # 漏洞掃描：上次 vulnerability summary 抓回來的 critical 數
    cve_critical_count: Mapped[int | None] = mapped_column(Integer)
    cve_high_count: Mapped[int | None] = mapped_column(Integer)
    cve_summary_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("instance_id", "agent_id", name="wazuh_agent_unique"),
    )
