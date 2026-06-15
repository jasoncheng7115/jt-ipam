"""integration subnet scope: scope_subnet_ids on wazuh / proxmox / adguard / dns

Revision ID: 0072_integration_subnet_scope
Revises: 0071_opnsense_firewall_scope
Create Date: 2026-06-07

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0072_integration_subnet_scope"
down_revision: str | None = "0071_opnsense_firewall_scope"
branch_labels: str | None = None
depends_on: str | None = None

_TABLES = (
    "wazuh_instances",
    "proxmox_instances",
    "adguard_instances",
    "dns_servers",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("scope_subnet_ids", postgresql.JSONB(), nullable=True),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "scope_subnet_ids")
