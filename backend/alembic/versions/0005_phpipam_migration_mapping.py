"""phpIPAM migration mapping table.

Revision ID: 0005_phpipam_migration
Revises: 0004_ip_requests
Create Date: 2026-05-09 02:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_phpipam_migration"
down_revision: str | None = "0004_ip_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "phpipam_migration_mapping",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("object_type", sa.String(32), nullable=False, index=True),
        sa.Column("legacy_id", sa.BigInteger(), nullable=False),
        sa.Column("jt_ipam_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_synced_hash", sa.LargeBinary()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("object_type", "legacy_id", name="phpipam_mapping_unique"),
    )
    op.create_index(
        "ix_phpipam_mapping_jt_ipam",
        "phpipam_migration_mapping",
        ["jt_ipam_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_phpipam_mapping_jt_ipam", table_name="phpipam_migration_mapping")
    op.drop_table("phpipam_migration_mapping")
