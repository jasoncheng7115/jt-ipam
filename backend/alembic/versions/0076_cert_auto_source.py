"""certificate auto-fetch source: source_type / source_config / fetch interval

Revision ID: 0076_cert_auto_source
Revises: 0075_cert_distribution
Create Date: 2026-06-13

憑證自動抓取來源(URL / SFTP):定期或手動從來源拉新版,fingerprint 不同才存成新版本。
帳密 / SSH key 走 encrypted_secret(object_type='certificate')。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0076_cert_auto_source"
down_revision: str | None = "0075_cert_distribution"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("certificates", sa.Column(
        "source_type", sa.String(16), server_default="none", nullable=False))
    op.add_column("certificates", sa.Column("source_config", postgresql.JSONB()))
    op.add_column("certificates", sa.Column(
        "fetch_interval_seconds", sa.Integer(), server_default="86400", nullable=False))
    op.add_column("certificates", sa.Column("last_fetch_at", sa.DateTime(timezone=True)))
    op.add_column("certificates", sa.Column("last_fetch_error", sa.Text()))


def downgrade() -> None:
    for col in ("last_fetch_error", "last_fetch_at", "fetch_interval_seconds",
                "source_config", "source_type"):
        op.drop_column("certificates", col)
