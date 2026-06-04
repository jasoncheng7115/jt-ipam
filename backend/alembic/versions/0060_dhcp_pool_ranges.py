"""dhcp_pool_ranges — DHCP 發放範圍（從 OPNsense Kea 同步）

Revision ID: 0060_dhcp_pool_ranges
Revises: 0059_rack_seq
Create Date: 2026-06-03

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0060_dhcp_pool_ranges"
down_revision: str | None = "0059_rack_seq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dhcp_pool_ranges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("firewall_id", UUID(as_uuid=True),
                  sa.ForeignKey("opnsense_firewalls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subnet_cidr", sa.String(64), nullable=False),
        sa.Column("start_ip", sa.String(64), nullable=False),
        sa.Column("end_ip", sa.String(64), nullable=False),
        sa.Column("family", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("source", sa.String(16), nullable=False, server_default="kea"),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_dhcp_pool_ranges_firewall_id", "dhcp_pool_ranges", ["firewall_id"])


def downgrade() -> None:
    op.drop_index("ix_dhcp_pool_ranges_firewall_id", table_name="dhcp_pool_ranges")
    op.drop_table("dhcp_pool_ranges")
