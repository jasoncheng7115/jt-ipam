"""ssh_credentials — by-user SSH credential store (envelope-encrypted)

Revision ID: 0082_ssh_credentials
Revises: 0081_ip_ssh_and_user_capability
Create Date: 2026-06-21

每位使用者個別保管自己的 SSH 帳密／私鑰。密碼／私鑰／passphrase 各自信封加密
（per-field 隨機 DEK，DEK 由主 KEK 包覆）存於 secrets_enc（JSONB）。
scope：target_ip_id 綁某 IP；NULL = 個人預設。owner/target 刪除時連帶刪除。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0082_ssh_credentials"
down_revision: str | None = "0081_ip_ssh_and_user_capability"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ssh_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.String(length=8), nullable=False),
        sa.Column("target_ip_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("secrets_enc", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_ip_id"], ["ip_addresses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ssh_credentials_owner_user_id", "ssh_credentials", ["owner_user_id"])
    op.create_index("ix_ssh_credentials_target_ip_id", "ssh_credentials", ["target_ip_id"])


def downgrade() -> None:
    op.drop_index("ix_ssh_credentials_target_ip_id", table_name="ssh_credentials")
    op.drop_index("ix_ssh_credentials_owner_user_id", table_name="ssh_credentials")
    op.drop_table("ssh_credentials")
