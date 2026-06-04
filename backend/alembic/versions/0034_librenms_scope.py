"""LibreNMS instance scope_subnet_ids（重疊網段：限定 sync 解析範圍）。

Revision ID: 0034_librenms_scope
Revises: 0033_vlan_customer_section
Create Date: 2026-05-29 13:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_librenms_scope"
down_revision: str | None = "0033_vlan_customer_section"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("librenms_instances",
                  sa.Column("scope_subnet_ids", sa.dialects.postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("librenms_instances", "scope_subnet_ids")
