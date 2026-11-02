"""網域：註冊商、到期日（RDAP）、NS 與 DNS 託管（2026-10-29）

客戶許願清單：租用了哪些網域、何時到期、註冊商是哪家、NS / DNS 放在哪家、綁著哪些服務。
見 app/models/domain.py。

Revision ID: 0233_domains
Revises: 0232_circuit_detail
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0233_domains"
down_revision: str | None = "0232_circuit_detail"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "domains",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(253), nullable=False, unique=True),
        sa.Column("registrar", sa.String(128)),
        sa.Column("registrar_url", sa.Text()),
        sa.Column("registrar_account", sa.String(128)),
        sa.Column("dns_provider", sa.String(128)),
        sa.Column("services", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("expires_at", sa.Date()),
        sa.Column("auto_renew", sa.Boolean()),
        sa.Column("rdap_checked_at", sa.DateTime(timezone=True)),
        sa.Column("rdap_error", sa.String(64)),
        sa.Column("rdap_registrar", sa.String(255)),
        sa.Column("rdap_registered_at", sa.DateTime(timezone=True)),
        sa.Column("rdap_expires_at", sa.DateTime(timezone=True)),
        sa.Column("rdap_nameservers", JSONB()),
        sa.Column("rdap_status", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("domains")
