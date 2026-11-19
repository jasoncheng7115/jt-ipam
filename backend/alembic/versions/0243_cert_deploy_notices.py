"""憑證更換成功的通知改成一批一則：先記在 cert_deploy_notices，排程合併後再送（使用者 2026-11-17）

Revision ID: 0243_cert_deploy_notices
Revises: 0242_checkmk
Create Date: 2026-11-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0243_cert_deploy_notices"
down_revision: str | None = "0242_checkmk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cert_deploy_notices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("certificate", sa.String(255), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("target", sa.String(128), nullable=False),     # 代理或推送目標的名稱
        sa.Column("service", sa.String(64)),                     # nginx / pve / opnsense …
        sa.Column("source", sa.String(16), nullable=False),      # agent | push
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_cert_deploy_notices_pending", "cert_deploy_notices", ["notified_at", "certificate"])


def downgrade() -> None:
    op.drop_index("ix_cert_deploy_notices_pending", table_name="cert_deploy_notices")
    op.drop_table("cert_deploy_notices")
