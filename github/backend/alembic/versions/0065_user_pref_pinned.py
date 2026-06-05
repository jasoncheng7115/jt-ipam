"""user_preferences.pinned（通用釘選：namespace → id 清單，存後端跟著帳號）

Revision ID: 0065_user_pref_pinned
Revises: 0064_device_port_mac
Create Date: 2026-06-05

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0065_user_pref_pinned"
down_revision: str | None = "0064_device_port_mac"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("user_preferences", sa.Column("pinned", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_preferences", "pinned")
