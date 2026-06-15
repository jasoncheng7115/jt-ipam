"""cert agent recent_sources: track distinct reporting source IPs

Revision ID: 0077_cert_agent_recent_sources
Revises: 0076_cert_auto_source
Create Date: 2026-06-15

派送代理近期回報來源 IP（list of {ip, at}）。同一把 enrollment key 被多台主機共用時,
近期會有多筆不同 IP → 後台據此警告「建議一台主機一把 Key」。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0077_cert_agent_recent_sources"
down_revision: str | None = "0076_cert_auto_source"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("cert_agents", sa.Column("recent_sources", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("cert_agents", "recent_sources")
