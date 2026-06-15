"""opnsense firewall association scope: location/customer/subnets/iface map for NAT IP resolution

Revision ID: 0071_opnsense_firewall_scope
Revises: 0070_scan_agent_force_scan
Create Date: 2026-06-07

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0071_opnsense_firewall_scope"
down_revision: str | None = "0070_scan_agent_force_scan"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "opnsense_firewalls",
        sa.Column(
            "scope_location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "opnsense_firewalls",
        sa.Column(
            "scope_customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "opnsense_firewalls",
        sa.Column(
            "scope_subnet_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
    )
    op.add_column(
        "opnsense_firewalls",
        sa.Column("iface_subnet_map", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("opnsense_firewalls", "iface_subnet_map")
    op.drop_column("opnsense_firewalls", "scope_subnet_ids")
    op.drop_column("opnsense_firewalls", "scope_customer_id")
    op.drop_column("opnsense_firewalls", "scope_location_id")
