"""Rack.numbering（U 編號方向）+ Rack.face（正面/背面）。

Revision ID: 0049_rack_numbering_face
Revises: 0048_dns_ucs_type
Create Date: 2026-05-31 15:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_rack_numbering_face"
down_revision: str | None = "0048_dns_ucs_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("racks", sa.Column("numbering", sa.String(16), nullable=False,
                                     server_default="top-down"))
    op.add_column("racks", sa.Column("face", sa.String(8), nullable=False,
                                     server_default="front"))


def downgrade() -> None:
    op.drop_column("racks", "face")
    op.drop_column("racks", "numbering")
