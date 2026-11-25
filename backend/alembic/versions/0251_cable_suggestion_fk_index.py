"""cable_suggestions.dismissed_by 補索引

Revision ID: 0251_cable_suggestion_fk_index
Revises: 0250_cable_inference
Create Date: 2026-11-25

外鍵欄位都要有索引（刪使用者時 ON DELETE SET NULL 才不會整表掃描；tests/test_fk_indexes.py 守門）。
0250 漏了這一欄，而且已經部署，所以另開一版補上。
"""
from __future__ import annotations

from alembic import op

revision: str = "0251_cable_suggestion_fk_index"
down_revision: str | None = "0250_cable_inference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_cable_suggestions_dismissed_by", "cable_suggestions", ["dismissed_by"])


def downgrade() -> None:
    op.drop_index("ix_cable_suggestions_dismissed_by", table_name="cable_suggestions")
