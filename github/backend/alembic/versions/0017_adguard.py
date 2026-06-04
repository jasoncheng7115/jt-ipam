"""AdGuard Home instances.

Revision ID: 0017_adguard
Revises: 0016_opnsense_extra_sync
Create Date: 2026-05-26 18:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_adguard"
down_revision: str | None = "0016_opnsense_extra_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "adguard_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("api_url", sa.Text, nullable=False),
        sa.Column("api_user", sa.String(128), nullable=False),
        sa.Column("api_password_enc", sa.LargeBinary, nullable=False),
        sa.Column("api_password_nonce", sa.LargeBinary, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("verify_tls", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("sync_clients", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("sync_rewrites", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("sync_interval_seconds", sa.Integer, nullable=False, server_default="300"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("adguard_instances")
