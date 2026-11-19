"""Kubernetes 整合：叢集連線、節點、Service 佔用的位址、LB 位址集區、叢集網段、`ip_addresses.last_seen_k8s`

Revision ID: 0244_k8s
Revises: 0243_cert_deploy_notices
Create Date: 2026-11-18

K8s 對 IPAM 有意義的是會佔用或撞到實體網段的位址：節點 IP、LoadBalancer／externalIPs、MetalLB 與
Cilium 的 LB 位址集區、Pod／Service 網段。Pod IP 是叢集內部、隨時在變的，不收（第一階段）。全程唯讀。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CIDR, INET, JSONB, UUID

revision: str = "0244_k8s"
down_revision: str | None = "0243_cert_deploy_notices"
branch_labels = None
depends_on = None

_ID = {"primary_key": True, "server_default": sa.text("gen_random_uuid()")}


def _cluster_fk() -> sa.Column:
    return sa.Column("cluster_id", UUID(as_uuid=True), sa.ForeignKey("k8s_clusters.id", ondelete="CASCADE"),
                     nullable=False, index=True)


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "k8s_clusters",
        sa.Column("id", UUID(as_uuid=True), **_ID),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("api_url", sa.Text(), nullable=False),
        sa.Column("ca_cert", sa.Text()),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("auth_method", sa.String(16), nullable=False, server_default="token"),   # token | client_cert
        sa.Column("token_enc", sa.LargeBinary()),
        sa.Column("token_nonce", sa.LargeBinary()),
        sa.Column("client_cert", sa.Text()),                                                # 公開憑證，不是機密
        sa.Column("client_key_enc", sa.LargeBinary()),
        sa.Column("client_key_nonce", sa.LargeBinary()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("scope_subnet_ids", JSONB()),
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        # LB 位址集區自動建成「位址範圍（集區）」（預設開）
        sa.Column("create_ip_ranges", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("server_version", sa.String(64)),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("description", sa.Text()),
        _now("created_at"), _now("updated_at"),
    )
    op.create_table(
        "k8s_nodes",
        sa.Column("id", UUID(as_uuid=True), **_ID),
        _cluster_fk(),
        sa.Column("name", sa.String(253), nullable=False),
        sa.Column("ip", INET()),
        sa.Column("addresses", JSONB()),
        sa.Column("roles", JSONB()),
        sa.Column("ready", sa.Boolean()),
        sa.Column("unschedulable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("kubelet_version", sa.String(64)),
        sa.Column("os_image", sa.String(255)),
        sa.Column("kernel", sa.String(128)),
        sa.Column("runtime", sa.String(128)),
        sa.Column("provider_id", sa.String(512)),
        sa.Column("pod_cidrs", JSONB()),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True)),
        sa.Column("jt_ipam_address_id", UUID(as_uuid=True), sa.ForeignKey("ip_addresses.id", ondelete="SET NULL"),
                  index=True),
        sa.Column("virtual_machine_id", UUID(as_uuid=True),
                  sa.ForeignKey("virtual_machines.id", ondelete="SET NULL"), index=True),
        _now("synced_at"),
        sa.UniqueConstraint("cluster_id", "name", name="uq_k8s_node_cluster_name"),
    )
    op.create_index("ix_k8s_nodes_ip", "k8s_nodes", ["ip"])
    op.create_table(
        "k8s_service_ips",
        sa.Column("id", UUID(as_uuid=True), **_ID),
        _cluster_fk(),
        sa.Column("namespace", sa.String(253), nullable=False),
        sa.Column("name", sa.String(253), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),        # loadbalancer | external
        sa.Column("ip", INET(), nullable=False),
        sa.Column("ports", JSONB()),
        sa.Column("jt_ipam_address_id", UUID(as_uuid=True), sa.ForeignKey("ip_addresses.id", ondelete="SET NULL"),
                  index=True),
        _now("synced_at"),
        sa.UniqueConstraint("cluster_id", "namespace", "name", "kind", "ip", name="uq_k8s_service_ip"),
    )
    op.create_index("ix_k8s_service_ips_ip", "k8s_service_ips", ["ip"])
    op.create_table(
        "k8s_ip_pools",
        sa.Column("id", UUID(as_uuid=True), **_ID),
        _cluster_fk(),
        sa.Column("provider", sa.String(16), nullable=False),    # metallb | cilium
        sa.Column("name", sa.String(253), nullable=False),
        sa.Column("namespace", sa.String(253)),
        sa.Column("blocks", JSONB(), nullable=False),            # [{"start","end"}]，CIDR 也展開成起訖
        sa.Column("auto_assign", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        _now("synced_at"),
        sa.UniqueConstraint("cluster_id", "provider", "name", name="uq_k8s_ip_pool"),
    )
    op.create_table(
        "k8s_cidrs",
        sa.Column("id", UUID(as_uuid=True), **_ID),
        _cluster_fk(),
        sa.Column("kind", sa.String(16), nullable=False),        # pod | service
        sa.Column("cidr", CIDR(), nullable=False),
        sa.Column("source", sa.String(253)),                     # 節點名稱或 ServiceCIDR 物件名稱
        _now("synced_at"),
        sa.UniqueConstraint("cluster_id", "kind", "cidr", name="uq_k8s_cidr"),
    )
    op.add_column("ip_addresses", sa.Column("last_seen_k8s", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("ip_addresses", "last_seen_k8s")
    op.drop_table("k8s_cidrs")
    op.drop_table("k8s_ip_pools")
    op.drop_index("ix_k8s_service_ips_ip", table_name="k8s_service_ips")
    op.drop_table("k8s_service_ips")
    op.drop_index("ix_k8s_nodes_ip", table_name="k8s_nodes")
    op.drop_table("k8s_nodes")
    op.drop_table("k8s_clusters")
