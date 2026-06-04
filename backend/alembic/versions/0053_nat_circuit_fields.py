"""NAT 規則擴充 OPNsense 完整欄位（停用/無RDR/IP版本/反向/埠範圍/記錄/類別/
NAT反射/集區/篩選規則關聯/alias 參考）+ 放寬 protocol；Circuit 加上傳/下載頻寬。

Revision ID: 0053_nat_circuit_fields
Revises: 0052_rbac_object_types
Create Date: 2026-06-01 03:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_nat_circuit_fields"
down_revision: str | None = "0052_rbac_object_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BOOL_COLS = ["disabled", "no_rdr", "src_not", "dst_not", "log"]
_STR_ALIAS = ["category", "nat_reflection", "pool_options", "filter_rule",
              "src_alias", "dst_alias", "src_port_alias", "dst_port_alias", "redirect_alias"]


def upgrade() -> None:
    for c in _BOOL_COLS:
        op.add_column("nat_translations",
                      sa.Column(c, sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("nat_translations",
                  sa.Column("ip_version", sa.String(length=8), nullable=False, server_default="inet"))
    op.add_column("nat_translations", sa.Column("src_port_to", sa.Integer(), nullable=True))
    op.add_column("nat_translations", sa.Column("dst_port_to", sa.Integer(), nullable=True))
    for c in _STR_ALIAS:
        ln = 128 if c in ("category", "filter_rule") else (16 if c == "nat_reflection" else (32 if c == "pool_options" else 64))
        op.add_column("nat_translations", sa.Column(c, sa.String(length=ln), nullable=True))

    # 放寬 protocol CHECK
    op.execute(
        "DO $$ DECLARE c text; BEGIN "
        "FOR c IN SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'nat_translations'::regclass AND conname LIKE '%protocol%' LOOP "
        "EXECUTE 'ALTER TABLE nat_translations DROP CONSTRAINT ' || quote_ident(c); END LOOP; END $$;"
    )
    op.create_check_constraint(
        "nat_protocol_valid", "nat_translations",
        "protocol IN ('tcp','udp','any','icmp','esp','gre','tcp/udp')",
    )

    # Circuit 非對稱頻寬
    op.add_column("circuits", sa.Column("up_kbps", sa.Integer(), nullable=True))
    op.add_column("circuits", sa.Column("down_kbps", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("circuits", "down_kbps")
    op.drop_column("circuits", "up_kbps")
    op.execute(
        "DO $$ DECLARE c text; BEGIN "
        "FOR c IN SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'nat_translations'::regclass AND conname LIKE '%protocol%' LOOP "
        "EXECUTE 'ALTER TABLE nat_translations DROP CONSTRAINT ' || quote_ident(c); END LOOP; END $$;"
    )
    op.create_check_constraint(
        "nat_protocol_valid", "nat_translations",
        "protocol IN ('tcp','udp','any')",
    )
    for c in reversed(_STR_ALIAS):
        op.drop_column("nat_translations", c)
    op.drop_column("nat_translations", "dst_port_to")
    op.drop_column("nat_translations", "src_port_to")
    op.drop_column("nat_translations", "ip_version")
    for c in reversed(_BOOL_COLS):
        op.drop_column("nat_translations", c)
