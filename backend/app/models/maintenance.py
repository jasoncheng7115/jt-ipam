"""維護排程（2026-11-01）：維護期間，範圍內的物件不發告警。

- 一次性（`starts_at`～`ends_at`）或每週例行（`weekdays` 的 `start_time`～`end_time`，可跨午夜，一定要有 `valid_until`）
- 範圍存規則（`maintenance_scopes`），不展開成每個 IP：IP、IP 範圍、子網路、區段（含子區段）、裝置（含名下 IP）、
  機櫃、機房地點、單位、整合、掃描代理
- 行為：mute＝只靜音通知（預設，照常偵測與記錄）、mark＝只標示
- 被靜音的告警記在 `maintenance_suppressions`：結束時發摘要、查「這段期間靜音了什麼」
見 app/services/maintenance.py。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

SCOPE_TYPES = ("ip", "ip_range", "subnet", "section", "device", "rack", "location", "customer", "integration",
               "scan_agent")


class MaintenanceWindow(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "maintenance_windows"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    ticket: Mapped[str | None] = mapped_column(String(100))           # 變更單號
    behavior: Mapped[str] = mapped_column(String(8), nullable=False, default="mute")   # mute / mark
    schedule_type: Mapped[str] = mapped_column(String(8), nullable=False, default="once")  # once / weekly
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    weekdays: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))  # 0＝週一 … 6＝週日
    start_time: Mapped[time | None] = mapped_column(Time)
    end_time: Mapped[time | None] = mapped_column(Time)               # 小於等於開始＝跨午夜
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    # 提前結束：一次性直接改 ends_at；每週例行記「這一次」略過到哪個時間
    skip_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 延長：每週例行這一次延到哪個時間（一次性直接改 ends_at）
    extended_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # manual / librenms / change_plan
    source_ref: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    # 排程每輪記下目前這一次是從何時開始生效（沒在維護＝NULL）：用來判斷開始 / 結束並發摘要
    active_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("behavior IN ('mute','mark')", name="ck_maintenance_windows_behavior"),
        CheckConstraint("schedule_type IN ('once','weekly')", name="ck_maintenance_windows_schedule"),
        CheckConstraint(
            "(schedule_type = 'once' AND starts_at IS NOT NULL AND ends_at IS NOT NULL AND ends_at > starts_at) OR "
            "(schedule_type = 'weekly' AND weekdays IS NOT NULL AND start_time IS NOT NULL AND end_time IS NOT NULL "
            "AND valid_until IS NOT NULL)", name="ck_maintenance_windows_shape"),
    )


class MaintenanceScope(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "maintenance_scopes"

    window_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("maintenance_windows.id", ondelete="CASCADE"), nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    integration_kind: Mapped[str | None] = mapped_column(String(32))   # scope_type='integration' 時
    range_start: Mapped[str | None] = mapped_column(INET)              # scope_type='ip_range'
    range_end: Mapped[str | None] = mapped_column(INET)
    label: Mapped[str | None] = mapped_column(String(255))             # 建立當下的顯示名稱（物件刪掉後還看得懂）

    __table_args__ = (
        CheckConstraint("scope_type IN ('ip','ip_range','subnet','section','device','rack','location','customer',"
                        "'integration','scan_agent')", name="ck_maintenance_scopes_type"),
    )


class MaintenanceSuppression(Base, UUIDPrimaryKeyMixin):
    """被維護靜音的告警（每一次維護、每個對象只記一筆）。"""

    __tablename__ = "maintenance_suppressions"

    window_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("maintenance_windows.id", ondelete="CASCADE"), nullable=False, index=True)
    occurrence: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # 哪一次（active_since）
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str | None] = mapped_column(Text)
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (UniqueConstraint("window_id", "occurrence", "key", name="uq_maintenance_suppressions_key"),)
