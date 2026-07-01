"""ip ssh distribution + user connection-management capability

Revision ID: 0081_ip_ssh_and_user_capability
Revises: 0080_cert_agent_device
Create Date: 2026-06-21

SSH 連線管理功能：
- ip_addresses.ssh_enabled：是否對此 IP 啟用 SSH 連線（控制詳情頁 SSH 按鈕是否出現）。
- ip_addresses.ssh_host_key：TOFU 信任後釘選的 host key（單行 known_host 格式；非機密）。
- users.can_ssh：獨立的「連線管理權限」——除了 admin / 對該 IP 有寫入權者，
  另可單獨授予此能力讓使用者對其可檢視的 SSH-enabled IP 開終端機。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0081_ip_ssh_and_user_capability"
down_revision: str | None = "0080_cert_agent_device"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ip_addresses",
        sa.Column(
            "ssh_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column(
        "ip_addresses",
        sa.Column("ssh_host_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "can_ssh", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "can_ssh")
    op.drop_column("ip_addresses", "ssh_host_key")
    op.drop_column("ip_addresses", "ssh_enabled")
