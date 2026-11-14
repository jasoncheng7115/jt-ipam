"""Checkmk 整合：連線設定、主機鏡像、`ip_addresses.last_seen_checkmk`

Revision ID: 0242_checkmk
Revises: 0241_device_groups
Create Date: 2026-11-14

跟 Zabbix 同一個定位（監控面補充）：主機與 IP 的對應、存活狀態、監控涵蓋落差、維護（停機排程）。
只讀監控端點（livestatus），用內建 guest 角色的自動化帳號就夠 —— 那個角色讀不到「設定」，
讀設定的端點會靜靜回 0 筆，所以不用它。自動化密碼以 AES-GCM 加密存放，欄位與其他整合一致。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

revision: str = "0242_checkmk"
down_revision: str | None = "0241_device_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checkmk_instances",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        # 網站網址（https://主機/網站名稱），API 路徑由程式補
        sa.Column("api_url", sa.Text(), nullable=False),
        sa.Column("api_user", sa.String(128), nullable=False),
        sa.Column("api_secret_enc", sa.LargeBinary()),
        sa.Column("api_secret_nonce", sa.LargeBinary()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("scope_subnet_ids", JSONB()),
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "checkmk_hosts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("instance_id", UUID(as_uuid=True),
                  sa.ForeignKey("checkmk_instances.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("host_name", sa.String(255), nullable=False),
        sa.Column("site", sa.String(64)),
        sa.Column("alias", sa.String(255)),
        sa.Column("state", sa.String(16)),                 # up | down | unreachable | pending
        sa.Column("checks_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("downtime", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ip", INET()),
        sa.Column("addresses", JSONB()),                   # 主要＋額外的位址
        sa.Column("folder", sa.String(512)),
        sa.Column("groups", JSONB()),
        sa.Column("labels", JSONB()),
        sa.Column("output", sa.Text()),
        sa.Column("last_check", sa.DateTime(timezone=True)),
        sa.Column("jt_ipam_address_id", UUID(as_uuid=True),
                  sa.ForeignKey("ip_addresses.id", ondelete="SET NULL"), index=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("instance_id", "host_name", name="uq_checkmk_host_instance_name"),
    )
    op.create_index("ix_checkmk_hosts_ip", "checkmk_hosts", ["ip"])
    op.add_column("ip_addresses", sa.Column("last_seen_checkmk", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("ip_addresses", "last_seen_checkmk")
    op.drop_index("ix_checkmk_hosts_ip", table_name="checkmk_hosts")
    op.drop_table("checkmk_hosts")
    op.drop_table("checkmk_instances")
