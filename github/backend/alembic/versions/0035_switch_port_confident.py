"""IPAddress.switch_port_confident（FDB 交換器位置信心：直連 vs uplink/trunk）。

Revision ID: 0035_switch_port_confident
Revises: 0034_librenms_scope
Create Date: 2026-05-29 14:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_switch_port_confident"
down_revision: str | None = "0034_librenms_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ip_addresses", sa.Column("switch_port_confident", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("ip_addresses", "switch_port_confident")
