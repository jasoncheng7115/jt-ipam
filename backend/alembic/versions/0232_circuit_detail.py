"""電路詳細資料：內建聯絡人角色、電路的 WAN 位址、租用設備（2026-10-29）

客戶許願清單：電路要能記供應商聯絡人（業務、機房人員）、好幾個 WAN IP、租用要歸還的設備。
見 app/models/advanced.py 的 CircuitAddress、DeviceLease。

Revision ID: 0232_circuit_detail
Revises: 0231_warroom_intel
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0232_circuit_detail"
down_revision: str | None = "0231_warroom_intel"
branch_labels: str | None = None
depends_on: str | None = None

BUILTIN_ROLES = ("sales", "onsite", "noc", "billing")


def _ts() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade() -> None:
    op.add_column("contact_roles", sa.Column("builtin_key", sa.String(32)))
    op.create_unique_constraint("contact_roles_builtin_key_key", "contact_roles", ["builtin_key"])
    for key in BUILTIN_ROLES:
        # 已經有同名角色（使用者自己建的）就把它認成內建，不另建一筆
        op.execute(sa.text(
            "INSERT INTO contact_roles (id, name, builtin_key) VALUES (gen_random_uuid(), :k, :k) "
            "ON CONFLICT (name) DO UPDATE SET builtin_key = EXCLUDED.builtin_key"
        ).bindparams(k=key))

    op.create_table(
        "circuit_addresses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("circuit_id", UUID(as_uuid=True), sa.ForeignKey("circuits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subnet_id", UUID(as_uuid=True), sa.ForeignKey("subnets.id", ondelete="CASCADE")),
        sa.Column("address_id", UUID(as_uuid=True), sa.ForeignKey("ip_addresses.id", ondelete="CASCADE")),
        sa.Column("role", sa.String(16), nullable=False, server_default="wan"),
        sa.Column("note", sa.Text()),
        *_ts(),
        sa.CheckConstraint("(subnet_id IS NULL) <> (address_id IS NULL)", name="ck_circuit_addresses_one_target"),
        sa.CheckConstraint("role IN ('wan','gateway','peer')", name="ck_circuit_addresses_role"),
        sa.UniqueConstraint("circuit_id", "subnet_id", name="uq_circuit_addresses_subnet"),
        sa.UniqueConstraint("circuit_id", "address_id", name="uq_circuit_addresses_address"),
    )
    op.create_index("ix_circuit_addresses_circuit_id", "circuit_addresses", ["circuit_id"])
    op.create_index("ix_circuit_addresses_subnet_id", "circuit_addresses", ["subnet_id"])
    op.create_index("ix_circuit_addresses_address_id", "circuit_addresses", ["address_id"])

    op.create_table(
        "device_leases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("device_id", UUID(as_uuid=True), sa.ForeignKey("devices.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("provider_id", UUID(as_uuid=True), sa.ForeignKey("providers.id", ondelete="SET NULL")),
        sa.Column("circuit_id", UUID(as_uuid=True), sa.ForeignKey("circuits.id", ondelete="SET NULL")),
        sa.Column("return_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("return_due", sa.Date()),
        sa.Column("note", sa.Text()),
        *_ts(),
    )
    op.create_index("ix_device_leases_provider_id", "device_leases", ["provider_id"])
    op.create_index("ix_device_leases_circuit_id", "device_leases", ["circuit_id"])


def downgrade() -> None:
    op.drop_table("device_leases")
    op.drop_table("circuit_addresses")
    op.execute(sa.text("DELETE FROM contact_roles WHERE builtin_key IS NOT NULL AND name = builtin_key "
                       "AND id NOT IN (SELECT role_id FROM contact_assignments WHERE role_id IS NOT NULL)"))
    op.drop_constraint("contact_roles_builtin_key_key", "contact_roles", type_="unique")
    op.drop_column("contact_roles", "builtin_key")
