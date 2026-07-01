"""librenms_instances.auto_create_ips

Revision ID: 0079_librenms_auto_create_ips
Revises: 0078_opnsense_rule_labels
Create Date: 2026-06-18

新增 librenms_instances.auto_create_ips（預設 true）：同步裝置時把落在「既有且符合
scope」子網路內的裝置主 IP 自動建成 IPAddress（discovery_source='librenms'）。
原本 LibreNMS 同步只 stamp 既有 IP、從不建立，導致只接 LibreNMS、未佈掃描代理的
環境子網路內 0 個 IP、即時狀態全 0。預設開啟＝使用者預期「接了 NMS 就會長出 IP」。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0079_librenms_auto_create_ips"
down_revision: str | None = "0078_opnsense_rule_labels"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "librenms_instances",
        sa.Column(
            "auto_create_ips", sa.Boolean(), nullable=False, server_default="true",
        ),
    )


def downgrade() -> None:
    op.drop_column("librenms_instances", "auto_create_ips")
