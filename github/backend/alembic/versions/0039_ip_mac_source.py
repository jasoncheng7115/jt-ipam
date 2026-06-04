"""IPAddress.mac_source：記錄目前 MAC 來自哪個來源，供 ARP 來源優先序覆寫判斷。

Revision ID: 0039_ip_mac_source
Revises: 0038_cluster_from_pve
Create Date: 2026-05-30 12:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_ip_mac_source"
down_revision: str | None = "0038_cluster_from_pve"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ip_addresses", sa.Column("mac_source", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("ip_addresses", "mac_source")
