"""scan_agent 來源 IP

Revision ID: 0068_scan_agent_source_ip
Revises: 0067_circuit_fixed_ip
Create Date: 2026-06-06

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0068_scan_agent_source_ip"
down_revision: str | None = "0067_circuit_fixed_ip"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("scan_agents", sa.Column("last_source_ip", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("scan_agents", "last_source_ip")
