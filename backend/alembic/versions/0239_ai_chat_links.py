"""AI 對話回答下方的「前往」按鈕存進對話歷程（2026-11-05）

使用者問「功能在哪」時，畫面地圖工具給的站內連結會顯示成回答下方的按鈕；存下來，重新打開對話也看得到。

Revision ID: 0239_ai_chat_links
Revises: 0238_agent_tls_mode
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0239_ai_chat_links"
down_revision: str | None = "0238_agent_tls_mode"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("ai_chat_messages", sa.Column("links", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_chat_messages", "links")
