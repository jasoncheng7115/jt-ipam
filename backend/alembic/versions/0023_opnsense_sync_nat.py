"""OPNsense firewall: separate sync_nat flag (was piggybacked on sync_rules).

Revision ID: 0023_opnsense_sync_nat
Revises: 0022_nat_origin
Create Date: 2026-05-27 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_opnsense_sync_nat"
down_revision: str | None = "0022_nat_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("opnsense_firewalls",
                  sa.Column("sync_nat", sa.Boolean, nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("opnsense_firewalls", "sync_nat")
