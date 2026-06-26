"""scan_agents.tools — agent dependency-tool inventory (installed + version)

Revision ID: 0086_scan_agent_tools
Revises: 0085_vm_unique_by_vmid
Create Date: 2026-06-26

掃描代理回報它相依的外部工具盤點（nmap / nmblookup / nbtscan / avahi-resolve / ping / ip…），
每筆 {name, installed, version}，前端「掃描代理」頁顯示「相依套件 N/M」+ 詳情（哪些裝了什麼版本、哪些缺）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0086_scan_agent_tools"
down_revision: str | None = "0085_vm_unique_by_vmid"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("scan_agents", sa.Column("tools", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("scan_agents", "tools")
