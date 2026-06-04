"""Scan agent push 模型：enroll_key_hash + agent_url 改 nullable。

Revision ID: 0031_scan_agent_push
Revises: 0030_device_vlans_librenms
Create Date: 2026-05-29 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_scan_agent_push"
down_revision: str | None = "0030_device_vlans_librenms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scan_agents", sa.Column("enroll_key_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_scan_agents_enroll_key_hash", "scan_agents", ["enroll_key_hash"], unique=True)
    op.alter_column("scan_agents", "agent_url", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.alter_column("scan_agents", "agent_url", existing_type=sa.Text(), nullable=False)
    op.drop_index("ix_scan_agents_enroll_key_hash", table_name="scan_agents")
    op.drop_column("scan_agents", "enroll_key_hash")
