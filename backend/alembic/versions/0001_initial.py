"""Initial schema — Phase 1 core tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-09 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Extensions（A02 / 全文搜尋 / UUID）
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # ── locations ──
    op.create_table(
        "locations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("address", sa.Text()),
        sa.Column("latitude", sa.Numeric(10, 7)),
        sa.Column("longitude", sa.Numeric(10, 7)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    # ── racks ──
    op.create_table(
        "racks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("location_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("locations.id", ondelete="SET NULL"), index=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("u_height", sa.Integer(), nullable=False, server_default=sa.text("42")),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    # ── sections ──
    op.create_table(
        "sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False, index=True),
        sa.Column("description", sa.Text()),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sections.id", ondelete="SET NULL"), index=True),
        sa.Column("strict_mode", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    # ── vlan_domains ──
    op.create_table(
        "vlan_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    # ── vlans ──
    op.create_table(
        "vlans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("domain_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("vlan_domains.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint("number BETWEEN 1 AND 4094", name="ck_vlans_vlan_number_range"),
        sa.UniqueConstraint("domain_id", "number", name="vlan_domain_number_uq"),
    )

    # ── vrfs ──
    op.create_table(
        "vrfs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("rd", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("allow_overlap", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    # ── subnets ──
    op.create_table(
        "subnets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("section_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sections.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("master_subnet_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("subnets.id", ondelete="SET NULL"), index=True),
        sa.Column("cidr", postgresql.CIDR(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("vlan_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("vlans.id", ondelete="SET NULL")),
        sa.Column("vrf_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("vrfs.id", ondelete="SET NULL")),
        sa.Column("is_pool", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_full", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("scan_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("scan_method", postgresql.ARRAY(sa.String()),
                  nullable=False, server_default=sa.text("ARRAY['icmp']::varchar[]")),
        sa.Column("threshold_pct", sa.Integer()),
        sa.Column("auto_dns", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("custom_fields", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index(
        "ix_subnets_cidr_gist", "subnets", ["cidr"],
        postgresql_using="gist",
    )

    # ── devices ──
    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False, index=True),
        sa.Column("primary_ip_id", postgresql.UUID(as_uuid=True)),
        sa.Column("type", sa.String(16), nullable=False, server_default=sa.text("'other'")),
        sa.Column("vendor", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("serial", sa.Text()),
        sa.Column("location_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("locations.id", ondelete="SET NULL")),
        sa.Column("rack_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("racks.id", ondelete="SET NULL")),
        sa.Column("u_position", sa.Integer()),
        sa.Column("u_size", sa.Integer()),
        sa.Column("description", sa.Text()),
        sa.Column("custom_fields", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "type IN ('server','switch','router','firewall','ap','storage','ipmi','other')",
            name="ck_devices_device_type_valid",
        ),
    )

    # ── ip_addresses ──
    op.create_table(
        "ip_addresses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("subnet_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("subnets.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("ip", postgresql.INET(), nullable=False),
        sa.Column("hostname", sa.Text(), index=True),
        sa.Column("description", sa.Text()),
        sa.Column("state", sa.String(16), nullable=False, server_default=sa.text("'active'")),
        sa.Column("mac", postgresql.MACADDR(), index=True),
        sa.Column("owner", sa.Text()),
        sa.Column("device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("switch_port", sa.Text()),
        sa.Column("exclude_from_ping", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ptr_ignore", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("note", sa.Text()),
        sa.Column("custom_fields", postgresql.JSONB()),
        sa.Column("discovery_source", sa.String(16),
                  nullable=False, server_default=sa.text("'manual'")),
        sa.Column("last_seen_scanner", sa.DateTime(timezone=True)),
        sa.Column("last_seen_librenms", sa.DateTime(timezone=True)),
        sa.Column("last_seen_dns", sa.DateTime(timezone=True)),
        sa.Column("effective_status", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("subnet_id", "ip", name="ip_subnet_ip_uq"),
        sa.CheckConstraint(
            "state IN ('active','reserved','offline','dhcp','used')",
            name="ck_ip_addresses_ip_state_valid",
        ),
        sa.CheckConstraint(
            "discovery_source IN ('manual','scanner','librenms','dns','proxmox','opnsense')",
            name="ck_ip_addresses_ip_discovery_source_valid",
        ),
    )
    op.create_index(
        "ix_ip_addresses_ip_gist", "ip_addresses", ["ip"], postgresql_using="gist",
    )

    # ── devices.primary_ip_id FK（after ip_addresses 建立）──
    op.create_foreign_key(
        "fk_devices_primary_ip_id_ip_addresses",
        "devices", "ip_addresses",
        ["primary_ip_id"], ["id"],
        ondelete="SET NULL",
    )

    # ── nat_translations ──
    op.create_table(
        "nat_translations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("src_ip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ip_addresses.id", ondelete="SET NULL")),
        sa.Column("dst_ip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ip_addresses.id", ondelete="SET NULL")),
        sa.Column("src_port", sa.Integer()),
        sa.Column("dst_port", sa.Integer()),
        sa.Column("protocol", sa.String(8), nullable=False, server_default=sa.text("'any'")),
        sa.Column("device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "type IN ('one_to_one','many_to_one','port_forward')",
            name="ck_nat_translations_nat_type_valid",
        ),
        sa.CheckConstraint(
            "protocol IN ('tcp','udp','any')",
            name="ck_nat_translations_nat_protocol_valid",
        ),
        sa.CheckConstraint(
            "(src_port IS NULL OR src_port BETWEEN 1 AND 65535) "
            "AND (dst_port IS NULL OR dst_port BETWEEN 1 AND 65535)",
            name="ck_nat_translations_nat_port_range",
        ),
    )

    # ── users ──
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("username", postgresql.CITEXT(), nullable=False, unique=True, index=True),
        sa.Column("email", postgresql.CITEXT(), nullable=False, unique=True, index=True),
        sa.Column("display_name", sa.Text()),
        sa.Column("password_hash", sa.Text()),
        sa.Column("auth_provider", sa.String(32), nullable=False, server_default=sa.text("'local'")),
        sa.Column("external_subject", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("totp_secret_enc", sa.LargeBinary()),
        sa.Column("totp_nonce", sa.LargeBinary()),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("last_login_ip", postgresql.INET()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "auth_provider IN ('local','ldap','radius','saml','oidc')",
            name="ck_users_auth_provider_valid",
        ),
        sa.CheckConstraint(
            "(password_hash IS NOT NULL) OR (auth_provider <> 'local')",
            name="ck_users_local_user_must_have_password",
        ),
    )

    # ── groups ──
    op.create_table(
        "groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    # ── user_group_members ──
    op.create_table(
        "user_group_members",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("group_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    )

    # ── api_tokens ──
    op.create_table(
        "api_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("token_prefix", sa.String(8), nullable=False, index=True),
        sa.Column("scopes", postgresql.ARRAY(sa.String()),
                  nullable=False, server_default=sa.text("ARRAY[]::varchar[]")),
        sa.Column("object_filters", postgresql.JSONB()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_ip", postgresql.INET()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    # ── user_preferences ──
    op.create_table(
        "user_preferences",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("locale", sa.String(8), nullable=False, server_default=sa.text("'zh-TW'")),
        sa.Column("theme", sa.String(8), nullable=False, server_default=sa.text("'auto'")),
        sa.Column("timezone", sa.String(64), nullable=False, server_default=sa.text("'Asia/Taipei'")),
        sa.Column("calendar", sa.String(16), nullable=False, server_default=sa.text("'gregorian'")),
        sa.Column("page_size", sa.Integer(), nullable=False, server_default=sa.text("50")),
        sa.Column("default_section_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sections.id", ondelete="SET NULL")),
        sa.Column("dashboard_layout", postgresql.JSONB()),
        sa.CheckConstraint("locale IN ('zh-TW','en-US')", name="ck_user_preferences_locale_valid"),
        sa.CheckConstraint("theme IN ('light','dark','auto')", name="ck_user_preferences_theme_valid"),
        sa.CheckConstraint("calendar IN ('gregorian','minguo')",
                           name="ck_user_preferences_calendar_valid"),
    )

    # ── permissions ──
    op.create_table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("object_type", sa.String(16), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_type", sa.String(8), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "object_type IN ('section','subnet')",
            name="ck_permissions_permission_object_type_valid",
        ),
        sa.CheckConstraint(
            "principal_type IN ('user','group')",
            name="ck_permissions_permission_principal_type_valid",
        ),
        sa.CheckConstraint(
            "level IN ('read','write','admin')",
            name="ck_permissions_permission_level_valid",
        ),
        sa.UniqueConstraint(
            "object_type", "object_id", "principal_type", "principal_id",
            name="permission_unique",
        ),
    )

    # ── encrypted_secrets（A02）──
    op.create_table(
        "encrypted_secrets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_id", sa.String(64), nullable=False, server_default=sa.text("'primary'")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint(
            "object_type", "object_id", "field", "key_id",
            name="encrypted_secret_unique",
        ),
    )

    # ── audit_logs（A08：SHA-256 異動鏈）──
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_ip", postgresql.INET()),
        sa.Column("actor_user_agent", sa.Text()),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("diff", postgresql.JSONB()),
        sa.Column("request_id", postgresql.UUID(as_uuid=True)),
        sa.Column("prev_hash", sa.LargeBinary(), nullable=False),
        sa.Column("this_hash", sa.LargeBinary(), nullable=False, unique=True),
    )
    op.create_index("ix_audit_object", "audit_logs", ["object_type", "object_id"])
    op.create_index("ix_audit_actor", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_ts", "audit_logs", ["ts"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("encrypted_secrets")
    op.drop_table("permissions")
    op.drop_table("user_preferences")
    op.drop_table("api_tokens")
    op.drop_table("user_group_members")
    op.drop_table("groups")
    op.drop_table("users")
    op.drop_table("nat_translations")
    op.drop_constraint("fk_devices_primary_ip_id_ip_addresses", "devices", type_="foreignkey")
    op.drop_index("ix_ip_addresses_ip_gist", table_name="ip_addresses")
    op.drop_table("ip_addresses")
    op.drop_table("devices")
    op.drop_index("ix_subnets_cidr_gist", table_name="subnets")
    op.drop_table("subnets")
    op.drop_table("vrfs")
    op.drop_table("vlans")
    op.drop_table("vlan_domains")
    op.drop_table("sections")
    op.drop_table("racks")
    op.drop_table("locations")
