"""device_ports（連接埠 + front/rear pass-through，給 Cable Trace 用）

Revision ID: 0063_device_ports
Revises: 0062_device_rack_side
Create Date: 2026-06-03

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0063_device_ports"
down_revision: str | None = "0062_device_rack_side"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "device_ports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("device_id", UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("type", sa.String(16), nullable=False, server_default="network"),
        sa.Column("peer_port_id", UUID(as_uuid=True),
                  sa.ForeignKey("device_ports.id", ondelete="SET NULL"), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("device_id", "name", name="device_port_unique_name"),
    )
    op.create_index("ix_device_ports_device_id", "device_ports", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_device_ports_device_id", table_name="device_ports")
    op.drop_table("device_ports")
