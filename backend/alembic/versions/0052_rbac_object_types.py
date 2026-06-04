"""RBAC：permissions 擴充 7 種物件類型 + object_id 可為 NULL（全部 wildcard）。

Revision ID: 0052_rbac_object_types
Revises: 0051_device_fqdn
Create Date: 2026-06-01 01:30:00

用 raw SQL（含 IF EXISTS）避開 alembic naming-convention 對既有約束名稱的二次套用問題。
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0052_rbac_object_types"
down_revision: str | None = "0051_device_fqdn"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE permissions ALTER COLUMN object_id DROP NOT NULL")
    # 不論既有 object_type CHECK 叫什麼名字（可能被 naming-convention 二次加首碼），全部砍掉
    op.execute(
        "DO $$ DECLARE c text; BEGIN "
        "FOR c IN SELECT conname FROM pg_constraint "
        "WHERE conrelid='permissions'::regclass AND contype='c' "
        "AND pg_get_constraintdef(oid) ILIKE '%object_type%' "
        "LOOP EXECUTE 'ALTER TABLE permissions DROP CONSTRAINT ' || quote_ident(c); END LOOP; END $$;"
    )
    op.execute(
        "ALTER TABLE permissions ADD CONSTRAINT permission_object_type_valid "
        "CHECK (object_type IN ('customer','section','subnet','ip','device','rack','location'))"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_permission_wildcard "
        "ON permissions (object_type, principal_type, principal_id) WHERE object_id IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_permission_wildcard")
    op.execute("ALTER TABLE permissions DROP CONSTRAINT IF EXISTS permission_object_type_valid")
    op.execute("DELETE FROM permissions WHERE object_id IS NULL")
    op.execute(
        "ALTER TABLE permissions ADD CONSTRAINT permission_object_type_valid "
        "CHECK (object_type IN ('section','subnet'))"
    )
    op.execute("ALTER TABLE permissions ALTER COLUMN object_id SET NOT NULL")
