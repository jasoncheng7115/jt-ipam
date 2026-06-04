"""IP request workflow tables.

Revision ID: 0004_ip_requests
Revises: 0003_notifications
Create Date: 2026-05-09 01:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_ip_requests"
down_revision: str | None = "0003_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ip_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("requester_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("approver_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("subnet_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("subnets.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("requested_ip", postgresql.INET()),
        sa.Column("hostname", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("allocated_ip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ip_addresses.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column("rejected_reason", sa.Text()),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','cancelled','fulfilled')",
            name="ck_ip_requests_status_valid",
        ),
    )

    op.create_table(
        "ip_request_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("request_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ip_requests.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("ip_request_events")
    op.drop_table("ip_requests")
