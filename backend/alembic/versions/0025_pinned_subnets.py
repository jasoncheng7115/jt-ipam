"""user_preferences: 加 pinned_subnet_ids（Dashboard 常用子網路）。

Revision ID: 0025_pinned_subnets
Revises: 0024_customers
Create Date: 2026-05-27 15:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0025_pinned_subnets"
down_revision: str | None = "0024_customers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_preferences",
        sa.Column("pinned_subnet_ids", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_preferences", "pinned_subnet_ids")
