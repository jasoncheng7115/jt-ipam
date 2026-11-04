"""憑證派送代理回報它怎麼確認連到的是真的 jt-ipam（2026-11-04）

舊的預設安裝指令關掉 TLS 驗證（curl -k / JT_IPAM_INSECURE），能攔截網路的人可以假冒 jt-ipam 拿走代理金鑰、
推一支以 root 執行的腳本。新版代理用釘選或憑證驗證，並在每次連線帶 X-Agent-Tls（pinned / ca / insecure）；
這裡記下來，清單才標得出哪些代理還沒驗證伺服器、要重新安裝。

Revision ID: 0237_cert_agent_tls_mode
Revises: 0236_maint_created_by_idx
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0237_cert_agent_tls_mode"
down_revision: str | None = "0236_maint_created_by_idx"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("cert_agents", sa.Column("tls_mode", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("cert_agents", "tls_mode")
