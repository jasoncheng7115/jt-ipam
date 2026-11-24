"""Subnet（子網）— phpIPAM 對齊；CIDR 用 PG 原生型別。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import CIDR, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Subnet(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "subnets"

    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    master_subnet_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subnets.id", ondelete="SET NULL"),
        index=True,
    )

    cidr: Mapped[str] = mapped_column(CIDR, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    vlan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vlans.id", ondelete="SET NULL"),
    )
    vrf_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vrfs.id", ondelete="SET NULL"),
    )

    is_pool: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_full: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # phpIPAM 對齊欄位
    gateway: Mapped[str | None] = mapped_column(INET)
    dns_servers: Mapped[str | None] = mapped_column(Text)   # 逗號分隔多個
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="SET NULL"), index=True,
    )
    # 歸檔：非 NULL = 已歸檔（保留資料但不顯示、不掃描；重疊檢查忽略已歸檔）
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    # 是否納入 AI 巡檢。預設納入 —— 新增的網段自動被看顧，而不是要記得去開。
    # 可在子網路編輯頁逐個切換，也可在 管理 → LLM / AI 一次挑選。兩處寫的是同一個欄位。
    ai_audit_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False)

    # 是否納入異常偵測。預設納入；訪客／實驗網段這類「本來就會亂」的可以關掉，
    # 免得把真正該看的東西淹沒。
    anomaly_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False)

    scan_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scan_method: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        server_default=text("ARRAY['icmp']::varchar[]"),
        nullable=False,
    )

    threshold_pct: Mapped[int | None] = mapped_column(Integer)
    auto_dns: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    scan_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scan_agents.id", ondelete="SET NULL"),
    )
    #: 掃描範圍（0205）：auto（4,096 個以內全掃，更大的改成已知位址每輪掃＋其餘背景探索）／all（一律全掃）／
    #: known（只掃已知位址）／ranges（只掃這個子網路的位址範圍）。規則在 services/scan_plan.py
    scan_scope: Mapped[str] = mapped_column(String(16), default="auto", server_default="auto", nullable=False)
    #: 偵測強度（0249）：normal（照舊）／gentle（對端點防護友善：不做 OS 指紋、放慢、只看少數埠、不跑 SMB 腳本；
    #: 客戶的 Symantec Endpoint Protection 把一般強度的 OS 偵測當成攻擊封鎖）。只有 gentle 才帶給代理
    scan_intensity: Mapped[str] = mapped_column(String(16), default="normal", server_default="normal", nullable=False)

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        index=True,
    )

    # 主控台的連線出口（issue #24）：空＝直連；IP 上的設定會覆寫這一欄
    jump_host_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jump_hosts.id", ondelete="SET NULL"), index=True,
    )
    # 或經由掃描代理中繼（issue #24 階段二，0175）：必須是這個子網路的掃描代理；與 jump_host_id 只能擇一
    console_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scan_agents.id", ondelete="SET NULL"), index=True,
    )

    custom_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_subnets_cidr_gist", "cidr", postgresql_using="gist"),
        CheckConstraint("jump_host_id IS NULL OR console_agent_id IS NULL",
                        name="subnet_console_egress_one"),
        CheckConstraint("scan_scope IN ('auto','all','known','ranges')", name="ck_subnets_scan_scope"),
        CheckConstraint("scan_intensity IN ('normal','gentle')", name="ck_subnets_scan_intensity"),
    )
