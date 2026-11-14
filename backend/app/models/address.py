"""IP Address — phpIPAM 對齊 + v0.3 多源欄位。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, MACADDR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class IPAddress(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ip_addresses"

    subnet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subnets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ip: Mapped[str] = mapped_column(INET, nullable=False)
    hostname: Mapped[str | None] = mapped_column(Text, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    mac: Mapped[str | None] = mapped_column(MACADDR, index=True)
    mac_source: Mapped[str | None] = mapped_column(String(16))  # 目前 MAC 的來源（ARP 優先序用）
    owner: Mapped[str | None] = mapped_column(Text)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
    )
    # 共用給裝置群組（堆疊管理 IP、HA 虛擬 IP；0241）：設了它，device_id 一律是群組的代表裝置
    # （services/device_groups 維持；沒經過檢查的寫入在存檔時改回）
    device_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("device_groups.id", ondelete="SET NULL"), index=True)
    switch_port: Mapped[str | None] = mapped_column(Text)
    # FDB 推得的交換器位置是否高信心（該 port 僅一個 MAC = 直連存取埠；
    # 多 MAC（uplink/trunk）→ False，前端以灰色 + tooltip 標示）
    switch_port_confident: Mapped[bool | None] = mapped_column(Boolean)

    exclude_from_ping: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: 上線狀態跟著所屬設備（0215，客戶 2026-10-14）：多 LAN 路由器的閘道不回 ping 但在用。
    #: 每輪把同設備其他位址最新的會過期證據搬到 arp_seen["device"]（services/device_liveness）；
    #: 沒掛設備時不算離線（「使用中（不回應探測）」）
    liveness_from_device: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false"))
    #: 「跟著設備」這一輪的依據是同設備的哪個位址（沒有＝來自 LibreNMS 的設備狀態或沒有依據）
    device_evidence_ip: Mapped[str | None] = mapped_column(INET)
    # 此 IP「略過」的探測項目（扣除）；icmp 與 exclude_from_ping 雙向同步以保留既有行為。
    excluded_probes: Mapped[list[str]] = mapped_column(
        ARRAY(String), server_default=text("'{}'::varchar[]"), nullable=False,
    )
    # OS 偵測結果：原始字串 + 正規化家族 key（前端依 family 配 icon）。see core/os_fingerprint.py
    os_guess: Mapped[str | None] = mapped_column(String(160))
    os_family: Mapped[str | None] = mapped_column(String(24))
    #: 掃描代理定期偵測判讀出的設備類型（camera / printer / storage …，見 services/device_identity）
    #: 與廠牌型號。判讀與 IP 探測同一套（含 Recog 指紋庫）；判讀不出來時保留上一次的結果。
    device_kind: Mapped[str | None] = mapped_column(String(24))
    device_model: Mapped[str | None] = mapped_column(String(120))
    device_identified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: 目前這台設備從什麼時候開始用這個位址（0209）：MAC 換成另一張網卡時寫（services/device_turnover.py）。
    #: 拿來判斷綁在位址上的資料（RustDesk、Wazuh、OCS、探測結果、已存帳密、AI 忽略）是不是換手前的
    device_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: OCS Inventory 代理回報的 OS 原始字串。獨立欄位（不污染掃描代理的 os_guess）——
    #: 經 os_precedence 排在 scanner 之上，讓 agent 回報的 OS 蓋過 nmap 的指紋猜測。
    os_ocs: Mapped[str | None] = mapped_column(String(160))
    #: 對到的 OCS Inventory 電腦 systemid，供裝置明細「在 OCS 檢視」深連結。
    ocs_id: Mapped[int | None] = mapped_column(BigInteger)
    #: OCS 資產標籤（accountinfo.TAG）。
    ocs_tag: Mapped[str | None] = mapped_column(String(128))
    #: OCS 代理版本（hardware.USERAGENT）。
    ocs_agent: Mapped[str | None] = mapped_column(String(128))
    #: OCS 最新幾筆備註（itmgmt_comments）：[{date,user,comment,action}]。
    ocs_notes: Mapped[list[Any] | None] = mapped_column(JSONB)
    #: OCS 回報的硬體摘要（services/ocs.hardware_summary）：系統／主機板／BIOS／CPU／記憶體／磁碟／顯示卡。
    #: 裝置明細的 OCS 卡片顯示這一份，而不是裝置本身的欄位（那可能是別的來源寫的）。
    ocs_hw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 各 probe 上次被執行的時間（由 report 回填），給「下次到期」顯示用。{"icmp": "...", "os": "..."}
    probe_last_run: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ptr_ignore: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 這個 IP 要忽略哪幾類異常（0137）。逐類別而不是一個總開關：標了「這台會自己
    # 換 MAC」（隱私隨機化）不代表它失聯也不用報。
    anomaly_ignore: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"),
    )
    note: Mapped[str | None] = mapped_column(Text)

    # 主控台的連線出口（issue #24）：空＝沿用所屬子網路的設定；有值就覆寫它
    jump_host_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jump_hosts.id", ondelete="SET NULL"), index=True,
    )
    # 或經由掃描代理中繼（issue #24 階段二，0175）；與 jump_host_id 只能擇一
    console_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scan_agents.id", ondelete="SET NULL"), index=True,
    )

    custom_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        index=True,
    )

    # feature A：固定以某來源的 hostname 為準（NULL = 跟全域優先序）
    hostname_source_pin: Mapped[str | None] = mapped_column(String(16))

    # v0.3 多來源
    discovery_source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    # 自動判定：此 IP 目前有 DHCP 租約（由 OPNsense DHCP lease 同步維護，與手動 state 分開）
    in_dhcp_lease: Mapped[bool] = mapped_column(default=False, nullable=False, server_default=text("false"))
    # DHCP 有把這個位址固定綁給某張網卡（reservation / static mapping）。
    # 與 in_dhcp_lease 意義不同：有租約＝現在有人在用；固定分配＝這個位址不會被換人用。
    dhcp_reserved: Mapped[bool] = mapped_column(default=False, nullable=False, server_default=text("false"))
    # 手動標記：此 IP 是 DHCP 伺服器（清單視覺化用；另有「對應防火牆 IP」自動判定）
    is_dhcp_server: Mapped[bool] = mapped_column(default=False, nullable=False, server_default=text("false"))
    last_seen_scanner: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_librenms: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ARP 證據獨立存：LibreNMS 的 ARP API 不回時間，只能靠「還在清單裡」推斷，
    # 可信度遠低於裝置狀態（來源設備快取不老化就會永遠是「剛看到」）
    last_seen_arp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: 逐來源的 ARP 觀測時間：`{"opnsense": "…", "librenms": "…"}`。
    #:
    #: 為什麼要分來源：各家 ARP 表的「會不會過期」差很多。防火牆的 ARP 條目會自己
    #: 老化、而且我們每輪重讀，所以「這輪還在」是有時間意義的；LibreNMS 的 ARP API
    #: 不回任何時間，來源設備（AP／路由器）的快取不老化就會永遠看起來是剛看到。
    #: 混成同一個欄位就沒辦法讓管理員只採信前者。
    arp_seen: Mapped[dict] = mapped_column(   # type: ignore[type-arg]
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict,
    )
    #: Wazuh agent 最後一次 keep-alive（manager 端維護，會過期）
    last_seen_wazuh: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Zabbix 最後一次回報這台主機可用（server 端輪詢維護，會過期）
    last_seen_zabbix: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Checkmk 最後一次檢查到這台主機 UP（監控核心輪詢維護，會過期）
    last_seen_checkmk: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: OCS Inventory 最後一次盤點到這個 IP。**顯示用、不進上線判定** ——
    #: 盤點時間不是活性訊號（機器關機後 agent 不會回報，但上次盤點時間還在）。
    last_seen_ocs: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_dns: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_status: Mapped[str | None] = mapped_column(String(32))

    # SSH 連線管理：是否對此 IP 啟用 SSH 終端機（控制詳細資料頁 SSH 按鈕是否出現）。
    ssh_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    # TOFU 信任後釘選的 host key（單行 known_host 格式；非機密，僅防 MITM）。
    ssh_host_key: Mapped[str | None] = mapped_column(Text)
    # SFTP 檔案瀏覽器：獨立於 SSH 的開關 —— 有些主機只想開放傳檔、不想開終端機。
    # 走的是同一條 SSH 連線，所以仍需 SSH 服務可用，但要不要開放由這個欄位決定。
    sftp_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    # RDP 連線管理：是否對此 IP 啟用 RDP（控制詳細資料頁 RDP 按鈕是否出現）。
    rdp_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    # VNC 連線管理：是否對此 IP 啟用 VNC（控制詳細資料頁 VNC 按鈕是否出現）。
    vnc_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    # PVE 主控台（qemu→noVNC / lxc→xterm）；僅對應到 Proxmox VM/CT 的 IP 有意義
    novnc_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    # BMC OOB主控台（IPMI SOL：鍵盤 + 文字畫面）；針對 BMC 管理 IP
    bmc_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    # 「以 RustDesk 連線」按鈕（叫出操作者電腦上的 RustDesk 客戶端）；比照 VNC 逐 IP 開啟
    rustdesk_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        UniqueConstraint("subnet_id", "ip", name="ip_subnet_ip_uq"),
        CheckConstraint(
            # deprecated／quarantine 是 0218 才加進來的（之前畫面能選、存檔卻 500）
            "state IN ('active','reserved','offline','dhcp','used','deprecated','quarantine')",
            name="ip_state_valid",
        ),
        CheckConstraint(
            "discovery_source IN ('manual','scanner','librenms','dns','proxmox','opnsense','phpipam',"
            "'pfsense','vmware','librenms_arp','isoinsight','snmp_arp','hyperv','openstack')",
            name="ip_discovery_source_valid",
        ),
        Index("ix_ip_addresses_ip_gist", "ip", postgresql_using="gist"),
        CheckConstraint("jump_host_id IS NULL OR console_agent_id IS NULL",
                        name="ip_console_egress_one"),
    )
