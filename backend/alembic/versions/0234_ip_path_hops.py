"""對外路徑上手動插入的中間設備（2026-10-29）

客戶許願清單：WAN IP → LAN 裝置之間還有防火牆或交換器時，允許使用者自己加在中間。
見 app/models/ip_path.py。

Revision ID: 0234_ip_path_hops
Revises: 0233_domains
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0234_ip_path_hops"
down_revision: str | None = "0233_domains"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ip_path_hops",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("address_id", UUID(as_uuid=True), sa.ForeignKey("ip_addresses.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(16), nullable=False, server_default="other"),
        sa.Column("device_id", UUID(as_uuid=True), sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("label", sa.String(128)),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('firewall','waf','load_balancer','proxy','router','switch','other')",
                           name="ck_ip_path_hops_kind"),
        sa.CheckConstraint("device_id IS NOT NULL OR label IS NOT NULL", name="ck_ip_path_hops_what"),
    )
    op.create_index("ix_ip_path_hops_address_id", "ip_path_hops", ["address_id"])
    op.create_index("ix_ip_path_hops_device_id", "ip_path_hops", ["device_id"])


def downgrade() -> None:
    op.drop_table("ip_path_hops")
