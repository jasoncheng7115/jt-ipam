"""憑證集中保管 + 派送（cert distribution）model。

設計：
- `Certificate`：一個邏輯憑證身分（如 wildcard-example-com）。
- `CertVersion`：每次上傳一份 bundle（cert/chain/key）。key 以 AES-GCM 加密（key_enc/key_nonce）。
  版本化才能讓 agent 偵測「有沒有比手上更新的」+ 出事可回滾。
- `CertAgent`：裝在目標主機上的派送代理，enroll_key_hash 認證（同 ScanAgent）；
  scope_cert_ids 限定它能取哪些憑證（deny-by-default）。部署清單在 agent 端 YAML，
  server 這邊以 `reported` 收 agent 回報的套用狀態（給後台看全部站台健康度 / 飄移偵測）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Certificate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "certificates"

    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    domains: Mapped[list[str] | None] = mapped_column(ARRAY(Text))  # 目前版本的 SAN

    # 自動抓取來源（none / url / sftp）。帳密/SSH key 走 encrypted_secret(object_type='certificate')
    source_type: Mapped[str] = mapped_column(String(16), server_default=text("'none'"), nullable=False)
    source_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    fetch_interval_seconds: Mapped[int] = mapped_column(
        Integer, server_default=text("86400"), nullable=False)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_fetch_error: Mapped[str | None] = mapped_column(Text)


class CertVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "cert_versions"

    certificate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("certificates.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    serial: Mapped[str | None] = mapped_column(String(128))
    subject: Mapped[str | None] = mapped_column(Text)
    issuer: Mapped[str | None] = mapped_column(Text)
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    domains: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    cert_pem: Mapped[str] = mapped_column(Text, nullable=False)
    chain_pem: Mapped[str | None] = mapped_column(Text)
    # 私鑰：AES-GCM 加密（aad 綁 cert_version:<id>:key）；明文絕不落 DB / log / API
    key_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("certificate_id", "fingerprint_sha256", name="cert_version_unique"),
    )


class CertAgent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cert_agents"

    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    # enrollment key 的 sha256（明文只在建立/輪替時回傳一次；同 ScanAgent）
    enroll_key_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    # deny-by-default：可取的 certificate id 清單；空/None＝不可取任何憑證
    scope_cert_ids: Mapped[list[Any] | None] = mapped_column(JSONB)
    # 對應的 jt-ipam 裝置（代理名稱可點去裝置詳情）；裝置刪除→SET NULL
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), index=True,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_source_ip: Mapped[str | None] = mapped_column(String(64))
    agent_version: Mapped[str | None] = mapped_column(String(32))
    # agent 回報的各部署狀態：list of {cert,profile,fingerprint,not_after,applied_at,status,message,dry_run}
    reported: Mapped[list[Any] | None] = mapped_column(JSONB)
    # 近期回報來源 IP（list of {ip, at}，去重、上限 10、保留 7 天）；多筆不同 IP＝同把 Key 被多台主機共用
    recent_sources: Mapped[list[Any] | None] = mapped_column(JSONB)
