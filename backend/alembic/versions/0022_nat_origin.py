"""NAT translations: source_origin + external_id (for OPNsense pull / phpIPAM tag).

Revision ID: 0022_nat_origin
Revises: 0021_opnsense_rules
Create Date: 2026-05-27 11:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_nat_origin"
down_revision: str | None = "0021_opnsense_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("nat_translations",
                  sa.Column("source_origin", sa.String(64)))
    op.add_column("nat_translations",
                  sa.Column("external_id", sa.String(64)))
    op.create_index("ix_nat_translations_source_origin",
                    "nat_translations", ["source_origin"])
    op.create_index("ix_nat_translations_external_id",
                    "nat_translations", ["external_id"])
    op.create_unique_constraint("nat_origin_external_unique",
                                "nat_translations", ["source_origin", "external_id"])


def downgrade() -> None:
    op.drop_constraint("nat_origin_external_unique", "nat_translations", type_="unique")
    op.drop_index("ix_nat_translations_external_id", table_name="nat_translations")
    op.drop_index("ix_nat_translations_source_origin", table_name="nat_translations")
    op.drop_column("nat_translations", "external_id")
    op.drop_column("nat_translations", "source_origin")
