"""佈線自動推斷：cables.source / evidence / verified_at、cable_suggestions

Revision ID: 0250_cable_inference
Revises: 0249_subnet_scan_intensity
Create Date: 2026-11-25

使用者 11-25：「連線路徑追蹤 除了人工設定 儘量我們系統內要有自動偵測 推斷 找出關係 自動連起來
有明確可以判斷的就自動」。
- cables.source：manual（人建的，系統一律不動）/ auto（依證據自動建立，證據改變或消失會跟著更新、撤掉）
- cables.evidence：自動建立的依據（LLDP / CDP / 單一 MAC …），畫面上講得出「為什麼這條線在這裡」
- cables.verified_at：證據最後一次成立的時間
- cable_suggestions：證據不夠明確（或跟人工的線衝突）時只列成建議；人刪掉的自動線也記在這裡（dismissed），
  下一輪不會再建回來
既有纜線一律 manual。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0250_cable_inference"
down_revision: str | None = "0249_subnet_scan_intensity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cables", sa.Column("source", sa.String(16), nullable=False, server_default="manual"))
    op.create_check_constraint("ck_cables_source_valid", "cables", "source IN ('manual','auto')")
    op.add_column("cables", sa.Column("evidence", postgresql.JSONB(), nullable=True))
    op.add_column("cables", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    # 「這個埠接了哪條線」每輪推斷都要問；以前沒有索引
    op.create_index("ix_cable_terminations_object", "cable_terminations", ["object_type", "object_id"])

    op.create_table(
        "cable_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        # 兩端照 id 排序存（a < b），同一對埠只有一筆
        sa.Column("a_port_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("device_ports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("b_port_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("device_ports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(32), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("dismissed_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("a_port_id", "b_port_id", name="cable_suggestion_pair_uq"),
        sa.CheckConstraint("status IN ('open','dismissed')", name="ck_cable_suggestions_status"),
        sa.CheckConstraint("a_port_id < b_port_id", name="ck_cable_suggestions_order"),
    )
    op.create_index("ix_cable_suggestions_b_port", "cable_suggestions", ["b_port_id"])


def downgrade() -> None:
    op.drop_index("ix_cable_suggestions_b_port", table_name="cable_suggestions")
    op.drop_table("cable_suggestions")
    op.drop_index("ix_cable_terminations_object", table_name="cable_terminations")
    op.drop_column("cables", "verified_at")
    op.drop_column("cables", "evidence")
    op.drop_constraint("ck_cables_source_valid", "cables", type_="check")
    op.drop_column("cables", "source")
