"""device_power_ports（裝置電源埠 → PDU 插座，NetBox PowerPort 風）

Revision ID: 0066_device_power_ports
Revises: 0065_user_pref_pinned
Create Date: 2026-06-05

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0066_device_power_ports"
down_revision: str | None = "0065_user_pref_pinned"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "device_power_ports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("device_id", UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("outlet_id", UUID(as_uuid=True),
                  sa.ForeignKey("power_outlets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("max_watts", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("device_id", "name", name="device_power_port_unique_name"),
    )
    op.create_index("ix_device_power_ports_device_id", "device_power_ports", ["device_id"])
    op.create_index("ix_device_power_ports_outlet_id", "device_power_ports", ["outlet_id"])


def downgrade() -> None:
    op.drop_index("ix_device_power_ports_outlet_id", table_name="device_power_ports")
    op.drop_index("ix_device_power_ports_device_id", table_name="device_power_ports")
    op.drop_table("device_power_ports")
