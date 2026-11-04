"""掃描代理、RustDesk 代理回報它怎麼確認連到的是真的 jt-ipam（2026-11-04，同 0237 派送代理）

這兩支代理以前的安裝指令一律 JT_IPAM_INSECURE=1：能攔截網路的人可以假冒 jt-ipam 拿走代理金鑰，或推一支
以 root 執行的「新版代理」。新版代理用釘選或憑證驗證、自我更新驗章，並在每次連線帶 X-Agent-Tls
（pinned / ca / insecure）；記下來，清單才標得出哪些代理還沒驗證伺服器、要重新安裝。

Revision ID: 0238_agent_tls_mode
Revises: 0237_cert_agent_tls_mode
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0238_agent_tls_mode"
down_revision: str | None = "0237_cert_agent_tls_mode"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("scan_agents", sa.Column("tls_mode", sa.String(16), nullable=True))
    op.add_column("rustdesk_servers", sa.Column("agent_tls_mode", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("rustdesk_servers", "agent_tls_mode")
    op.drop_column("scan_agents", "tls_mode")
