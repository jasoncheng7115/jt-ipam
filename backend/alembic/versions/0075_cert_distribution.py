"""certificate distribution: certificates / cert_versions / cert_agents

Revision ID: 0075_cert_distribution
Revises: 0074_dhcp_lease_phpipam
Create Date: 2026-06-13

集中保管商業憑證 + 由 agent 拉取派送到各站台（nginx/apache/pve/pmg/zimbra…）。
私鑰以 AES-GCM 加密儲存（key_enc/key_nonce）；agent 以 enroll_key_hash 認證、scope 限定可取憑證。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0075_cert_distribution"
down_revision: str | None = "0074_dhcp_lease_phpipam"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "certificates",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("domains", postgresql.ARRAY(sa.Text())),  # 目前版本的 SAN
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "cert_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("certificate_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("certificates.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("fingerprint_sha256", sa.String(64), nullable=False),
        sa.Column("serial", sa.String(128)),
        sa.Column("subject", sa.Text()),
        sa.Column("issuer", sa.Text()),
        sa.Column("not_before", sa.DateTime(timezone=True)),
        sa.Column("not_after", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("domains", postgresql.ARRAY(sa.Text())),
        sa.Column("cert_pem", sa.Text(), nullable=False),
        sa.Column("chain_pem", sa.Text()),
        # 私鑰：AES-GCM 加密（aad 綁 cert_version:<id>:key）
        sa.Column("key_enc", sa.LargeBinary(), nullable=False),
        sa.Column("key_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("certificate_id", "fingerprint_sha256", name="cert_version_unique"),
    )

    op.create_table(
        "cert_agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        # enrollment key 的 sha256（明文只在建立/輪替時回傳一次）
        sa.Column("enroll_key_hash", sa.String(64), unique=True, index=True),
        # deny-by-default：此 agent 可取的 certificate id 清單（JSONB 陣列）；空＝不可取任何
        sa.Column("scope_cert_ids", postgresql.JSONB()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_source_ip", sa.String(64)),
        sa.Column("agent_version", sa.String(32)),
        # agent 回報的各部署狀態（list of {cert,profile,fingerprint,not_after,applied_at,status,message,dry_run}）
        sa.Column("reported", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cert_agents")
    op.drop_table("cert_versions")
    op.drop_table("certificates")
