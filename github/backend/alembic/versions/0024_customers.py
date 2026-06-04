"""Customers / 管理單位 + nullable FK on subnets / sections / ip_addresses / devices.

Revision ID: 0024_customers
Revises: 0023_opnsense_sync_nat
Create Date: 2026-05-27 14:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_customers"
down_revision: str | None = "0023_opnsense_sync_nat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES_WITH_FK = ("subnets", "sections", "ip_addresses", "devices")


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True, server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("contact", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("name", name="uq_customers_name"),
    )

    for tname in _TABLES_WITH_FK:
        op.add_column(
            tname,
            sa.Column(
                "customer_id", postgresql.UUID(as_uuid=True), nullable=True,
            ),
        )
        op.create_foreign_key(
            f"fk_{tname}_customer_id_customers",
            tname, "customers",
            ["customer_id"], ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            f"ix_{tname}_customer_id", tname, ["customer_id"],
        )


def downgrade() -> None:
    for tname in _TABLES_WITH_FK:
        op.drop_index(f"ix_{tname}_customer_id", table_name=tname)
        op.drop_constraint(f"fk_{tname}_customer_id_customers", tname, type_="foreignkey")
        op.drop_column(tname, "customer_id")
    op.drop_table("customers")
