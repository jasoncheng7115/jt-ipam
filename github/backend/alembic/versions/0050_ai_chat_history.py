"""AI chat 歷程：ai_chat_conversations + ai_chat_messages。

Revision ID: 0050_ai_chat_history
Revises: 0049_rack_numbering_face
Create Date: 2026-05-31 16:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050_ai_chat_history"
down_revision: str | None = "0049_rack_numbering_face"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_chat_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE",
                                name="fk_ai_chat_conversations_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_ai_chat_conversations"),
    )
    op.create_index("ix_ai_chat_conversations_user_id", "ai_chat_conversations",
                    ["user_id"])
    op.create_index("ix_ai_chat_conversations_updated_at", "ai_chat_conversations",
                    ["updated_at"])

    op.create_table(
        "ai_chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_chat_conversations.id"],
                                ondelete="CASCADE",
                                name="fk_ai_chat_messages_conversation_id_ai_chat_conversations"),
        sa.PrimaryKeyConstraint("id", name="pk_ai_chat_messages"),
    )
    op.create_index("ix_ai_chat_messages_conversation_id", "ai_chat_messages",
                    ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_chat_messages_conversation_id", "ai_chat_messages")
    op.drop_table("ai_chat_messages")
    op.drop_index("ix_ai_chat_conversations_updated_at", "ai_chat_conversations")
    op.drop_index("ix_ai_chat_conversations_user_id", "ai_chat_conversations")
    op.drop_table("ai_chat_conversations")
