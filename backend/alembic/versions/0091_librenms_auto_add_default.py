"""librenms auto_add_devices defaults true (auto-link devices on every sync)

Revision ID: 0091_librenms_auto_add
Revises: 0090_ip_is_dhcp_server
Create Date: 2026-06-30

LibreNMS 同步時自動把裝置 match-or-create 成 jt-ipam Device，本來預設關閉、要再手動按「連結裝置」。
改為預設開啟（與 auto_create_ips 一致），並把既有實例一併打開，這樣每次同步／拉取就順帶連結，不必再人工按一次。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0091_librenms_auto_add"
down_revision: str | None = "0090_ip_is_dhcp_server"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("UPDATE librenms_instances SET auto_add_devices = true")
    op.alter_column(
        "librenms_instances", "auto_add_devices",
        server_default=sa.text("true"),
    )


def downgrade() -> None:
    op.alter_column(
        "librenms_instances", "auto_add_devices",
        server_default=sa.text("false"),
    )
