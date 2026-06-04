"""VLAN 加 customer_id / section_id（可篩選 + 顯示欄位）。

Revision ID: 0033_vlan_customer_section
Revises: 0032_nat_src_interface
Create Date: 2026-05-29 13:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_vlan_customer_section"
down_revision: str | None = "0032_nat_src_interface"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vlans", sa.Column("customer_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("vlans", sa.Column("section_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_vlans_customer_id_customers", "vlans", "customers",
                          ["customer_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_vlans_section_id_sections", "vlans", "sections",
                          ["section_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_vlans_customer_id", "vlans", ["customer_id"])
    op.create_index("ix_vlans_section_id", "vlans", ["section_id"])


def downgrade() -> None:
    op.drop_index("ix_vlans_section_id", table_name="vlans")
    op.drop_index("ix_vlans_customer_id", table_name="vlans")
    op.drop_constraint("fk_vlans_section_id_sections", "vlans", type_="foreignkey")
    op.drop_constraint("fk_vlans_customer_id_customers", "vlans", type_="foreignkey")
    op.drop_column("vlans", "section_id")
    op.drop_column("vlans", "customer_id")
