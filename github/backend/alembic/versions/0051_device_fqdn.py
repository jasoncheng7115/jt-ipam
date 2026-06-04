"""Device.fqdn（完整網域名稱）。

Revision ID: 0051_device_fqdn
Revises: 0050_ai_chat_history
Create Date: 2026-06-01 00:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051_device_fqdn"
down_revision: str | None = "0050_ai_chat_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("fqdn", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "fqdn")
