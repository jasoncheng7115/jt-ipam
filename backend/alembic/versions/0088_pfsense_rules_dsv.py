"""pfSense Phase 2: firewall rules sync + Graylog DSV expose

Revision ID: 0088_pfsense_rules_dsv
Revises: 0087_pfsense_firewall
Create Date: 2026-06-27

pfsense_firewalls 加：expose_dsv（是否對外提供 Graylog DSV）、sync_rules（同步防火牆規則）、
rules（JSONB，存精簡規則 [{tracker, descr, type, interface, …}] 供檢視 + tracker→descr DSV）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0088_pfsense_rules_dsv"
down_revision: str | None = "0087_pfsense_firewall"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("pfsense_firewalls",
                  sa.Column("expose_dsv", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("pfsense_firewalls",
                  sa.Column("sync_rules", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("pfsense_firewalls", sa.Column("rules", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("pfsense_firewalls", "rules")
    op.drop_column("pfsense_firewalls", "sync_rules")
    op.drop_column("pfsense_firewalls", "expose_dsv")
