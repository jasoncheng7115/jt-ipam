"""background_tasks.trigger — 區分排程 / 手動觸發

作業表格原本只記錄手動 / API 觸發的作業；排程 timer（jt-ipam-sync.py）直接寫各整合表、
不建作業紀錄，導致 UI 看不到排程同步、誤以為排程沒生效。新增 trigger 欄（manual /
scheduled）：排程每輪對每個整合 upsert 一列心跳，前端據此顯示「觸發方式」。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0095_bgtask_trigger"
down_revision: str | None = "0094_librenms_verify_tls"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "background_tasks",
        sa.Column(
            "trigger",
            sa.String(length=16),
            nullable=False,
            server_default="manual",
        ),
    )
    op.create_check_constraint(
        "background_task_trigger_valid",
        "background_tasks",
        "trigger IN ('manual','scheduled')",
    )


def downgrade() -> None:
    op.drop_constraint("background_task_trigger_valid", "background_tasks", type_="check")
    op.drop_column("background_tasks", "trigger")
