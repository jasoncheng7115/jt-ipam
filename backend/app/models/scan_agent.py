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

from sqlalchemy import ARRAY, Boolean, DateTime, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
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
    last_source_ip: Mapped[str | None] = mapped_column(String(64))  # agent 連上來的來源 IP

    # 此代理「被允許 / 有能力」執行的探測項目（能力天花板）；預設只 ICMP。
    # 詳見 app/core/scan_probes.py 的目錄。
    enabled_probes: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        server_default=text("ARRAY['icmp']::varchar[]"),
        nullable=False,
    )
    # 各 probe 的執行間隔覆寫（秒）；空 = 用目錄預設。{"os": 86400, ...}
    probe_intervals: Mapped[dict[str, int] | None] = mapped_column(JSONB)
    # agent 回報它「實際裝得起」哪些 probe（有沒有 nmap、能不能 raw socket）→ UI 反灰用
    available_probes: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    # agent 回報的相依工具盤點：[{"name","installed","version"}]（版本資訊頁/掃描代理頁顯示哪些裝了/缺）
    tools: Mapped[list | None] = mapped_column(JSONB)
    # 「立刻執行一次」：admin 按鈕設此時間，代理下次 poll 取走（清空）後本輪所有探測強制到期立即跑
    force_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
