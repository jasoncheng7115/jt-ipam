"""ip vnc enable (reuse credential vault protocol='vnc')

Revision ID: 0084_ip_vnc_enabled
Revises: 0083_ip_rdp_cred_protocol
Create Date: 2026-06-22

VNC 連線管理（比照 SSH/RDP，aardwolf 選用相依，同一個 VNCConnection）：
- ip_addresses.vnc_enabled：是否對此 IP 啟用 VNC 連線。
憑證金庫沿用 ssh_credentials（protocol='vnc'），連線管理權限沿用 users.can_ssh，故不新增其他欄位。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0084_ip_vnc_enabled"
down_revision: str | None = "0083_ip_rdp_cred_protocol"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ip_addresses",
        sa.Column(
            "vnc_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )


def downgrade() -> None:
    op.drop_column("ip_addresses", "vnc_enabled")
