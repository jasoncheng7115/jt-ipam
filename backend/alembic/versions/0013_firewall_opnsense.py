"""OPNsense firewall + alias mapping.

Revision ID: 0013_firewall_opnsense
Revises: 0012_physical
Create Date: 2026-05-10 00:50:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_firewall_opnsense"
down_revision: str | None = "0012_physical"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "opnsense_firewalls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("api_url", sa.Text, nullable=False),
        sa.Column("api_key_enc", sa.LargeBinary, nullable=False),
        sa.Column("api_key_nonce", sa.LargeBinary, nullable=False),
        sa.Column("api_secret_enc", sa.LargeBinary, nullable=False),
        sa.Column("api_secret_nonce", sa.LargeBinary, nullable=False),
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
        "opnsense_alias_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("firewall_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("opnsense_firewalls.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("alias_name", sa.String(64), nullable=False),
        sa.Column("alias_type", sa.String(32), nullable=False, server_default="host"),
        sa.Column("selector", postgresql.JSONB, nullable=False),
        sa.Column("direction", sa.String(8), nullable=False, server_default="push"),
        sa.Column("last_alias_uuid", sa.String(64)),
        sa.Column("last_synced_count", sa.Integer),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("firewall_id", "alias_name", name="opnsense_alias_mapping_unique"),
    )
    op.create_index(
        "ix_opnsense_alias_mappings_firewall_id",
        "opnsense_alias_mappings", ["firewall_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_opnsense_alias_mappings_firewall_id",
                  table_name="opnsense_alias_mappings")
    op.drop_table("opnsense_alias_mappings")
    op.drop_table("opnsense_firewalls")
