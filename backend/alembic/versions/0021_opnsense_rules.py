"""OPNsense firewall rules cache + sync_rules flag.

Revision ID: 0021_opnsense_rules
Revises: 0020_system_settings
Create Date: 2026-05-27 09:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_opnsense_rules"
down_revision: str | None = "0020_system_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("opnsense_firewalls",
                  sa.Column("sync_rules", sa.Boolean, nullable=False, server_default="false"))

    op.create_table(
        "opnsense_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("firewall_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("opnsense_firewalls.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("legacy_uuid", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("sequence", sa.Integer),
        sa.Column("action", sa.String(16)),
        sa.Column("interface", sa.String(64)),
        sa.Column("direction", sa.String(8)),
        sa.Column("protocol", sa.String(16)),
        sa.Column("source_net", sa.Text),
        sa.Column("source_port", sa.String(64)),
        sa.Column("destination_net", sa.Text),
        sa.Column("destination_port", sa.String(64)),
        sa.Column("description", sa.Text),
        sa.Column("raw", postgresql.JSONB),
        sa.Column("last_synced_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("firewall_id", "legacy_uuid",
                            name="opnsense_rule_legacy_unique"),
    )
    op.create_index("ix_opnsense_rules_firewall_id", "opnsense_rules", ["firewall_id"])


def downgrade() -> None:
    op.drop_index("ix_opnsense_rules_firewall_id", table_name="opnsense_rules")
    op.drop_table("opnsense_rules")
    op.drop_column("opnsense_firewalls", "sync_rules")
