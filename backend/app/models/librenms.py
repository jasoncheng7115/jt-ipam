"""LibreNMS 整合 model。

設計：
- LibreNMSInstance：多站點支援（規格書 §6.10）；api_token 加密
- LibreNMSDevice：每次 sync 從 LibreNMS 抓回的裝置；legacy_id = LibreNMS device_id
- ARPEntry：從 LibreNMS API /resources/ip/arp/ 取得，自動補 IP 的 MAC；
  掃描代理與防火牆 ARP 表的觀測也寫這裡（IP 衝突偵測的依據，issue #41）
- FDBEntry：從 LibreNMS API /devices/{id}/fdb 取得，定位 MAC 在哪個 switch port
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, MACADDR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LibreNMSInstance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "librenms_instances"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    api_url: Mapped[str] = mapped_column(Text, nullable=False)

    api_token_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_token_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 關閉＝接受自簽/主機名稱不符（httpx verify=False；與 Wazuh 同機制）
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="true")

    # 細分開關（規格書 §6.10）
    sync_devices: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_arp: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_fdb: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_vlans: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # LLDP / CDP 鄰居（LibreNMS 的 links 表）—— 需要對方也開 LLDP/CDP 且 LibreNMS
    # 的 xdp discovery 有跑到，否則來源本身就是空的
    sync_links: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default="true",
    )
    use_for_status: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_add_devices: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true"),
    )
    # 同步裝置時，把落在「既有且符合 scope」子網路內的裝置主 IP 自動建成 IPAddress
    # （discovery_source='librenms'）。預設開啟——使用者通常預期「接了 NMS 就會長出 IP」。
    # 只建裝置主 IP，不建 ARP 學到的鄰居（避免把雜訊端點灌進來）。
    auto_create_ips: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true"),
    )
    # 依 ARP 表自動建立 IP（GitHub #48，0173）：**預設關**。開了之後私接的設備也會被收錄，
    # 而且從此不再出現在「未授權 IP」異常偵測（那道偵測看的就是「ARP 看得到、IPAM 沒有」）。
    # 規則與把關見 services/arp_autocreate.py。
    auto_create_from_arp: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false"),
    )
    # 要交換器 MAC 表（FDB）在 24 小時內也看過這個 MAC 才建。LibreNMS 的 ARP 沒有時間欄位，
    # 而有些設備的 ARP 快取幾小時到幾天才清（Cisco 預設 4 小時，有的要到介面斷線或重開機）；
    # MAC 表通常 5 分鐘就老化，所以拿它證明「最近真的有在講話」。
    arp_create_require_fdb: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true"),
    )
    # 略過 DHCP 動態範圍內的位址（那段會回收再發給別台，建了只是一直換人）
    arp_create_skip_dhcp: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true"),
    )

    # VLAN 同步寫進哪個 domain（GitHub issue #53，0211）：NULL＝專用的「LibreNMS」domain（原本的行為）。
    # 指定了就沿用那個 domain 裡同編號的 VLAN、只補 device↔VLAN 對應，已有的名稱不覆寫
    vlan_domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vlan_domains.id", ondelete="SET NULL"), nullable=True, index=True)
    # 指定 domain 時，交換器上有、domain 裡沒有的 VLAN 要不要新建（關掉＝只對應既有的，
    # 交換器預設的 1002–1005 之類就不會被帶進來）
    vlan_create_missing: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true"))

    # 限定 sync 解析 IP 的子網路範圍（解決重疊網段：A/B 客戶都用 192.168.123.x）。
    # 空 = 全域比對（向下相容）。存 subnet UUID 字串陣列。
    scope_subnet_ids: Mapped[list[Any] | None] = mapped_column(JSONB)

    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class LibreNMSDevice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "librenms_devices"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("librenms_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    legacy_device_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    hostname: Mapped[str | None] = mapped_column(Text)
    sysname: Mapped[str | None] = mapped_column(Text)
    primary_ip: Mapped[str | None] = mapped_column(INET)
    hardware: Mapped[str | None] = mapped_column(Text)
    os: Mapped[str | None] = mapped_column(Text)
    # LibreNMS 原生 device type（network/server/firewall/power/wireless/storage/…）
    type: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(Text)
    serial: Mapped[str | None] = mapped_column(Text)
    sysObjectID: Mapped[str | None] = mapped_column(Text)
    uptime: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str | None] = mapped_column(String(16))   # up / down

    # 對映到 jt-ipam 的 Device（如已連結）
    jt_ipam_device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
    )

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "instance_id", "legacy_device_id", name="librenms_device_unique",
        ),
    )


class ARPEntry(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "arp_entries"

    ip: Mapped[str] = mapped_column(INET, nullable=False, index=True)
    mac: Mapped[str] = mapped_column(MACADDR, nullable=False, index=True)
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("librenms_instances.id", ondelete="SET NULL"),
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("librenms_devices.id", ondelete="SET NULL"),
    )
    interface: Mapped[str | None] = mapped_column(String(64))
    vrf: Mapped[str | None] = mapped_column(String(64))
    # librenms / scanner / arp:<廠牌>（防火牆 ARP 表）。非 LibreNMS 的觀測沒有 device_id，
    # 改用 subnet_id 界定範圍 —— 重疊網段不可以互相判成衝突（issue #41，見 services/arp_evidence.py）
    source: Mapped[str] = mapped_column(String(16), default="librenms", nullable=False)
    subnet_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subnets.id", ondelete="CASCADE"), index=True,
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )

    __table_args__ = (
        UniqueConstraint("ip", "mac", "device_id", name="arp_entry_unique"),
        # 掃描代理／防火牆的觀測：同一個 IP、MAC、來源、子網路只一筆，再看到只更新時間
        Index("arp_entry_observed_unique", "ip", "mac", "source", "subnet_id", unique=True,
              postgresql_where=text("device_id IS NULL AND subnet_id IS NOT NULL")),
    )


class FDBEntry(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "fdb_entries"

    mac: Mapped[str] = mapped_column(MACADDR, nullable=False, index=True)
    vlan_id_num: Mapped[int | None] = mapped_column(Integer)
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("librenms_instances.id", ondelete="SET NULL"),
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("librenms_devices.id", ondelete="SET NULL"),
    )
    port_name: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), default="librenms", nullable=False)
    #: source=mikrotik 的列（0170）：直接記 jt-ipam 裝置（`device_id` 是 LibreNMS 裝置，這裡沒有），
    #: 以及是哪台路由器回報的（刪路由器時跟著刪）
    switch_device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    mikrotik_router_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mikrotik_routers.id", ondelete="CASCADE"), index=True)
    #: source=snmp 的列（0217）：哪個 SNMP 收集目標回報的（刪目標時跟著刪）；switch_device_id 同樣記目標裝置
    snmp_target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("snmp_targets.id", ondelete="CASCADE"), index=True)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "mac", "device_id", "port_name", "vlan_id_num",
            name="fdb_entry_unique",
        ),
    )


class LibreNMSLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """LLDP / CDP 探到的鄰居連線（鏡像 LibreNMS 的 `links` 表）。

    與 FDB/ARP 推導的差別：這是**對方自己宣告**的「我是誰、你接在我哪個埠」，
    所以交換器之間的 trunk 也能正確畫出來 —— 那正是 FDB 推導最不準的地方
    （trunk 埠上 MAC 太多，「MAC 數最少者為 access port」的啟發法會失準）。

    remote_* 允許為空：對端不一定也被 LibreNMS 監控，此時只有 hostname/platform
    這類 LLDP 通報字串，沒有可對映的 device id。
    """

    __tablename__ = "librenms_links"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("librenms_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # LibreNMS 端的識別（links.id）；用來做 upsert 與清除已消失的連線
    legacy_link_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    protocol: Mapped[str | None] = mapped_column(String(16))       # lldp / cdp / edp …
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    local_device_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    local_port_id: Mapped[int | None] = mapped_column(BigInteger)
    local_port_name: Mapped[str | None] = mapped_column(Text)

    remote_device_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    remote_port_id: Mapped[int | None] = mapped_column(BigInteger)
    remote_hostname: Mapped[str | None] = mapped_column(Text)
    remote_port: Mapped[str | None] = mapped_column(Text)
    remote_platform: Mapped[str | None] = mapped_column(Text)
    remote_version: Mapped[str | None] = mapped_column(Text)

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("instance_id", "legacy_link_id", name="librenms_link_unique"),
    )
