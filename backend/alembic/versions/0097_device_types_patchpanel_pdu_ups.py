"""devices.type: allow patch_panel / pdu / ups (issue #21) + librenms_devices.type (native LibreNMS type)

Revision ID: 0097_device_types_pp_pdu_ups
Revises: 0096_device_port_name_len
Create Date: 2026-07-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0097_device_types_pp_pdu_ups"
down_revision: str | None = "0096_device_port_name_len"
branch_labels = None
depends_on = None

_OLD = "type IN ('server','switch','router','firewall','ap','storage','ipmi','other')"
_NEW = ("type IN ('server','switch','router','firewall','ap','storage','ipmi',"
        "'patch_panel','pdu','ups','other')")
# 約束在歷史裡可能被命名慣例前綴（甚至雙重前綴），用 IF EXISTS 全掃再重建
_NAMES = (
    "device_type_valid",
    "ck_devices_device_type_valid",
    "ck_devices_ck_devices_device_type_valid",
)
_CANON = "ck_devices_device_type_valid"


def upgrade() -> None:
    # 1) 放寬 device.type 約束，加入 patch_panel / pdu / ups
    for n in _NAMES:
        op.execute(f'ALTER TABLE devices DROP CONSTRAINT IF EXISTS "{n}"')
    op.execute(f'ALTER TABLE devices ADD CONSTRAINT "{_CANON}" CHECK ({_NEW})')
    # 2) 保留 LibreNMS 原生 device type（network/server/firewall/power/wireless/…），
    #    讓 _infer_device_type 能優先依它對應（比純關鍵字推測可靠）
    op.add_column("librenms_devices", sa.Column("type", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("librenms_devices", "type")
    # 把新類型併回 other，才能還原舊約束
    op.execute("UPDATE devices SET type='other' WHERE type IN ('patch_panel','pdu','ups')")
    for n in _NAMES:
        op.execute(f'ALTER TABLE devices DROP CONSTRAINT IF EXISTS "{n}"')
    op.execute(f'ALTER TABLE devices ADD CONSTRAINT "{_CANON}" CHECK ({_OLD})')
