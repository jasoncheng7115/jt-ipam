"""cert_agents.device_id

Revision ID: 0080_cert_agent_device
Revises: 0079_librenms_auto_create_ips
Create Date: 2026-06-18

cert_agents 加 device_id（可對應到 jt-ipam 裝置）：讓派送代理清單 / 唯讀現況頁的
代理名稱可點去裝置詳情；裝置刪除時 SET NULL。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0080_cert_agent_device"
down_revision: str | None = "0079_librenms_auto_create_ips"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "cert_agents",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_cert_agents_device_id", "cert_agents", "devices",
        ["device_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_cert_agents_device_id", "cert_agents", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_cert_agents_device_id", table_name="cert_agents")
    op.drop_constraint("fk_cert_agents_device_id", "cert_agents", type_="foreignkey")
    op.drop_column("cert_agents", "device_id")
