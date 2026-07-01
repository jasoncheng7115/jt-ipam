"""ip rdp enable + credential protocol/domain (reuse vault for RDP)

Revision ID: 0083_ip_rdp_and_credential_protocol
Revises: 0082_ssh_credentials
Create Date: 2026-06-21

RDP 連線管理（比照 SSH，aardwolf 選用相依）：
- ip_addresses.rdp_enabled：是否對此 IP 啟用 RDP 連線（控制詳情頁 RDP 按鈕是否出現）。
- ssh_credentials.protocol：沿用同一個 by-user 憑證金庫保管 RDP 帳密（'ssh' / 'rdp'）。
- ssh_credentials.domain：RDP 網域（選填，僅 RDP 用）。
連線管理權限沿用既有 users.can_ssh（泛用遠端主控權限），故本次不新增 user 欄位。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0083_ip_rdp_cred_protocol"
down_revision: str | None = "0082_ssh_credentials"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ip_addresses",
        sa.Column(
            "rdp_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column(
        "ssh_credentials",
        sa.Column(
            "protocol", sa.String(length=8), nullable=False, server_default=sa.text("'ssh'")
        ),
    )
    op.add_column(
        "ssh_credentials",
        sa.Column("domain", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ssh_credentials", "domain")
    op.drop_column("ssh_credentials", "protocol")
    op.drop_column("ip_addresses", "rdp_enabled")
