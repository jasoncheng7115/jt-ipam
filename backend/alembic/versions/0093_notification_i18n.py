"""notifications i18n — store title_key/body_key + params so the bell renders in the UI language

站內通知原本只存固定字串（title/body），切語言不會翻譯。新增 title_key/body_key（i18n key）
與 params（JSONB 插值參數）：前端有 key 就 t(key, params) 依當前語言渲染，沒有就退回原字串
（向下相容舊通知）。Email 與退路仍用既有 title/body（預設語言）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0093_notification_i18n"
down_revision: str | None = "0092_ip_bmc_enabled"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("title_key", sa.String(length=64), nullable=True))
    op.add_column("notifications", sa.Column("body_key", sa.String(length=64), nullable=True))
    op.add_column("notifications", sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("notifications", "params")
    op.drop_column("notifications", "body_key")
    op.drop_column("notifications", "title_key")
