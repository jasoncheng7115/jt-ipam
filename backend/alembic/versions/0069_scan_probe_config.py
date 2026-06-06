"""scan probe config: per-agent enabled probes + intervals, per-IP excluded probes + OS

Revision ID: 0069_scan_probe_config
Revises: 0068_scan_agent_source_ip
Create Date: 2026-06-06

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0069_scan_probe_config"
down_revision: str | None = "0068_scan_agent_source_ip"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── scan_agents：能力天花板 + 間隔 + 回報可用性 ──
    op.add_column(
        "scan_agents",
        sa.Column(
            "enabled_probes",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("ARRAY['icmp']::varchar[]"),
        ),
    )
    op.add_column("scan_agents", sa.Column("probe_intervals", postgresql.JSONB(), nullable=True))
    op.add_column(
        "scan_agents",
        sa.Column("available_probes", postgresql.ARRAY(sa.String()), nullable=True),
    )

    # ── ip_addresses：逐 IP 略過 + OS 結果 + 各 probe 上次執行 ──
    op.add_column(
        "ip_addresses",
        sa.Column(
            "excluded_probes",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
    )
    op.add_column("ip_addresses", sa.Column("os_guess", sa.String(160), nullable=True))
    op.add_column("ip_addresses", sa.Column("os_family", sa.String(24), nullable=True))
    op.add_column("ip_addresses", sa.Column("probe_last_run", postgresql.JSONB(), nullable=True))

    # 回填：舊 exclude_from_ping=true 的 IP → excluded_probes 含 'icmp'
    op.execute(
        "UPDATE ip_addresses SET excluded_probes = ARRAY['icmp']::varchar[] "
        "WHERE exclude_from_ping = true"
    )


def downgrade() -> None:
    op.drop_column("ip_addresses", "probe_last_run")
    op.drop_column("ip_addresses", "os_family")
    op.drop_column("ip_addresses", "os_guess")
    op.drop_column("ip_addresses", "excluded_probes")
    op.drop_column("scan_agents", "available_probes")
    op.drop_column("scan_agents", "probe_intervals")
    op.drop_column("scan_agents", "enabled_probes")
