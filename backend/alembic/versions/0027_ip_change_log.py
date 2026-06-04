"""IP 異動記錄表 ip_change_log（高頻事件，不進 audit 雜湊鏈）。

Revision ID: 0027_ip_change_log
Revises: 0026_oui_vendors
Create Date: 2026-05-29 09:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_ip_change_log"
down_revision: str | None = "0026_oui_vendors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ip_change_log",
        sa.Column(
            "id", sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(), primary_key=True,
        ),
        sa.Column(
            "ip_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ip_addresses.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "subnet_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subnets.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("ip_text", sa.Text(), nullable=False),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=16), server_default="system", nullable=False),
        sa.Column(
            "actor_user_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_ip_change_log_ip_id", "ip_change_log", ["ip_id"])
    op.create_index("ix_ip_change_log_subnet_id", "ip_change_log", ["subnet_id"])
    op.create_index("ix_ip_change_log_created_at", "ip_change_log", ["created_at"])
    op.create_index("ix_ip_change_log_ip_created", "ip_change_log", ["ip_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ip_change_log_ip_created", table_name="ip_change_log")
    op.drop_index("ix_ip_change_log_created_at", table_name="ip_change_log")
    op.drop_index("ix_ip_change_log_subnet_id", table_name="ip_change_log")
    op.drop_index("ix_ip_change_log_ip_id", table_name="ip_change_log")
    op.drop_table("ip_change_log")
