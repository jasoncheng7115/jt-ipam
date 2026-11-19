"""Kubernetes 第三階段：Egress IP 所在的節點

Revision ID: 0246_k8s_phase3
Revises: 0245_k8s_phase2
Create Date: 2026-11-19

Egress IP（OVN-Kubernetes、Cilium、Antrea）、API 伺服器位址、Multus static 位址都走 k8s_service_ips（kind 字串，
沒有 CHECK）；Antrea ExternalIPPool、Multus host-local 網段走 k8s_ip_pools。只多一欄：Egress IP 目前在哪個節點。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0246_k8s_phase3"
down_revision: str | None = "0245_k8s_phase2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("k8s_service_ips", sa.Column("node", sa.String(253)))


def downgrade() -> None:
    op.drop_column("k8s_service_ips", "node")
