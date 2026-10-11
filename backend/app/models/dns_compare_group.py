"""DNS 比對群組：內容應該一致的 DNS 伺服器（主從、AD 整合、多主，伺服器之間自己複寫）放在同一組
（2026-10-10 使用者需求；名稱刻意不叫「同步群組」：jt-ipam 不會在伺服器之間同步紀錄，只比對拉回來的資料）。

- 顯示時，同一組裡完全相同的紀錄合併成一筆（DNS 紀錄頁、異常偵測、IP 變更評估），寫出哪幾台都有
- 每台拉取完，用已經拉取下來的紀錄比對組內每一台（不另外連 DNS），不一樣的記在
  `dns_compare_group_diffs`，持續超過寬限時間才算確認、才發通知（吸收伺服器之間的複寫延遲）

比對規則在 services/dns_compare.py。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DNSCompareGroup(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "dns_compare_groups"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    #: 確認不一致時用通知功能發告警（事件 dns_compare_mismatch／dns_compare_resolved）
    notify_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: 差異要持續這麼久才算確認：伺服器之間的複寫本來就有延遲，剛改完的紀錄會短暫只在一台
    grace_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: ok（一致）／mismatch（有已確認的差異）／pending（有差異、還在寬限期內）／
    #: incomplete（有成員還沒拉取成功，先不判定）／single（成員不到兩台）／
    #: incompatible（Unbound 和有 zone 的伺服器混在一起，不比對）
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_message: Mapped[str | None] = mapped_column(Text)
    #: 已經為目前這批差異發過告警（全部恢復一致時清掉，並發一則恢復通知）
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: 不比對的 zone（正規化後的名稱；空＝全部都比）：主從只複寫部分 zone、或某台另外放自己的 zone 時用（0204）
    excluded_zones: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)

    __table_args__ = (
        CheckConstraint("grace_minutes >= 0 AND grace_minutes <= 1440", name="ck_dns_compare_groups_grace"),
    )


class DNSCompareGroupDiff(Base, UUIDPrimaryKeyMixin):
    """組內各台不一樣的一筆：整個 zone 只在部分成員上（kind=zone），或某筆紀錄只在部分成員上（kind=record）。"""

    __tablename__ = "dns_compare_group_diffs"

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dns_compare_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    zone: Mapped[str] = mapped_column(String(255), nullable=False)
    #: kind=zone 時 name／type／value 都是空字串（唯一鍵要有值）
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    type: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: DNS 伺服器 id 字串陣列
    present_on: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    missing_on: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    #: kind|zone|name|type|value 的 SHA-256：唯一鍵用它（TXT 的值可能很長，直接進索引會超過長度上限）
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: 確認的時間：每一台「沒有」的伺服器都在 first_seen_at＋寬限時間之後重新拉取過、仍然沒有（0204）。
    #: first_seen_at 是「有這筆的伺服器」拉取的時間（資料的時間），不是比對當下的時鐘
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ('zone','record')", name="ck_dns_compare_group_diffs_kind"),
        UniqueConstraint("group_id", "key_hash", name="uq_dns_compare_group_diff"),
    )
