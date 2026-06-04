"""rack physical dimensions (width_mm / depth_mm) for floor-plan footprint

Revision ID: 0054_rack_dimensions
Revises: 0053_nat_circuit_fields
Create Date: 2026-06-01

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0054_rack_dimensions"
down_revision: str | None = "0053_nat_circuit_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("racks", sa.Column("width_mm", sa.Integer(), nullable=True))
    op.add_column("racks", sa.Column("depth_mm", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("racks", "depth_mm")
    op.drop_column("racks", "width_mm")
