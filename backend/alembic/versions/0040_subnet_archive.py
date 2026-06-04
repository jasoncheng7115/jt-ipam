"""Subnet 歸檔：archived_at（非空＝已歸檔，不顯示、不掃描；重疊檢查忽略已歸檔）。

Revision ID: 0040_subnet_archive
Revises: 0039_ip_mac_source
Create Date: 2026-05-30 13:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_subnet_archive"
down_revision: str | None = "0039_ip_mac_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("subnets", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_subnets_archived_at", "subnets", ["archived_at"])


def downgrade() -> None:
    op.drop_index("ix_subnets_archived_at", table_name="subnets")
    op.drop_column("subnets", "archived_at")
