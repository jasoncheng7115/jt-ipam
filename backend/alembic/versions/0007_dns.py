"""DNS integration tables.

Revision ID: 0007_dns
Revises: 0006_scan_agents
Create Date: 2026-05-09 03:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_dns"
down_revision: str | None = "0006_scan_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dns_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("api_url", sa.Text()),
        sa.Column("server_address", sa.Text()),
        sa.Column("extra_config", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=False, server_default=sa.text("300")),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "type IN ('powerdns','bind9','unbound_opnsense','windows_dns')",
            name="ck_dns_servers_type_valid",
        ),
    )

    op.create_table(
        "dns_zones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("server_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("dns_servers.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("managed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("associated_subnet_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                  nullable=False, server_default=sa.text("ARRAY[]::uuid[]")),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint("type IN ('forward','reverse')", name="ck_dns_zones_type_valid"),
        sa.UniqueConstraint("server_id", "name", name="dns_zone_server_name_uq"),
    )

    op.create_table(
        "dns_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("zone_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("dns_zones.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(8), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("ttl", sa.Integer(), nullable=False, server_default=sa.text("3600")),
        sa.Column("source", sa.String(16), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("consistency_state", sa.String(16), nullable=False,
                  server_default=sa.text("'consistent'")),
        sa.Column("ipam_address_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ip_addresses.id", ondelete="SET NULL"),
                  index=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "type IN ('A','AAAA','PTR','CNAME','MX','TXT','SRV','NS','SOA')",
            name="ck_dns_records_type_valid",
        ),
        sa.CheckConstraint(
            "source IN ('manual','from_ipam','from_dns_pulled')",
            name="ck_dns_records_source_valid",
        ),
        sa.CheckConstraint(
            "consistency_state IN ('consistent','dns_only','ipam_only','mismatch')",
            name="ck_dns_records_consistency_valid",
        ),
        sa.UniqueConstraint("zone_id", "name", "type", "value", name="dns_record_unique"),
    )


def downgrade() -> None:
    op.drop_table("dns_records")
    op.drop_table("dns_zones")
    op.drop_table("dns_servers")
