"""LibreNMS integration tables.

Revision ID: 0008_librenms
Revises: 0007_dns
Create Date: 2026-05-09 03:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_librenms"
down_revision: str | None = "0007_dns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "librenms_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("api_url", sa.Text(), nullable=False),
        sa.Column("api_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column("api_token_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sync_devices", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sync_arp", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sync_fdb", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("use_for_status", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("auto_add_devices", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=False, server_default=sa.text("300")),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_table(
        "librenms_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("librenms_instances.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("legacy_device_id", sa.BigInteger(), nullable=False),
        sa.Column("hostname", sa.Text()),
        sa.Column("sysname", sa.Text()),
        sa.Column("primary_ip", postgresql.INET()),
        sa.Column("hardware", sa.Text()),
        sa.Column("os", sa.Text()),
        sa.Column("version", sa.Text()),
        sa.Column("serial", sa.Text()),
        sa.Column("sysObjectID", sa.Text()),
        sa.Column("uptime", sa.BigInteger()),
        sa.Column("status", sa.String(16)),
        sa.Column("jt_ipam_device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("instance_id", "legacy_device_id",
                            name="librenms_device_unique"),
    )

    op.create_table(
        "arp_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("ip", postgresql.INET(), nullable=False, index=True),
        sa.Column("mac", postgresql.MACADDR(), nullable=False, index=True),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("librenms_instances.id", ondelete="SET NULL")),
        sa.Column("device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("librenms_devices.id", ondelete="SET NULL")),
        sa.Column("interface", sa.String(64)),
        sa.Column("vrf", sa.String(64)),
        sa.Column("source", sa.String(16), nullable=False,
                  server_default=sa.text("'librenms'")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False, index=True),
        sa.UniqueConstraint("ip", "mac", "device_id", name="arp_entry_unique"),
    )

    op.create_table(
        "fdb_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("mac", postgresql.MACADDR(), nullable=False, index=True),
        sa.Column("vlan_id_num", sa.Integer()),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("librenms_instances.id", ondelete="SET NULL")),
        sa.Column("device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("librenms_devices.id", ondelete="SET NULL")),
        sa.Column("port_name", sa.String(64)),
        sa.Column("source", sa.String(16), nullable=False,
                  server_default=sa.text("'librenms'")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False, index=True),
        sa.UniqueConstraint("mac", "device_id", "port_name", "vlan_id_num",
                            name="fdb_entry_unique"),
    )


def downgrade() -> None:
    op.drop_table("fdb_entries")
    op.drop_table("arp_entries")
    op.drop_table("librenms_devices")
    op.drop_table("librenms_instances")
