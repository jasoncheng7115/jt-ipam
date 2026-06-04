"""IP hostname 多來源觀測 + 單 IP pin（feature A）。

Revision ID: 0028_ip_hostname_obs
Revises: 0027_ip_change_log
Create Date: 2026-05-29 09:40:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_ip_hostname_obs"
down_revision: str | None = "0027_ip_change_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ip_hostname_observations",
        sa.Column(
            "id", sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(), primary_key=True,
        ),
        sa.Column(
            "ip_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ip_addresses.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=False),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("ip_id", "source", name="uq_ip_hostname_obs_ip_source"),
    )
    op.create_index(
        "ix_ip_hostname_observations_ip_id", "ip_hostname_observations", ["ip_id"],
    )
    op.add_column(
        "ip_addresses",
        sa.Column("hostname_source_pin", sa.String(length=16), nullable=True),
    )

    # 既有資料回填：把目前 ip_addresses.hostname 當成各自 discovery_source 的觀測，
    # 讓優先序立刻有東西可解析（hostname 非空者）。
    op.execute(
        """
        INSERT INTO ip_hostname_observations (ip_id, source, hostname, observed_at)
        SELECT id,
               CASE WHEN discovery_source IN
                    ('manual','scanner','librenms','dns','proxmox','opnsense')
                    THEN discovery_source ELSE 'manual' END,
               hostname, now()
          FROM ip_addresses
         WHERE hostname IS NOT NULL AND hostname <> ''
        ON CONFLICT (ip_id, source) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_column("ip_addresses", "hostname_source_pin")
    op.drop_index(
        "ix_ip_hostname_observations_ip_id", table_name="ip_hostname_observations",
    )
    op.drop_table("ip_hostname_observations")
