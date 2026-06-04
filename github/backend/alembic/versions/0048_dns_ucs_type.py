"""DNS：新增 univention_ucs 類型（放寬 dns_servers type CHECK）。

Revision ID: 0048_dns_ucs_type
Revises: 0047_rack_pos_size
Create Date: 2026-05-31 14:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0048_dns_ucs_type"
down_revision: str | None = "0047_rack_pos_size"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "type IN ('powerdns','bind9','unbound_opnsense','windows_dns')"
_NEW = "type IN ('powerdns','bind9','unbound_opnsense','windows_dns','univention_ucs')"


def upgrade() -> None:
    op.drop_constraint("ck_dns_servers_type_valid", "dns_servers", type_="check")
    op.create_check_constraint("ck_dns_servers_type_valid", "dns_servers", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_dns_servers_type_valid", "dns_servers", type_="check")
    op.create_check_constraint("ck_dns_servers_type_valid", "dns_servers", _OLD)
