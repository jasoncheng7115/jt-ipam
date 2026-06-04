"""VPNTunnel.local_public_key / peer_public_key：WireGuard 對接偵測用公鑰。

Revision ID: 0043_vpn_pubkeys
Revises: 0042_scan_agent_version
Create Date: 2026-05-30 21:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_vpn_pubkeys"
down_revision: str | None = "0042_scan_agent_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vpn_tunnels", sa.Column("local_public_key", sa.Text(), nullable=True))
    op.add_column("vpn_tunnels", sa.Column("peer_public_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("vpn_tunnels", "peer_public_key")
    op.drop_column("vpn_tunnels", "local_public_key")
