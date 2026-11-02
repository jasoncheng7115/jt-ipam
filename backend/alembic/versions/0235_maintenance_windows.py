"""維護排程：維護期間範圍內不告警（2026-11-01）

見 app/models/maintenance.py、app/services/maintenance.py。

Revision ID: 0235_maintenance_windows
Revises: 0234_ip_path_hops
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, INET, UUID

revision: str = "0235_maintenance_windows"
down_revision: str | None = "0234_ip_path_hops"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "maintenance_windows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("ticket", sa.String(100)),
        sa.Column("behavior", sa.String(8), nullable=False, server_default="mute"),
        sa.Column("schedule_type", sa.String(8), nullable=False, server_default="once"),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("weekdays", ARRAY(sa.Integer())),
        sa.Column("start_time", sa.Time()),
        sa.Column("end_time", sa.Time()),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_until", sa.Date()),
        sa.Column("skip_until", sa.DateTime(timezone=True)),
        sa.Column("extended_until", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(100)),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("active_since", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("behavior IN ('mute','mark')", name="ck_maintenance_windows_behavior"),
        sa.CheckConstraint("schedule_type IN ('once','weekly')", name="ck_maintenance_windows_schedule"),
        sa.CheckConstraint(
            "(schedule_type = 'once' AND starts_at IS NOT NULL AND ends_at IS NOT NULL AND ends_at > starts_at) OR "
            "(schedule_type = 'weekly' AND weekdays IS NOT NULL AND start_time IS NOT NULL AND end_time IS NOT NULL "
            "AND valid_until IS NOT NULL)", name="ck_maintenance_windows_shape"),
    )
    op.create_table(
        "maintenance_scopes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("window_id", UUID(as_uuid=True), sa.ForeignKey("maintenance_windows.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("object_id", UUID(as_uuid=True)),
        sa.Column("integration_kind", sa.String(32)),
        sa.Column("range_start", INET()),
        sa.Column("range_end", INET()),
        sa.Column("label", sa.String(255)),
        sa.CheckConstraint("scope_type IN ('ip','ip_range','subnet','section','device','rack','location','customer',"
                           "'integration','scan_agent')", name="ck_maintenance_scopes_type"),
    )
    op.create_index("ix_maintenance_scopes_window_id", "maintenance_scopes", ["window_id"])
    op.create_index("ix_maintenance_scopes_object_id", "maintenance_scopes", ["object_id"])
    op.create_table(
        "maintenance_suppressions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("window_id", UUID(as_uuid=True), sa.ForeignKey("maintenance_windows.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("occurrence", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("label", sa.Text()),
        sa.Column("first_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("window_id", "occurrence", "key", name="uq_maintenance_suppressions_key"),
    )
    op.create_index("ix_maintenance_suppressions_window_id", "maintenance_suppressions", ["window_id"])


def downgrade() -> None:
    op.drop_table("maintenance_suppressions")
    op.drop_table("maintenance_scopes")
    op.drop_table("maintenance_windows")
