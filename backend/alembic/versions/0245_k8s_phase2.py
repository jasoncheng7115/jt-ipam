"""Kubernetes 第二階段：主機名稱（Ingress／Gateway／Route）、Calico 與 Whereabouts 集區、可路由的 Pod 網段

Revision ID: 0245_k8s_phase2
Revises: 0244_k8s
Create Date: 2026-11-19

- k8s_hostnames：網域名稱 → 位址（Ingress、Gateway listener、HTTPRoute／GRPCRoute、OpenShift Route）
- k8s_ip_pools.use：loadbalancer | pod（Whereabouts 發給 Pod 次要網卡的實體網段）
- k8s_cidrs.routable／encapsulation：Calico 集區看得出能不能直接路由
- k8s_clusters.pod_network_routable：整個叢集的 Pod 網段可直接路由（手動標記）
- k8s_clusters.missing_permissions：唯讀帳號還是第一階段的版本時，列出讀不到的資源
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

revision: str = "0245_k8s_phase2"
down_revision: str | None = "0244_k8s"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("k8s_clusters", sa.Column("pod_network_routable", sa.Boolean(), nullable=False,
                                            server_default=sa.text("false")))
    op.add_column("k8s_clusters", sa.Column("missing_permissions", JSONB()))
    op.add_column("k8s_ip_pools", sa.Column("use", sa.String(16), nullable=False, server_default="loadbalancer"))
    op.add_column("k8s_cidrs", sa.Column("routable", sa.Boolean()))
    op.add_column("k8s_cidrs", sa.Column("encapsulation", sa.String(16)))
    op.create_table(
        "k8s_hostnames",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("cluster_id", UUID(as_uuid=True), sa.ForeignKey("k8s_clusters.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),        # ingress | gateway | httproute | grpcroute | route
        sa.Column("namespace", sa.String(253), nullable=False),
        sa.Column("name", sa.String(253), nullable=False),
        sa.Column("host", sa.String(253), nullable=False, index=True),
        sa.Column("ip", INET(), index=True),                     # 還沒拿到位址時是 NULL
        sa.Column("tls", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("jt_ipam_address_id", UUID(as_uuid=True), sa.ForeignKey("ip_addresses.id", ondelete="SET NULL"),
                  index=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("k8s_hostnames")
    op.drop_column("k8s_cidrs", "encapsulation")
    op.drop_column("k8s_cidrs", "routable")
    op.drop_column("k8s_ip_pools", "use")
    op.drop_column("k8s_clusters", "missing_permissions")
    op.drop_column("k8s_clusters", "pod_network_routable")
