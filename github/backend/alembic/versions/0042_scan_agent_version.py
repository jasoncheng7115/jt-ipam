"""ScanAgent.agent_version：agent 連上來回報的版本號。

Revision ID: 0042_scan_agent_version
Revises: 0041_subnet_gw_loc_dns
Create Date: 2026-05-30 15:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_scan_agent_version"
down_revision: str | None = "0041_subnet_gw_loc_dns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scan_agents", sa.Column("agent_version", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("scan_agents", "agent_version")
