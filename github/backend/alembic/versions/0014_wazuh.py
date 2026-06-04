"""Wazuh instance + agent inventory.

Revision ID: 0014_wazuh
Revises: 0013_firewall_opnsense
Create Date: 2026-05-10 01:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_wazuh"
down_revision: str | None = "0013_firewall_opnsense"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wazuh_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("api_url", sa.Text, nullable=False),
        sa.Column("api_user", sa.String(128), nullable=False),
        sa.Column("api_password_enc", sa.LargeBinary, nullable=False),
        sa.Column("api_password_nonce", sa.LargeBinary, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("verify_tls", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("sync_interval_seconds", sa.Integer, nullable=False, server_default="300"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "wazuh_agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("wazuh_instances.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("agent_id", sa.String(16), nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("ip", postgresql.INET),
        sa.Column("register_ip", postgresql.INET),
        sa.Column("status", sa.String(32)),
        sa.Column("os_platform", sa.String(64)),
        sa.Column("os_version", sa.String(64)),
        sa.Column("agent_version", sa.String(64)),
        sa.Column("group", sa.Text),
        sa.Column("node_name", sa.String(64)),
        sa.Column("last_keep_alive", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("jt_ipam_address_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ip_addresses.id", ondelete="SET NULL")),
        sa.Column("cve_critical_count", sa.Integer),
        sa.Column("cve_high_count", sa.Integer),
        sa.Column("cve_summary_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("instance_id", "agent_id", name="wazuh_agent_unique"),
    )
    op.create_index("ix_wazuh_agents_instance_id", "wazuh_agents", ["instance_id"])
    op.create_index("ix_wazuh_agents_ip", "wazuh_agents", ["ip"])


def downgrade() -> None:
    op.drop_index("ix_wazuh_agents_ip", table_name="wazuh_agents")
    op.drop_index("ix_wazuh_agents_instance_id", table_name="wazuh_agents")
    op.drop_table("wazuh_agents")
    op.drop_table("wazuh_instances")
