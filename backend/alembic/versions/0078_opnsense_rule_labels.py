"""opnsense rule labels (Graylog firewall DSV)

Revision ID: 0078_opnsense_rule_labels
Revises: 0077_cert_agent_recent_sources
Create Date: 2026-06-18

新增 opnsense_rule_labels（從 pf_statistics 解析的 規則 label→alias 對照，給 Graylog DSV
用 filterlog rid 反查 alias）+ opnsense_firewalls.expose_dsv（每台防火牆是否對外提供 DSV）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0078_opnsense_rule_labels"
down_revision: str | None = "0077_cert_agent_recent_sources"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "opnsense_firewalls",
        sa.Column("expose_dsv", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_table(
        "opnsense_rule_labels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("firewall_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("opnsense_firewalls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=True),
        sa.Column("interface", sa.String(length=64), nullable=True),
        sa.Column("alias_names", postgresql.JSONB(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("firewall_id", "label", name="opnsense_rule_label_unique"),
    )
    op.create_index("ix_opnsense_rule_labels_firewall_id", "opnsense_rule_labels", ["firewall_id"])


def downgrade() -> None:
    op.drop_index("ix_opnsense_rule_labels_firewall_id", table_name="opnsense_rule_labels")
    op.drop_table("opnsense_rule_labels")
    op.drop_column("opnsense_firewalls", "expose_dsv")
