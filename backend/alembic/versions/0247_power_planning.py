"""規劃電力：整機規劃負載、電源分攤比例、迴路規劃容量（交接包 physical-v1.2，issue #54 重做）

Revision ID: 0247_power_planning
Revises: 0246_k8s_phase3
Create Date: 2026-11-21

以前把電源埠的 max_watts 當成負載加總：兩顆 800 W 的 PSU、整機 500 W 被算成 800（每一路也各 800），
迴路容量直接用電壓 × 電流。這一版分開三件事：
- device_power_profiles：整台只填一次的規劃負載（null＝未知，0＝明確填零）、數值依據、供電模式、單路承載能力、是否納入
- device_power_ports.normal_share_pct：平常各顆電源分攤幾 %；rating_confirmed_at：額定容量確認過的時間
- power_feeds.planning_capacity_w：人工確認的規劃容量（null＝未設定），warning_pct 警戒百分比

只新增欄位與資料表，全部允許空值；既有的 max_watts、電壓、電流原樣保留，不自動轉成規劃負載或容量
（不猜舊值的用途：畫面標「舊值，請確認」）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0247_power_planning"
down_revision: str | None = "0246_k8s_phase3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_power_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("planning_input_w", sa.Numeric(10, 2)),
        sa.Column("value_basis", sa.String(24)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.Column("supply_mode", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("failover_capability", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("primary_port_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("device_power_ports.id", ondelete="SET NULL")),
        sa.Column("include_in_planning", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("exclude_reason", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("planning_input_w IS NULL OR planning_input_w >= 0", name="ck_power_profile_load_nonneg"),
        sa.CheckConstraint(
            "value_basis IS NULL OR value_basis IN ('manual_estimate','vendor_reference','measurement_reference')",
            name="ck_power_profile_basis"),
        sa.CheckConstraint("supply_mode IN ('unknown','single','shared','active_standby','custom')",
                           name="ck_power_profile_mode"),
        sa.CheckConstraint("failover_capability IN ('unknown','confirmed_full_load','insufficient')",
                           name="ck_power_profile_failover"),
    )
    op.add_column("device_power_ports", sa.Column("normal_share_pct", sa.Numeric(5, 2)))
    op.add_column("device_power_ports", sa.Column("rating_confirmed_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_power_port_share_range", "device_power_ports",
        "normal_share_pct IS NULL OR (normal_share_pct >= 0 AND normal_share_pct <= 100)")
    op.add_column("power_feeds", sa.Column("planning_capacity_w", sa.Numeric(10, 2)))
    op.add_column("power_feeds", sa.Column("warning_pct", sa.Integer(), nullable=False, server_default="80"))
    op.add_column("power_feeds", sa.Column("capacity_basis", sa.String(128)))
    op.create_check_constraint(
        "ck_power_feed_capacity_nonneg", "power_feeds", "planning_capacity_w IS NULL OR planning_capacity_w >= 0")
    op.create_check_constraint("ck_power_feed_warning_pct", "power_feeds", "warning_pct BETWEEN 1 AND 100")


def downgrade() -> None:
    op.drop_constraint("ck_power_feed_warning_pct", "power_feeds", type_="check")
    op.drop_constraint("ck_power_feed_capacity_nonneg", "power_feeds", type_="check")
    op.drop_column("power_feeds", "capacity_basis")
    op.drop_column("power_feeds", "warning_pct")
    op.drop_column("power_feeds", "planning_capacity_w")
    op.drop_constraint("ck_power_port_share_range", "device_power_ports", type_="check")
    op.drop_column("device_power_ports", "rating_confirmed_at")
    op.drop_column("device_power_ports", "normal_share_pct")
    op.drop_table("device_power_profiles")
