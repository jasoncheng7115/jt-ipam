"""OUI vendor lookup table。

Revision ID: 0026_oui_vendors
Revises: 0025_pinned_subnets
Create Date: 2026-05-28 22:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_oui_vendors"
down_revision: str | None = "0025_pinned_subnets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oui_vendors",
        sa.Column("prefix", sa.String(length=6), primary_key=True),
        sa.Column("short_name", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), server_default="wireshark", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_oui_vendors_short_name", "oui_vendors", ["short_name"])


def downgrade() -> None:
    op.drop_index("ix_oui_vendors_short_name", table_name="oui_vendors")
    op.drop_table("oui_vendors")
