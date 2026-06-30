"""ip bmc_enabled — BMC KV (IPMI SOL) console toggle per IP

Revision ID: 0092_ip_bmc_enabled
Revises: 0091_librenms_auto_add
Create Date: 2026-07-01

OOB BMC 主控台（IPMI 2.0 SOL，鍵盤 + 文字畫面；非破壞、不含滑鼠/電源）。針對「BMC 管理 IP」開關。
連線帳密走既有憑證金庫（protocol='bmc'）；權限沿用 can_ssh + 物件層級授權（與 SSH 同等級）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0092_ip_bmc_enabled"
down_revision: str | None = "0091_librenms_auto_add"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ip_addresses",
        sa.Column("bmc_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("ip_addresses", "bmc_enabled")
