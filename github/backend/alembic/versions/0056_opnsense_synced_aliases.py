"""opnsense_synced_aliases — pull alias definitions from OPNsense for viewing

Revision ID: 0056_opnsense_synced_aliases
Revises: 0055_drop_pref_online_grace
Create Date: 2026-06-01

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0056_opnsense_synced_aliases"
down_revision: str | None = "0055_drop_pref_online_grace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "opnsense_synced_aliases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("firewall_id", UUID(as_uuid=True),
                  sa.ForeignKey("opnsense_firewalls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("alias_type", sa.String(32), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("content", JSONB(), nullable=True),
        sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("opn_uuid", sa.String(64), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("firewall_id", "name", name="opnsense_synced_alias_unique"),
    )
    op.create_index("ix_opnsense_synced_aliases_firewall_id", "opnsense_synced_aliases", ["firewall_id"])


def downgrade() -> None:
    op.drop_index("ix_opnsense_synced_aliases_firewall_id", table_name="opnsense_synced_aliases")
    op.drop_table("opnsense_synced_aliases")
