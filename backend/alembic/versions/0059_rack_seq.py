"""racks.seq — 機櫃排序編號（多機櫃並排時，編號小的排左邊）

Revision ID: 0059_rack_seq
Revises: 0058_vpn_pairing_method
Create Date: 2026-06-03

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0059_rack_seq"
down_revision: str | None = "0058_vpn_pairing_method"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("racks", sa.Column("seq", sa.Integer(), nullable=True))
    op.create_index("ix_racks_seq", "racks", ["seq"])


def downgrade() -> None:
    op.drop_index("ix_racks_seq", table_name="racks")
    op.drop_column("racks", "seq")
