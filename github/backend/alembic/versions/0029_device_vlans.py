"""device_vlans 對應表 + librenms_instances.sync_vlans（feature C）。

Revision ID: 0029_device_vlans
Revises: 0028_ip_hostname_obs
Create Date: 2026-05-29 10:20:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_device_vlans"
down_revision: str | None = "0028_ip_hostname_obs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "librenms_instances",
        sa.Column("sync_vlans", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_table(
        "device_vlans",
        sa.Column(
            "id", sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(), primary_key=True,
        ),
        sa.Column(
            "device_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "vlan_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vlans.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("source", sa.String(length=16), server_default="librenms", nullable=False),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("device_id", "vlan_id", name="uq_device_vlans_device_vlan"),
    )
    op.create_index("ix_device_vlans_device_id", "device_vlans", ["device_id"])
    op.create_index("ix_device_vlans_vlan_id", "device_vlans", ["vlan_id"])


def downgrade() -> None:
    op.drop_index("ix_device_vlans_vlan_id", table_name="device_vlans")
    op.drop_index("ix_device_vlans_device_id", table_name="device_vlans")
    op.drop_table("device_vlans")
    op.drop_column("librenms_instances", "sync_vlans")
