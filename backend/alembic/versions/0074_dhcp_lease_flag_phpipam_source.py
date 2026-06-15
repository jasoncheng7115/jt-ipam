"""ip_addresses: in_dhcp_lease flag (auto from DHCP leases) + allow 'phpipam' discovery_source

Revision ID: 0074_dhcp_lease_flag_phpipam_source
Revises: 0073_ip_request_stage_approvals
Create Date: 2026-06-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0074_dhcp_lease_phpipam"
down_revision: str | None = "0073_ip_request_stage_approvals"
branch_labels = None
depends_on = None

_OLD = "discovery_source IN ('manual','scanner','librenms','dns','proxmox','opnsense')"
_NEW = "discovery_source IN ('manual','scanner','librenms','dns','proxmox','opnsense','phpipam')"
# 此約束在歷史 migration 裡被命名慣例前綴過（甚至雙重前綴），用 IF EXISTS 把所有可能名稱都掃掉再重建
_NAMES = (
    "ip_discovery_source_valid",
    "ck_ip_addresses_ip_discovery_source_valid",
    "ck_ip_addresses_ck_ip_addresses_ip_discovery_source_valid",
)
_CANON = "ck_ip_addresses_ip_discovery_source_valid"


def upgrade() -> None:
    # 1) 自動判定的「有 DHCP 租約」旗標（由 OPNsense DHCP lease 同步維護，與手動 state 分開）
    op.add_column(
        "ip_addresses",
        sa.Column("in_dhcp_lease", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # 2) 放寬 discovery_source 約束，讓 phpIPAM 匯入能標成 'phpipam'（而非誤標 manual）
    for n in _NAMES:
        op.execute(f'ALTER TABLE ip_addresses DROP CONSTRAINT IF EXISTS "{n}"')
    op.execute(f'ALTER TABLE ip_addresses ADD CONSTRAINT "{_CANON}" CHECK ({_NEW})')


def downgrade() -> None:
    op.execute("UPDATE ip_addresses SET discovery_source='manual' WHERE discovery_source='phpipam'")
    for n in _NAMES:
        op.execute(f'ALTER TABLE ip_addresses DROP CONSTRAINT IF EXISTS "{n}"')
    op.execute(f'ALTER TABLE ip_addresses ADD CONSTRAINT "{_CANON}" CHECK ({_OLD})')
    op.drop_column("ip_addresses", "in_dhcp_lease")
