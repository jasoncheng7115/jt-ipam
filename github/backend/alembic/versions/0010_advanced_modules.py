"""Phase 3 advanced modules: tenancy, contacts, asn, circuits, wireless.

Revision ID: 0010_advanced_modules
Revises: 0009_pgvector
Create Date: 2026-05-09 04:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_advanced_modules"
down_revision: str | None = "0009_pgvector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Tenancy ──
    op.create_table(
        "tenant_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenant_groups.id", ondelete="SET NULL"), index=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("slug", sa.String(64), unique=True, nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenant_groups.id", ondelete="SET NULL"), index=True),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    # ── Contacts ──
    op.create_table(
        "contact_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("contact_groups.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_table(
        "contact_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(64), unique=True, nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_table(
        "contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("title", sa.String(128)),
        sa.Column("phone", sa.String(64)),
        sa.Column("email", sa.String(255)),
        sa.Column("address", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("group_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("contact_groups.id", ondelete="SET NULL")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_table(
        "contact_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("contacts.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("role_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("contact_roles.id", ondelete="SET NULL")),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("contact_id", "role_id", "object_type", "object_id",
                            name="contact_assignment_unique"),
    )

    # ── ASN ──
    op.create_table(
        "asns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("asn", sa.BigInteger(), unique=True, nullable=False),
        sa.Column("rir", sa.String(16)),
        sa.Column("description", sa.Text()),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint("asn > 0 AND asn < 4294967295", name="ck_asns_range"),
    )

    # ── Circuits ──
    op.create_table(
        "providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("asn", sa.BigInteger()),
        sa.Column("account_number", sa.String(128)),
        sa.Column("portal_url", sa.Text()),
        sa.Column("noc_contact", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_table(
        "circuit_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(64), unique=True, nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_table(
        "circuits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("cid", sa.String(128), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("providers.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("type_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("circuit_types.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default=sa.text("'active'")),
        sa.Column("install_date", sa.DateTime(timezone=True)),
        sa.Column("contract_end_date", sa.DateTime(timezone=True)),
        sa.Column("monthly_fee_cents", sa.Integer()),
        sa.Column("commit_rate_kbps", sa.Integer()),
        sa.Column("description", sa.Text()),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("provider_id", "cid", name="circuit_provider_cid_uq"),
        sa.CheckConstraint(
            "status IN ('planned','provisioning','active','offline','decommissioned')",
            name="ck_circuits_status_valid",
        ),
    )

    # ── Wireless ──
    op.create_table(
        "wireless_ssids",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("ssid", sa.String(64), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("auth_type", sa.String(32)),
        sa.Column("vlan_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("vlans.id", ondelete="SET NULL")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_table(
        "wireless_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("a_device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("b_device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("ssid", sa.String(64)),
        sa.Column("distance_m", sa.Integer()),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )


def downgrade() -> None:
    for t in (
        "wireless_links", "wireless_ssids",
        "circuits", "circuit_types", "providers",
        "asns",
        "contact_assignments", "contacts", "contact_roles", "contact_groups",
        "tenants", "tenant_groups",
    ):
        op.drop_table(t)
