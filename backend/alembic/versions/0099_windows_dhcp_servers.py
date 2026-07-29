"""windows_dhcp_servers：Windows DHCP Server 整合（Beta，WinRM + PowerShell 唯讀）

Revision ID: 0099_windows_dhcp_servers
Revises: 0098_dhcp_ranges_multi_src
Create Date: 2026-07-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0099_windows_dhcp_servers"
down_revision: str | None = "0098_dhcp_ranges_multi_src"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "windows_dhcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="5986"),
        sa.Column("use_ssl", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("password_enc", sa.LargeBinary(), nullable=False),
        sa.Column("password_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("sync_scopes", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_leases", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("scope_subnet_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True))),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.execute("DELETE FROM dhcp_pool_ranges WHERE source_type = 'windows_dhcp'")
    op.drop_table("windows_dhcp_servers")
