"""機房平面圖：Rack.pos_rot（在平面圖上的旋轉角度 0/90/180/270）。

Revision ID: 0046_rack_pos_rot
Revises: 0045_drop_dead_prefs
Create Date: 2026-05-31 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_rack_pos_rot"
down_revision: str | None = "0045_drop_dead_prefs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "racks",
        sa.Column("pos_rot", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("racks", "pos_rot")
