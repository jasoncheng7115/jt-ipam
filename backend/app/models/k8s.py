"""Kubernetes 整合 model（services/k8s）。

對 IPAM 有意義的是會佔用或撞到實體網段的位址：節點 IP、LoadBalancer／externalIPs、LB 位址集區
（MetalLB、Cilium、Calico）、Pod／Service 網段。Pod IP 是叢集內部、隨時在變的，不收；例外是 Multus 次要網卡
（Whereabouts 發的實體網段位址）。第二階段（0245）加上 Ingress／Gateway API／OpenShift Route 的主機名稱。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import DateTime as SADateTime
from sqlalchemy.dialects.postgresql import CIDR, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


def _cluster_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("k8s_clusters.id", ondelete="CASCADE"),
                         nullable=False, index=True)


def _now() -> Mapped[datetime]:
    return mapped_column(SADateTime(timezone=True), server_default=func.now(), nullable=False)


class K8sCluster(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """一個 Kubernetes 叢集的 API 連線（唯讀 ServiceAccount）。"""

    __tablename__ = "k8s_clusters"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    api_url: Mapped[str] = mapped_column(Text, nullable=False)
    #: 叢集 CA（PEM）；有填就只信任它
    ca_cert: Mapped[str | None] = mapped_column(Text)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: token（ServiceAccount）或 client_cert（kubeconfig 裡的用戶端憑證）
    auth_method: Mapped[str] = mapped_column(String(16), default="token", nullable=False)
    token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    token_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    client_cert: Mapped[str | None] = mapped_column(Text)
    client_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    client_key_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    scope_subnet_ids: Mapped[list[Any] | None] = mapped_column(JSONB)
    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    #: LB 位址集區自動建成「位址範圍（集區）」
    create_ip_ranges: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: 整個叢集的 Pod 網段可以直接路由到實體網路（Cilium native routing、BGP 發佈、扁平網路）：
    #: Pod 網段跟子網路重疊是刻意的對應，不算衝突。Calico 集區自己看得出來，不靠這個
    pod_network_routable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: 上一輪同步唯讀帳號讀不到（403）的資源，例如還在用第一階段的 ClusterRole；None＝都讀得到
    missing_permissions: Mapped[list[Any] | None] = mapped_column(JSONB)
    server_version: Mapped[str | None] = mapped_column(String(64))
    last_sync_at: Mapped[datetime | None] = mapped_column(SADateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)


class K8sNode(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "k8s_nodes"
    __table_args__ = (UniqueConstraint("cluster_id", "name", name="uq_k8s_node_cluster_name"),)

    cluster_id: Mapped[uuid.UUID] = _cluster_fk()
    name: Mapped[str] = mapped_column(String(253), nullable=False)
    #: 主要位址（第一個 InternalIP，沒有就 ExternalIP）
    ip: Mapped[str | None] = mapped_column(INET)
    addresses: Mapped[list[Any] | None] = mapped_column(JSONB)
    roles: Mapped[list[Any] | None] = mapped_column(JSONB)
    ready: Mapped[bool | None] = mapped_column(Boolean)
    unschedulable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    kubelet_version: Mapped[str | None] = mapped_column(String(64))
    os_image: Mapped[str | None] = mapped_column(String(255))
    kernel: Mapped[str | None] = mapped_column(String(128))
    runtime: Mapped[str | None] = mapped_column(String(128))
    #: `openstack:///<uuid>`、`vsphere://<uuid>`…：用來對到既有的 VM
    provider_id: Mapped[str | None] = mapped_column(String(512))
    pod_cidrs: Mapped[list[Any] | None] = mapped_column(JSONB)
    last_heartbeat: Mapped[datetime | None] = mapped_column(SADateTime(timezone=True))
    jt_ipam_address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ip_addresses.id", ondelete="SET NULL"), index=True)
    virtual_machine_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("virtual_machines.id", ondelete="SET NULL"), index=True)
    synced_at: Mapped[datetime] = _now()


class K8sServiceIP(Base, UUIDPrimaryKeyMixin):
    """佔用實體網段的位址：LoadBalancer 發到的 IP、spec.externalIPs、Pod 次要網卡的位址（Whereabouts、Multus static）、
    Egress IP（OVN-Kubernetes、Cilium、Antrea），以及 API 位址本身是 IP 時的 API 伺服器位址。"""

    __tablename__ = "k8s_service_ips"
    __table_args__ = (UniqueConstraint("cluster_id", "namespace", "name", "kind", "ip", name="uq_k8s_service_ip"),)

    cluster_id: Mapped[uuid.UUID] = _cluster_fk()
    namespace: Mapped[str] = mapped_column(String(253), nullable=False)
    name: Mapped[str] = mapped_column(String(253), nullable=False)
    #: loadbalancer | external | pod（Whereabouts 的 name 是 Pod、Multus static 的是 NetworkAttachmentDefinition）
    #: | egress（name 是 Egress 物件，namespace 空白）| apiserver（default/kubernetes）
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ip: Mapped[str] = mapped_column(INET, nullable=False)
    ports: Mapped[list[Any] | None] = mapped_column(JSONB)
    #: Egress IP 目前在哪個節點上（OVN-Kubernetes、Antrea 的 status 才有）
    node: Mapped[str | None] = mapped_column(String(253))
    jt_ipam_address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ip_addresses.id", ondelete="SET NULL"), index=True)
    synced_at: Mapped[datetime] = _now()


class K8sIPPool(Base, UUIDPrimaryKeyMixin):
    """實體網段上的位址集區：LoadBalancer 用（MetalLB、Cilium、Calico allowedUses=LoadBalancer）
    與 Pod 次要網卡用（Whereabouts）。"""

    __tablename__ = "k8s_ip_pools"
    __table_args__ = (UniqueConstraint("cluster_id", "provider", "name", name="uq_k8s_ip_pool"),)

    cluster_id: Mapped[uuid.UUID] = _cluster_fk()
    #: metallb | cilium | calico | whereabouts | antrea | multus
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    #: loadbalancer | pod | external（Antrea ExternalIPPool：LoadBalancer 與 Egress 共用）
    use: Mapped[str] = mapped_column(String(16), default="loadbalancer", server_default="loadbalancer", nullable=False)
    name: Mapped[str] = mapped_column(String(253), nullable=False)
    namespace: Mapped[str | None] = mapped_column(String(253))
    #: [{"start": ..., "end": ...}]（CIDR 也展開成起訖）
    blocks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    auto_assign: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    synced_at: Mapped[datetime] = _now()


class K8sCidr(Base, UUIDPrimaryKeyMixin):
    """叢集網段：節點的 podCIDR、Calico 的 Workload 集區、ServiceCIDR（跟實體子網路重疊要告警，可路由的除外）。"""

    __tablename__ = "k8s_cidrs"
    __table_args__ = (UniqueConstraint("cluster_id", "kind", "cidr", name="uq_k8s_cidr"),)

    cluster_id: Mapped[uuid.UUID] = _cluster_fk()
    #: pod | service
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    cidr: Mapped[str] = mapped_column(CIDR, nullable=False)
    source: Mapped[str | None] = mapped_column(String(253))
    #: 能不能直接路由到實體網路：Calico 集區不 NAT 也不封裝＝True、有 NAT 或封裝＝False；看不出來（節點 podCIDR）＝None
    routable: Mapped[bool | None] = mapped_column(Boolean)
    #: IPIP | VXLAN | None（Calico）
    encapsulation: Mapped[str | None] = mapped_column(String(16))
    synced_at: Mapped[datetime] = _now()


class K8sHostname(Base, UUIDPrimaryKeyMixin):
    """網域名稱 → 位址：Ingress、Gateway（listener）、HTTPRoute／GRPCRoute、OpenShift Route。

    一個名稱有幾個位址就幾列；還沒拿到位址（LB pending、找不到 router 的 Service）的 ip 是 NULL。
    """

    __tablename__ = "k8s_hostnames"

    cluster_id: Mapped[uuid.UUID] = _cluster_fk()
    #: ingress | gateway | httproute | grpcroute | route
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    namespace: Mapped[str] = mapped_column(String(253), nullable=False)
    name: Mapped[str] = mapped_column(String(253), nullable=False)
    #: 小寫；可能是萬用字元（*.apps.example.com）
    host: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    ip: Mapped[str | None] = mapped_column(INET, index=True)
    tls: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    jt_ipam_address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ip_addresses.id", ondelete="SET NULL"), index=True)
    synced_at: Mapped[datetime] = _now()
