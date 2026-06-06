"""circuit 固定 IP 欄位 + 關聯裝置

Revision ID: 0067_circuit_fixed_ip
Revises: 0066_device_power_ports
Create Date: 2026-06-06

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0067_circuit_fixed_ip"
down_revision: str | None = "0066_device_power_ports"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("circuits", sa.Column("ip_address", sa.String(64), nullable=True))
    op.add_column("circuits", sa.Column("gateway", sa.String(64), nullable=True))
    op.add_column("circuits", sa.Column("netmask", sa.String(64), nullable=True))
    op.add_column("circuits", sa.Column("dns_servers", sa.Text(), nullable=True))
    op.add_column(
        "circuits",
        sa.Column("device_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "circuits_device_id_fkey", "circuits", "devices",
        ["device_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_circuits_device_id", "circuits", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_circuits_device_id", table_name="circuits")
    op.drop_constraint("circuits_device_id_fkey", "circuits", type_="foreignkey")
    op.drop_column("circuits", "device_id")
    op.drop_column("circuits", "dns_servers")
    op.drop_column("circuits", "netmask")
    op.drop_column("circuits", "gateway")
    op.drop_column("circuits", "ip_address")
