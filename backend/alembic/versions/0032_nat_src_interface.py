"""NAT 來源介面欄位 src_interface（feature: NAT source interface）。

Revision ID: 0032_nat_src_interface
Revises: 0031_scan_agent_push
Create Date: 2026-05-29 12:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_nat_src_interface"
down_revision: str | None = "0031_scan_agent_push"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("nat_translations", sa.Column("src_interface", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("nat_translations", "src_interface")
