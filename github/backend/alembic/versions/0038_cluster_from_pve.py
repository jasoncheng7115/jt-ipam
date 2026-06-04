"""叢集名稱改由 PVE 帶：proxmox_instances.cluster_id 可為空（同步時自動指派）
+ virt_clusters.is_standalone（標示獨立節點）。

Revision ID: 0038_cluster_from_pve
Revises: 0037_proxmox_verify_multiurl
Create Date: 2026-05-30 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_cluster_from_pve"
down_revision: str | None = "0037_proxmox_verify_multiurl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("proxmox_instances", "cluster_id", existing_type=sa.dialects.postgresql.UUID(),
                    nullable=True)
    op.add_column(
        "virt_clusters",
        sa.Column("is_standalone", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("virt_clusters", "is_standalone")
    op.alter_column("proxmox_instances", "cluster_id", existing_type=sa.dialects.postgresql.UUID(),
                    nullable=False)
