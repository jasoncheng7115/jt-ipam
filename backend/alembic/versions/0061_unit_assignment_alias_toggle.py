"""locations.customer_id + virt_clusters.customer_id + opnsense_firewalls.sync_aliases

Revision ID: 0061_unit_assignment
Revises: 0060_dhcp_pool_ranges
Create Date: 2026-06-03

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0061_unit_assignment"
down_revision: str | None = "0060_dhcp_pool_ranges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("locations", sa.Column("customer_id", UUID(as_uuid=True),
                  sa.ForeignKey("customers.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_locations_customer_id", "locations", ["customer_id"])
    op.add_column("virt_clusters", sa.Column("customer_id", UUID(as_uuid=True),
                  sa.ForeignKey("customers.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_virt_clusters_customer_id", "virt_clusters", ["customer_id"])
    op.add_column("opnsense_firewalls", sa.Column(
        "sync_aliases", sa.Boolean(), nullable=False, server_default="true"))


def downgrade() -> None:
    op.drop_column("opnsense_firewalls", "sync_aliases")
    op.drop_index("ix_virt_clusters_customer_id", table_name="virt_clusters")
    op.drop_column("virt_clusters", "customer_id")
    op.drop_index("ix_locations_customer_id", table_name="locations")
    op.drop_column("locations", "customer_id")
