"""Cabling + Power + VPN tables.

Revision ID: 0012_physical
Revises: 0011_virtualization
Create Date: 2026-05-09 05:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_physical"
down_revision: str | None = "0011_virtualization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cables",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("label", sa.String(128)),
        sa.Column("type", sa.String(32)),
        sa.Column("color", sa.String(16)),
        sa.Column("length_m", sa.Numeric(6, 2)),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default=sa.text("'connected'")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('planned','connected','decommissioned')",
            name="ck_cables_status_valid",
        ),
    )

    op.create_table(
        "cable_terminations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("cable_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cables.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("side", sa.String(1), nullable=False),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("port_label", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("cable_id", "side", name="cable_termination_unique_side"),
        sa.CheckConstraint("side IN ('A','B')", name="ck_cable_terminations_side_valid"),
    )

    op.create_table(
        "power_panels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("locations.id", ondelete="SET NULL")),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("name", "location_id", name="power_panel_unique"),
    )

    op.create_table(
        "power_feeds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("panel_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("power_panels.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("voltage_v", sa.Integer(), nullable=False, server_default=sa.text("220")),
        sa.Column("amperage_a", sa.Integer(), nullable=False, server_default=sa.text("20")),
        sa.Column("phase", sa.String(8), nullable=False, server_default=sa.text("'single'")),
        sa.Column("supply_type", sa.String(8), nullable=False,
                  server_default=sa.text("'ac'")),
        sa.Column("rack_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("racks.id", ondelete="SET NULL")),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("panel_id", "name", name="power_feed_panel_name_uq"),
        sa.CheckConstraint("phase IN ('single','three')", name="ck_power_feeds_phase_valid"),
        sa.CheckConstraint("supply_type IN ('ac','dc')", name="ck_power_feeds_supply_valid"),
    )

    op.create_table(
        "power_outlets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("feed_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("power_feeds.id", ondelete="SET NULL"), index=True),
        sa.Column("rack_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("racks.id", ondelete="SET NULL")),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_table(
        "vpn_tunnels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default=sa.text("'active'")),
        sa.Column("a_device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("b_device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("a_endpoint", sa.Text()),
        sa.Column("b_endpoint", sa.Text()),
        sa.Column("encryption_algo", sa.String(32)),
        sa.Column("auth_algo", sa.String(32)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "type IN ('ipsec_ikev1','ipsec_ikev2','wireguard','openvpn',"
            "'l2tp','vxlan','vpls','evpn','other')",
            name="ck_vpn_tunnels_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('planned','active','offline','decommissioned')",
            name="ck_vpn_tunnels_status_valid",
        ),
    )


def downgrade() -> None:
    for t in (
        "vpn_tunnels",
        "power_outlets", "power_feeds", "power_panels",
        "cable_terminations", "cables",
    ):
        op.drop_table(t)
