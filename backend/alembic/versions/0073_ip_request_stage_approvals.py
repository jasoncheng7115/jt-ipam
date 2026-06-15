"""ip request multi-step approvals: ip_request_stage_approvals

Revision ID: 0073_ip_request_stage_approvals
Revises: 0072_integration_subnet_scope
Create Date: 2026-06-09

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0073_ip_request_stage_approvals"
down_revision: str | None = "0072_integration_subnet_scope"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ip_request_stage_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("request_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ip_requests.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("approver_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("request_id", "step_index", name="uq_ip_req_stage_step"),
    )


def downgrade() -> None:
    op.drop_table("ip_request_stage_approvals")
