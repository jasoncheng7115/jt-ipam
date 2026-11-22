"""device_power_profiles.primary_port_id 補索引

Revision ID: 0248_power_profile_fk_index
Revises: 0247_power_planning
Create Date: 2026-11-21

外鍵欄位都要有索引（tests/test_fk_indexes.py）：刪電源埠時 ON DELETE SET NULL 要找引用它的規劃資料，
沒有索引就整表掃描。0247 已經在站台跑過，所以另外補一支，不改 0247。
"""
from __future__ import annotations

from alembic import op

revision: str = "0248_power_profile_fk_index"
down_revision: str | None = "0247_power_planning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_device_power_profiles_primary_port_id", "device_power_profiles", ["primary_port_id"])


def downgrade() -> None:
    op.drop_index("ix_device_power_profiles_primary_port_id", table_name="device_power_profiles")
