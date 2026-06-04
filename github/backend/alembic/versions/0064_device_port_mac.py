"""device_ports.mac_address（埠自身實體 MAC，來自 LibreNMS ifPhysAddress）

Revision ID: 0064_device_port_mac
Revises: 0063_device_ports
Create Date: 2026-06-03

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0064_device_port_mac"
down_revision: str | None = "0063_device_ports"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "device_ports",
        sa.Column("mac_address", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device_ports", "mac_address")
