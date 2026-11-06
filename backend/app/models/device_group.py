"""裝置群組：堆疊／HA 主備／HA 雙主動／叢集（客戶 2026-11-05：兩台 N2048P 堆疊共用一個管理 IP）。

成員仍是各自的實體裝置（序號、機櫃位置、連接埠都在自己身上），`devices.group_id`／`group_role`／`group_order`
記它在哪一組、扮演什麼角色。

**共用 IP（方案 B）**：屬於群組（`ip_addresses.device_group_id`），同時 `ip_addresses.device_id` 指向群組的
**代表裝置**（堆疊的主機、HA 目前的主要機）。系統裡約 70 處讀「IP 屬於哪台設備」，沒改成認得群組的地方
看到的就是代表裝置，跟以前一樣，不會把共用 IP 當成「沒連結」而去自動對應、搶走。換代表裝置時共用 IP 跟著轉
（services/device_groups）。整合（LibreNMS 把堆疊當一台）的資料也接到代表裝置。
"""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

#: 群組類型
GROUP_KINDS = ("stack", "ha_active_passive", "ha_active_active", "cluster")
#: 成員角色：堆疊 master／member；HA primary／standby（主備）或 active（雙主動）；叢集 member
GROUP_ROLES = ("master", "member", "primary", "standby", "active")


class DeviceGroup(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "device_groups"

    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="stack")
    description: Mapped[str | None] = mapped_column(Text)
    #: 代表裝置：共用 IP 的 device_id 指向它（成員之一；成員離開時換成剩下的第一台）
    representative_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL", use_alter=True), index=True)

    __table_args__ = (
        CheckConstraint("kind IN ('stack','ha_active_passive','ha_active_active','cluster')",
                        name="device_group_kind_valid"),
    )
