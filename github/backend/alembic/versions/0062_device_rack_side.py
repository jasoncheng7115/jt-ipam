"""device rack_side（半 U：left/right；預設 full）

Revision ID: 0062_device_rack_side
Revises: 0061_unit_assignment
Create Date: 2026-06-03

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0062_device_rack_side"
down_revision: str | None = "0061_unit_assignment"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "rack_side",
            sa.String(length=8),
            nullable=False,
            server_default="full",
        ),
    )


def downgrade() -> None:
    op.drop_column("devices", "rack_side")
