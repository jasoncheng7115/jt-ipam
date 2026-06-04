"""Scan agents + subnets.scan_agent_id.

Revision ID: 0006_scan_agents
Revises: 0005_phpipam_migration
Create Date: 2026-05-09 02:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_scan_agents"
down_revision: str | None = "0005_phpipam_migration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("agent_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("api_token_enc", sa.LargeBinary()),
        sa.Column("api_token_nonce", sa.LargeBinary()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.add_column(
        "subnets",
        sa.Column("scan_agent_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("scan_agents.id", ondelete="SET NULL")),
    )


def downgrade() -> None:
    op.drop_column("subnets", "scan_agent_id")
    op.drop_table("scan_agents")
