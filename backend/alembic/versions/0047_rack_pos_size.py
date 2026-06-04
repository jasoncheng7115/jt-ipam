"""機房平面圖：Rack.pos_w / pos_h（方框大小，0..1 比例）。

Revision ID: 0047_rack_pos_size
Revises: 0046_rack_pos_rot
Create Date: 2026-05-31 13:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_rack_pos_size"
down_revision: str | None = "0046_rack_pos_rot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("racks", sa.Column("pos_w", sa.Numeric(6, 5), nullable=True))
    op.add_column("racks", sa.Column("pos_h", sa.Numeric(6, 5), nullable=True))


def downgrade() -> None:
    op.drop_column("racks", "pos_h")
    op.drop_column("racks", "pos_w")
