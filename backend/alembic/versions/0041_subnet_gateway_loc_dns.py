"""Subnet 加 gateway / location_id / dns_servers（phpIPAM 對齊欄位）。

Revision ID: 0041_subnet_gw_loc_dns
Revises: 0040_subnet_archive
Create Date: 2026-05-30 14:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET, UUID

revision: str = "0041_subnet_gw_loc_dns"
down_revision: str | None = "0040_subnet_archive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("subnets", sa.Column("gateway", INET(), nullable=True))
    op.add_column("subnets", sa.Column("dns_servers", sa.Text(), nullable=True))
    op.add_column(
        "subnets",
        sa.Column("location_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_subnets_location", "subnets", "locations",
        ["location_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_subnets_location_id", "subnets", ["location_id"])


def downgrade() -> None:
    op.drop_index("ix_subnets_location_id", table_name="subnets")
    op.drop_constraint("fk_subnets_location", "subnets", type_="foreignkey")
    op.drop_column("subnets", "location_id")
    op.drop_column("subnets", "dns_servers")
    op.drop_column("subnets", "gateway")
