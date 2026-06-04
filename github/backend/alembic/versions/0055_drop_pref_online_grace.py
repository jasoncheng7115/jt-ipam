"""drop user_preferences.online_grace_minutes (moved to global system setting)

Revision ID: 0055_drop_pref_online_grace
Revises: 0054_rack_dimensions
Create Date: 2026-06-01

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0055_drop_pref_online_grace"
down_revision: str | None = "0054_rack_dimensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("user_preferences", "online_grace_minutes")


def downgrade() -> None:
    op.add_column(
        "user_preferences",
        sa.Column("online_grace_minutes", sa.Integer(), nullable=False, server_default="30"),
    )
