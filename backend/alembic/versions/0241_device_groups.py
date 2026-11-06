"""裝置群組：堆疊／HA／叢集（客戶 2026-11-05）

- device_groups：名稱、類型、說明、代表裝置
- devices.group_id／group_role／group_order：在哪一組、角色、堆疊序號
- ip_addresses.device_group_id：共用給群組的 IP（device_id 一律是群組的代表裝置，見 models/device_group.py）

Revision ID: 0241_device_groups
Revises: 0240_dns_compare_exclusions
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0241_device_groups"
down_revision: str | None = "0240_dns_compare_exclusions"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "device_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("kind", sa.String(24), nullable=False, server_default="stack"),
        sa.Column("description", sa.Text()),
        sa.Column("representative_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('stack','ha_active_passive','ha_active_active','cluster')",
                           name="device_group_kind_valid"),
    )
    op.create_foreign_key("fk_device_groups_representative_id_devices", "device_groups", "devices",
                          ["representative_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_device_groups_representative_id", "device_groups", ["representative_id"])
    op.add_column("devices", sa.Column("group_id", postgresql.UUID(as_uuid=True),
                                       sa.ForeignKey("device_groups.id", ondelete="SET NULL")))
    op.add_column("devices", sa.Column("group_role", sa.String(16)))
    op.add_column("devices", sa.Column("group_order", sa.Integer()))
    op.create_index("ix_devices_group_id", "devices", ["group_id"])
    op.add_column("ip_addresses", sa.Column("device_group_id", postgresql.UUID(as_uuid=True),
                                            sa.ForeignKey("device_groups.id", ondelete="SET NULL")))
    op.create_index("ix_ip_addresses_device_group_id", "ip_addresses", ["device_group_id"])


def downgrade() -> None:
    op.drop_index("ix_ip_addresses_device_group_id", table_name="ip_addresses")
    op.drop_column("ip_addresses", "device_group_id")
    op.drop_index("ix_devices_group_id", table_name="devices")
    op.drop_column("devices", "group_order")
    op.drop_column("devices", "group_role")
    op.drop_column("devices", "group_id")
    op.drop_table("device_groups")
