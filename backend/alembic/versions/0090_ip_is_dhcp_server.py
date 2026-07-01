"""ip is_dhcp_server — manual marker for "this IP is a DHCP server"

Revision ID: 0090_ip_is_dhcp_server
Revises: 0089_ip_novnc_enabled
Create Date: 2026-06-29

在 IP 位址清單把特殊角色的 IP 視覺化（粗體 / 圖示）。本欄是「手動標記某 IP 為 DHCP 伺服器」；
另外兩種（子網路閘道、落在 DHCP 範圍/租約內）以及「自動：對應到已整合 OPNsense/pfSense 防火牆 IP」
都由讀取端即時推導，不需新欄位。預設行為：OPNsense/pfSense 有對應防火牆 IP 就自動顯示，使用者也可
用此手動旗標額外標記其它 DHCP 主機。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0090_ip_is_dhcp_server"
down_revision: str | None = "0089_ip_novnc_enabled"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ip_addresses",
        sa.Column(
            "is_dhcp_server", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )


def downgrade() -> None:
    op.drop_column("ip_addresses", "is_dhcp_server")
