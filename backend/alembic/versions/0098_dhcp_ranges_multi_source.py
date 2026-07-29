"""dhcp_pool_ranges: 改成可容納多個 DHCP 來源（OPNsense / pfSense / Windows DHCP）

原本 firewall_id 的外鍵寫死 opnsense_firewalls，別的來源進不來。
改為 (source_type, source_id, source_name)：各整合各自寫入、各自只清除自己的列。
不設外鍵（跨三張來源表），改由各整合的 DELETE 端點負責清理。
subnet_cidr 放寬為可空（pfSense 的 DHCP 以介面為單位，不一定給得出 CIDR）。

Revision ID: 0098_dhcp_ranges_multi_source
Revises: 0097_device_types_pp_pdu_ups
Create Date: 2026-07-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0098_dhcp_ranges_multi_src"
down_revision: str | None = "0097_device_types_pp_pdu_ups"
branch_labels = None
depends_on = None

_FK_NAMES = (
    "dhcp_pool_ranges_firewall_id_fkey",
    "fk_dhcp_pool_ranges_firewall_id_opnsense_firewalls",
)


def upgrade() -> None:
    # 1) 新欄位（先可空，回填後再設 NOT NULL）
    op.add_column("dhcp_pool_ranges", sa.Column("source_type", sa.String(24), nullable=True))
    op.add_column("dhcp_pool_ranges", sa.Column("source_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("dhcp_pool_ranges", sa.Column("source_name", sa.String(128), nullable=True))

    # 2) 回填既有資料（既有列全部來自 OPNsense）
    op.execute("""
        UPDATE dhcp_pool_ranges r
           SET source_type = 'opnsense',
               source_id   = r.firewall_id,
               source_name = f.name
          FROM opnsense_firewalls f
         WHERE f.id = r.firewall_id
    """)
    # 萬一有孤兒列（防火牆已刪但 cascade 沒清到）→ 也補上 source_type/source_id
    op.execute("""
        UPDATE dhcp_pool_ranges
           SET source_type = 'opnsense', source_id = firewall_id
         WHERE source_type IS NULL AND firewall_id IS NOT NULL
    """)
    op.execute("DELETE FROM dhcp_pool_ranges WHERE source_id IS NULL")

    # 3) 收緊約束 + 索引
    op.alter_column("dhcp_pool_ranges", "source_type", nullable=False)
    op.alter_column("dhcp_pool_ranges", "source_id", nullable=False)
    op.create_index("ix_dhcp_pool_ranges_source", "dhcp_pool_ranges", ["source_type", "source_id"])

    # 4) 丟掉舊的 OPNsense 專用外鍵與欄位
    for n in _FK_NAMES:
        op.execute(f'ALTER TABLE dhcp_pool_ranges DROP CONSTRAINT IF EXISTS "{n}"')
    op.drop_column("dhcp_pool_ranges", "firewall_id")

    # 5) pfSense 以介面為單位，不一定有 CIDR
    op.alter_column("dhcp_pool_ranges", "subnet_cidr", existing_type=sa.String(64), nullable=True)

    # 6) pfSense 自己的「同步發放範圍」開關（預設關，沿用既有同步開關的保守作法）
    op.add_column(
        "pfsense_firewalls",
        sa.Column("sync_dhcp_ranges", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("pfsense_firewalls", "sync_dhcp_ranges")
    # 還原成 OPNsense 專用：非 opnsense 來源的列無處可放 → 直接刪除
    op.execute("DELETE FROM dhcp_pool_ranges WHERE source_type <> 'opnsense'")
    op.execute("UPDATE dhcp_pool_ranges SET subnet_cidr = '' WHERE subnet_cidr IS NULL")
    op.alter_column("dhcp_pool_ranges", "subnet_cidr", existing_type=sa.String(64), nullable=False)

    op.add_column(
        "dhcp_pool_ranges",
        sa.Column("firewall_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute("UPDATE dhcp_pool_ranges SET firewall_id = source_id")
    # 來源防火牆已不存在的列無法建立外鍵 → 先清掉
    op.execute("""
        DELETE FROM dhcp_pool_ranges
         WHERE firewall_id IS NULL
            OR firewall_id NOT IN (SELECT id FROM opnsense_firewalls)
    """)
    op.alter_column("dhcp_pool_ranges", "firewall_id", nullable=False)
    op.create_foreign_key(
        "fk_dhcp_pool_ranges_firewall_id_opnsense_firewalls",
        "dhcp_pool_ranges", "opnsense_firewalls",
        ["firewall_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_dhcp_pool_ranges_firewall_id", "dhcp_pool_ranges", ["firewall_id"])

    op.drop_index("ix_dhcp_pool_ranges_source", table_name="dhcp_pool_ranges")
    op.drop_column("dhcp_pool_ranges", "source_name")
    op.drop_column("dhcp_pool_ranges", "source_id")
    op.drop_column("dhcp_pool_ranges", "source_type")
