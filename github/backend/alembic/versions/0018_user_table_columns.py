"""user_preferences.table_columns (per-table column visibility).

Revision ID: 0018_user_table_columns
Revises: 0017_adguard
Create Date: 2026-05-26 19:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_user_table_columns"
down_revision: str | None = "0017_adguard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_preferences",
                  sa.Column("table_columns", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("user_preferences", "table_columns")
