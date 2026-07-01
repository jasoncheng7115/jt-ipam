"""ip novnc (PVE console) enable — reuse credential vault protocol='pve'

Revision ID: 0089_ip_novnc_enabled
Revises: 0088_pfsense_rules_dsv
Create Date: 2026-06-27

PVE 主控台連線管理（針對對應到 Proxmox VE 的 IP）：
- ip_addresses.novnc_enabled：是否對此 IP 啟用 PVE 主控台（qemu VM→noVNC 圖形 / lxc CT→xterm 終端機）。
連線時透過 PVE API（使用者輸入的 PVE 帳密，可選擇存進既有憑證金庫 protocol='pve'）取得 vncproxy/termproxy
ticket，後端對接 PVE vncwebsocket。權限沿用 users.can_ssh + 物件層級授權 + PVE 端自身權限把關，故不新增其他欄位。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0089_ip_novnc_enabled"
down_revision: str | None = "0088_pfsense_rules_dsv"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ip_addresses",
        sa.Column(
            "novnc_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )


def downgrade() -> None:
    op.drop_column("ip_addresses", "novnc_enabled")
