"""Virtualization tables: virt_clusters / virtual_machines / vm_interfaces / proxmox_instances.

Revision ID: 0011_virtualization
Revises: 0010_advanced_modules
Create Date: 2026-05-09 05:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_virtualization"
down_revision: str | None = "0010_advanced_modules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "virt_clusters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("type", sa.String(32), nullable=False, server_default=sa.text("'proxmox'")),
        sa.Column("description", sa.Text()),
        sa.Column("location_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("locations.id", ondelete="SET NULL")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "type IN ('proxmox','vmware','hyper-v','kvm','xenserver','other')",
            name="ck_virt_clusters_type_valid",
        ),
    )

    op.create_table(
        "virtual_machines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("virt_clusters.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("legacy_vmid", sa.BigInteger(), index=True),
        sa.Column("name", sa.String(128), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default=sa.text("'unknown'")),
        sa.Column("vcpus", sa.Integer()),
        sa.Column("memory_mb", sa.Integer()),
        sa.Column("disk_gb", sa.Integer()),
        sa.Column("primary_ip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ip_addresses.id", ondelete="SET NULL")),
        sa.Column("device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="SET NULL")),
        sa.Column("description", sa.Text()),
        sa.Column("is_template", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("cluster_id", "name", name="vm_cluster_name_uq"),
        sa.CheckConstraint(
            "status IN ('running','stopped','paused','migrating','unknown')",
            name="ck_virtual_machines_status_valid",
        ),
    )

    op.create_table(
        "vm_interfaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("vm_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("virtual_machines.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("mac", postgresql.MACADDR()),
        sa.Column("primary_ip", postgresql.INET()),
        sa.Column("bridge", sa.String(64)),
        sa.Column("vlan_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("vlans.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("vm_id", "name", name="vmif_vm_name_uq"),
    )

    op.create_table(
        "proxmox_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("virt_clusters.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("api_url", sa.Text(), nullable=False),
        sa.Column("auth_username", sa.String(128), nullable=False),
        sa.Column("auth_token_id", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=False,
                  server_default=sa.text("600")),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )


def downgrade() -> None:
    for t in (
        "proxmox_instances", "vm_interfaces", "virtual_machines", "virt_clusters",
    ):
        op.drop_table(t)
