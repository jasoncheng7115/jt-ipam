"""scan_agent force_scan_at: 'scan now' trigger flag

Revision ID: 0070_scan_agent_force_scan
Revises: 0069_scan_probe_config
Create Date: 2026-06-06

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0070_scan_agent_force_scan"
down_revision: str | None = "0069_scan_probe_config"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "scan_agents",
        sa.Column("force_scan_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scan_agents", "force_scan_at")
