"""ProxmoxInstance.verify_tls（PVE 多為自簽，預設不驗證）+ extra_api_urls（多節點容錯）。

Revision ID: 0037_proxmox_verify_multiurl
Revises: 0036_vm_node_kind
Create Date: 2026-05-30 11:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_proxmox_verify_multiurl"
down_revision: str | None = "0036_vm_node_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "proxmox_instances",
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "proxmox_instances",
        sa.Column("extra_api_urls", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("proxmox_instances", "extra_api_urls")
    op.drop_column("proxmox_instances", "verify_tls")
