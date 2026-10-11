"""dns_compare_groups：內容應該一致的 DNS 伺服器放在同一個比對群組，合併顯示、每次拉取後比對並告警

2026-10-10 使用者：兩台互相複寫的 DNS 都接進來時，紀錄頁、異常偵測、IP 變更評估都會出現兩份；
想把它們標成一組，相同的合併成一筆，並在每次拉取時檢查各台是不是一樣，不一樣的列出來、發告警。
（叫「比對群組」不叫「同步群組」：jt-ipam 不會在伺服器之間同步紀錄。）

- dns_compare_groups：名稱、是否通知、寬限分鐘數（複寫延遲）、最後一次比對的結果
- dns_servers.compare_group_id：屬於哪一組
- dns_compare_group_diffs：目前組內不一樣的 zone 或紀錄（第一次、最後一次看到的時間）
- dns_records.name_norm／value_norm：正規化後的名稱與值（不同廠牌的大小寫、結尾點、IPv6 寫法不同），
  拉取時填；既有紀錄在下一輪拉取時補上，所以這裡不回填

Revision ID: 0203_dns_compare_groups
Revises: 0202_public_endpoint_tokens
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0203_dns_compare_groups"
down_revision: str | None = "0202_public_endpoint_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dns_compare_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("notify_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("grace_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_status", sa.String(16)),
        sa.Column("last_message", sa.Text()),
        sa.Column("alerted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("grace_minutes >= 0 AND grace_minutes <= 1440", name="ck_dns_compare_groups_grace"),
    )
    op.add_column("dns_servers", sa.Column(
        "compare_group_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("dns_compare_groups.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_dns_servers_compare_group_id", "dns_servers", ["compare_group_id"])
    op.create_table(
        "dns_compare_group_diffs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("group_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("dns_compare_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("zone", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("type", sa.String(8), nullable=False, server_default=""),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("present_on", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("missing_on", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('zone','record')", name="ck_dns_compare_group_diffs_kind"),
        sa.UniqueConstraint("group_id", "key_hash", name="uq_dns_compare_group_diff"),
    )
    op.create_index("ix_dns_compare_group_diffs_group_id", "dns_compare_group_diffs", ["group_id"])
    op.add_column("dns_records", sa.Column("name_norm", sa.String(255), nullable=True))
    op.add_column("dns_records", sa.Column("value_norm", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("dns_records", "value_norm")
    op.drop_column("dns_records", "name_norm")
    op.drop_index("ix_dns_compare_group_diffs_group_id", table_name="dns_compare_group_diffs")
    op.drop_table("dns_compare_group_diffs")
    op.drop_index("ix_dns_servers_compare_group_id", table_name="dns_servers")
    op.drop_column("dns_servers", "compare_group_id")
    op.drop_table("dns_compare_groups")
