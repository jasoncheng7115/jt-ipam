"""VirtualMachine.node（所在 PVE 節點）+ kind（vm/ct），給拓樸圖與橋接關係用。

Revision ID: 0036_vm_node_kind
Revises: 0035_switch_port_confident
Create Date: 2026-05-30 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_vm_node_kind"
down_revision: str | None = "0035_switch_port_confident"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("virtual_machines", sa.Column("node", sa.String(length=128), nullable=True))
    op.add_column("virtual_machines", sa.Column("kind", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("virtual_machines", "kind")
    op.drop_column("virtual_machines", "node")
