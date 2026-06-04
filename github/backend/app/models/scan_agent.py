"""Scan Agent — 多點掃描代理。

設計：
- 主機跨網段掃描受防火牆限制，把 agent 部署到目標網段；本機向 agent 發任務
- agent_url 是 agent 端的 HTTP API（HTTPS）；走 safe_http
- api_token 加密儲存（aad 綁 agent id）
- Subnet.scan_agent_id：若設定，掃描走 agent；否則走本機 ICMP（現有 Phase 1 行為）

Phase 1：model + CRUD + Subnet 關聯欄位；agent 通訊協定 stub 留 Phase 2。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ScanAgent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scan_agents"

    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    # push 模型：agent 主動連 server，server 不需要 agent_url（保留給舊 pull 模型相容）
    agent_url: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # AES-GCM 加密的 token（舊 pull 模型用）
    api_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    api_token_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)

    # push 模型：enrollment key 的 sha256（agent 帶 key 連進來時比對；明文只在建立時回傳一次）
    enroll_key_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    agent_version: Mapped[str | None] = mapped_column(String(32))  # agent 連上來回報的版本
