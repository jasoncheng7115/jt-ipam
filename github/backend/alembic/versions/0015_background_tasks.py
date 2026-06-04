"""Background tasks (UI 任務區).

Revision ID: 0015_background_tasks
Revises: 0014_wazuh
Create Date: 2026-05-26 15:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_background_tasks"
down_revision: str | None = "0014_wazuh"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "background_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("target_type", sa.String(64)),
        sa.Column("target_id", postgresql.UUID(as_uuid=True)),
        sa.Column("target_label", sa.Text),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("progress", sa.Integer, nullable=False, server_default="0"),
        sa.Column("summary", postgresql.JSONB),
        sa.Column("error", sa.Text),
        sa.Column("queued_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending','running','succeeded','failed','cancelled')",
            name="background_task_status_valid",
        ),
        sa.CheckConstraint(
            "progress BETWEEN 0 AND 100",
            name="background_task_progress_range",
        ),
    )
    op.create_index("ix_background_tasks_kind", "background_tasks", ["kind"])
    op.create_index("ix_background_tasks_status", "background_tasks", ["status"])
    op.create_index("ix_background_tasks_queued_at", "background_tasks", ["queued_at"])


def downgrade() -> None:
    op.drop_index("ix_background_tasks_queued_at", table_name="background_tasks")
    op.drop_index("ix_background_tasks_status", table_name="background_tasks")
    op.drop_index("ix_background_tasks_kind", table_name="background_tasks")
    op.drop_table("background_tasks")
