"""維護排程建立者欄位加索引（2026-11-02）

外鍵欄位都要有索引：刪使用者時 ON DELETE SET NULL 要找出他建的維護，沒有索引就整表掃描
（tests/test_fk_indexes.py 守著；0235 漏了這一個）。

Revision ID: 0236_maint_created_by_idx
Revises: 0235_maintenance_windows
"""
from __future__ import annotations

from alembic import op

revision: str = "0236_maint_created_by_idx"
down_revision: str | None = "0235_maintenance_windows"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index("ix_maintenance_windows_created_by", "maintenance_windows", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_maintenance_windows_created_by", table_name="maintenance_windows")
