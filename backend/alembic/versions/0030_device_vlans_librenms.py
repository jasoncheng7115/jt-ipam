"""device_vlans 改掛 librenms_devices（feature C 調整：pull-only，不需 jt-ipam Device）。

Revision ID: 0030_device_vlans_librenms
Revises: 0029_device_vlans
Create Date: 2026-05-29 11:00:00

0029 把 device_vlans 掛在 jt-ipam devices，但 LibreNMS 裝置幾乎沒被建成 jt-ipam
Device，導致對應永遠是空的。改掛 librenms_devices。表目前為空，直接 drop+recreate。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_device_vlans_librenms"
down_revision: str | None = "0029_device_vlans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("device_vlans")
    op.create_table(
        "device_vlans",
        sa.Column(
            "id", sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(), primary_key=True,
        ),
        sa.Column(
            "librenms_device_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("librenms_devices.id", ondelete="CASCADE"), nullable=False,
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
        sa.UniqueConstraint(
            "librenms_device_id", "vlan_id", name="uq_device_vlans_ldev_vlan",
        ),
    )
    op.create_index("ix_device_vlans_librenms_device_id", "device_vlans", ["librenms_device_id"])
    op.create_index("ix_device_vlans_vlan_id", "device_vlans", ["vlan_id"])


def downgrade() -> None:
    op.drop_index("ix_device_vlans_vlan_id", table_name="device_vlans")
    op.drop_index("ix_device_vlans_librenms_device_id", table_name="device_vlans")
    op.drop_table("device_vlans")
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
