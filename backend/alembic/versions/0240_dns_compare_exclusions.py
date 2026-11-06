"""DNS 比對群組的排除：DHCP 範圍、管理員自訂的名稱與 IP（2026-11-05）

- exclude_dhcp_ranges：勾了之後，對應 IP 落在 DHCP 範圍內的紀錄不比對（預設不勾，既有群組行為不變）
- excluded_names：不比對的 FQDN（可用 *.example.com）
- excluded_ips：不比對的 IP、CIDR 或「起-迄」範圍

Revision ID: 0240_dns_compare_exclusions
Revises: 0239_ai_chat_links
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0240_dns_compare_exclusions"
down_revision: str | None = "0239_ai_chat_links"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("dns_compare_groups", sa.Column("exclude_dhcp_ranges", sa.Boolean(), nullable=False,
                                                  server_default=sa.false()))
    op.add_column("dns_compare_groups", sa.Column("excluded_names", postgresql.JSONB(), nullable=False,
                                                  server_default="[]"))
    op.add_column("dns_compare_groups", sa.Column("excluded_ips", postgresql.JSONB(), nullable=False,
                                                  server_default="[]"))


def downgrade() -> None:
    op.drop_column("dns_compare_groups", "excluded_ips")
    op.drop_column("dns_compare_groups", "excluded_names")
    op.drop_column("dns_compare_groups", "exclude_dhcp_ranges")
