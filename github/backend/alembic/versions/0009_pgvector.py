"""pgvector extension + embedding columns.

Revision ID: 0009_pgvector
Revises: 0008_librenms
Create Date: 2026-05-09 04:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_pgvector"
down_revision: str | None = "0008_librenms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector extension（需要 PG ≥ 14 + 已 apt install postgresql-16-pgvector）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 用 raw text 加 vector(768) — SQLAlchemy 對 vector 型別需 pgvector lib
    op.execute("ALTER TABLE subnets ADD COLUMN description_embedding vector(768)")
    op.execute(
        "ALTER TABLE ip_addresses ADD COLUMN description_embedding vector(768)"
    )
    op.execute(
        "ALTER TABLE devices ADD COLUMN description_embedding vector(768)"
    )

    # IVFFLAT index（搜尋時近似最相近向量）
    # 注意：建 index 前資料表內要有些向量資料；空表建 index 也 OK 但效率低
    op.execute(
        "CREATE INDEX ix_subnets_description_embedding "
        "ON subnets USING ivfflat (description_embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX ix_ip_addresses_description_embedding "
        "ON ip_addresses USING ivfflat (description_embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX ix_devices_description_embedding "
        "ON devices USING ivfflat (description_embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_devices_description_embedding")
    op.execute("DROP INDEX IF EXISTS ix_ip_addresses_description_embedding")
    op.execute("DROP INDEX IF EXISTS ix_subnets_description_embedding")
    op.execute("ALTER TABLE devices DROP COLUMN IF EXISTS description_embedding")
    op.execute(
        "ALTER TABLE ip_addresses DROP COLUMN IF EXISTS description_embedding"
    )
    op.execute("ALTER TABLE subnets DROP COLUMN IF EXISTS description_embedding")
    # 不 DROP EXTENSION vector — 其他 schema 可能也用
