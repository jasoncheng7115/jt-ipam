"""Virtualization：Cluster / VirtualMachine / VMInterface（NetBox 風格）。

主要與 Proxmox VE 對接（Phase 3：Proxmox 為唯一 reference）。Cluster 是
Proxmox cluster；每個 VM 屬於一個 cluster + 可能對映到 jt-ipam Device。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import INET, MACADDR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class VirtCluster(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "virt_clusters"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(32), default="proxmox", nullable=False)
    is_standalone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="SET NULL"),
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
    )
    # 所屬單位 / 客戶（決定 VM 屬於哪個單位；IP 關係鏈只連同單位的 VM）
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        index=True,
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('proxmox','vmware','hyper-v','kvm','xenserver','other')",
            name="ck_virt_clusters_type_valid",
        ),
    )


class VirtualMachine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "virtual_machines"

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("virt_clusters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    legacy_vmid: Mapped[int | None] = mapped_column(BigInteger, index=True)  # Proxmox VMID
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    node: Mapped[str | None] = mapped_column(String(128))   # 所在 PVE 節點（host）
    kind: Mapped[str | None] = mapped_column(String(8))      # "vm"（qemu）/ "ct"（lxc）
    status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    vcpus: Mapped[int | None] = mapped_column(Integer)
    memory_mb: Mapped[int | None] = mapped_column(Integer)
    disk_gb: Mapped[int | None] = mapped_column(Integer)

    primary_ip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_addresses.id", ondelete="SET NULL"),
    )
    # 對映到 jt-ipam Device（如已連結）
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
    )

    description: Mapped[str | None] = mapped_column(Text)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("cluster_id", "name", name="vm_cluster_name_uq"),
        CheckConstraint(
            "status IN ('running','stopped','paused','migrating','unknown')",
            name="ck_virtual_machines_status_valid",
        ),
    )


class VMInterface(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "vm_interfaces"

    vm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("virtual_machines.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    mac: Mapped[str | None] = mapped_column(MACADDR)
    primary_ip: Mapped[str | None] = mapped_column(INET)
    bridge: Mapped[str | None] = mapped_column(String(64))
    vlan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vlans.id", ondelete="SET NULL"),
    )

    __table_args__ = (
        UniqueConstraint("vm_id", "name", name="vmif_vm_name_uq"),
    )


class ProxmoxInstance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Proxmox VE API 連線實例。"""

    __tablename__ = "proxmox_instances"

    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("virt_clusters.id", ondelete="CASCADE"),
        nullable=True,   # 同步時依 PVE 叢集名稱自動指派
        index=True,
    )
    api_url: Mapped[str] = mapped_column(Text, nullable=False)
    # 同一 cluster 其他節點的 API URL（換行 / 逗號分隔），主節點故障時自動換手
    extra_api_urls: Mapped[str | None] = mapped_column(Text)
    # Proxmox API token：username + token_id + token_secret
    auth_username: Mapped[str] = mapped_column(String(128), nullable=False)
    auth_token_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # token secret 走 EncryptedSecret 表，這裡只放索引欄位
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=600, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
