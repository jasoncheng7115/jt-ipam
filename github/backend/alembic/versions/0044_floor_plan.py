"""機房平面圖：Location.floor_plan_path + Rack.pos_x/pos_y。

Revision ID: 0044_floor_plan
Revises: 0043_vpn_pubkeys
Create Date: 2026-05-31 09:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_floor_plan"
down_revision: str | None = "0043_vpn_pubkeys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("locations", sa.Column("floor_plan_path", sa.Text(), nullable=True))
    op.add_column("racks", sa.Column("pos_x", sa.Numeric(6, 5), nullable=True))
    op.add_column("racks", sa.Column("pos_y", sa.Numeric(6, 5), nullable=True))


def downgrade() -> None:
    op.drop_column("racks", "pos_y")
    op.drop_column("racks", "pos_x")
    op.drop_column("locations", "floor_plan_path")
