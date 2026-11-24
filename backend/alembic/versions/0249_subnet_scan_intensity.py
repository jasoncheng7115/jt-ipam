"""subnets.scan_intensity：偵測強度（一般 / 對端點防護友善）

Revision ID: 0249_subnet_scan_intensity
Revises: 0248_power_profile_fk_index
Create Date: 2026-11-24

客戶的 Symantec Endpoint Protection 在 OS 偵測與連接埠探測打開時把掃描代理當成攻擊封鎖。gentle 模式下
代理不做 OS 指紋、放慢、只看少數埠。既有子網路一律 normal（行為不變）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0249_subnet_scan_intensity"
down_revision: str | None = "0248_power_profile_fk_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subnets", sa.Column("scan_intensity", sa.String(16), nullable=False, server_default="normal"))
    op.create_check_constraint("ck_subnets_scan_intensity", "subnets", "scan_intensity IN ('normal','gentle')")


def downgrade() -> None:
    op.drop_constraint("ck_subnets_scan_intensity", "subnets", type_="check")
    op.drop_column("subnets", "scan_intensity")
