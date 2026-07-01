"""pfSense integration tables (pfsense_firewalls + pfsense_synced_aliases)

Revision ID: 0087_pfsense_firewall
Revises: 0086_scan_agent_tools
Create Date: 2026-06-27

pfSense 整合（與 OPNsense 分開，獨立資料表）。走第三方 pfSense-pkg-RESTAPI（/api/v2、X-API-Key）。
Phase 1：DHCP / ARP → 在關聯子網路內 stamp IP 存活 / MAC / 主機名稱；別名唯讀同步。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0087_pfsense_firewall"
down_revision: str | None = "0086_scan_agent_tools"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "pfsense_firewalls",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("api_url", sa.Text(), nullable=False),
        sa.Column("api_key_enc", sa.LargeBinary(), nullable=False),
        sa.Column("api_key_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sync_dhcp", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sync_arp", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_aliases", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scope_subnet_ids", ARRAY(UUID(as_uuid=True)), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "pfsense_synced_aliases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("firewall_id", UUID(as_uuid=True),
                  sa.ForeignKey("pfsense_firewalls.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("alias_type", sa.String(32), nullable=True),
        sa.Column("members", JSONB(), nullable=True),
        sa.Column("descr", sa.Text(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("pfsense_synced_aliases")
    op.drop_table("pfsense_firewalls")
