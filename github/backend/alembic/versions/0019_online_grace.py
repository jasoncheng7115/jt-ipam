"""user_preferences.online_grace_minutes.

Revision ID: 0019_online_grace
Revises: 0018_user_table_columns
Create Date: 2026-05-26 21:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_online_grace"
down_revision: str | None = "0018_user_table_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_preferences",
                  sa.Column("online_grace_minutes", sa.Integer, nullable=False,
                            server_default="30"))


def downgrade() -> None:
    op.drop_column("user_preferences", "online_grace_minutes")
