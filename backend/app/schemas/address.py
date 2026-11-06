"""IPAddress schemas。"""

from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, field_validator

from app.schemas.base import StrictModel

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}([:\-]|$)){6}$|^[0-9A-Fa-f]{12}$")
#: IP 狀態的允許清單（REST 建立與 AI 工具共用；說明見 IPAddressBase._state_valid）
IP_STATES: frozenset[str] = frozenset({"active", "reserved", "offline", "dhcp", "used",
                                       "deprecated", "quarantine"})
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)


class IPAddressBase(StrictModel):
    subnet_id: uuid.UUID
    ip: Annotated[str, Field(min_length=2, max_length=64)]
    hostname: str | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    state: str = "active"
    mac: str | None = None
    owner: Annotated[str | None, Field(max_length=128)] = None
    device_id: uuid.UUID | None = None
    switch_port: Annotated[str | None, Field(max_length=64)] = None
    exclude_from_ping: bool = False
    #: 上線狀態跟著所屬設備（多 LAN 路由器的閘道不回 ping 但在用；services/device_liveness）
    liveness_from_device: bool = False
    # 此 IP 略過的探測項目（扣除）；icmp 與 exclude_from_ping 在端點層雙向同步
    excluded_probes: list[str] = Field(default_factory=list)
    ptr_ignore: bool = False
    note: Annotated[str | None, Field(max_length=2048)] = None
    customer_id: uuid.UUID | None = None
    #: 主控台的連線出口（issue #24）：空＝繼承上層或直連
    jump_host_id: uuid.UUID | None = None
    #: 或經由掃描代理中繼（階段二）：必須是所屬子網路的掃描代理；與 jump_host_id 擇一
    console_agent_id: uuid.UUID | None = None
    custom_fields: dict[str, Any] | None = None

    @field_validator("ip", mode="before")
    @classmethod
    def _ip_valid(cls, v: object) -> str:
        if v is None:
            raise ValueError("ip is required")
        # asyncpg 把 inet 反序列化為 ipaddress.IPv4Address/IPv6Address；轉成字串
        s = str(v).split("/")[0] if hasattr(v, "compressed") else str(v)
        try:
            ipaddress.ip_address(s)
        except ValueError as exc:
            raise ValueError(f"Invalid IP address: {s}") from exc
        return s

    @field_validator("hostname")
    @classmethod
    def _hostname_normalize(cls, v: str | None) -> str | None:
        # Base 只負責正規化（空字串視為 None）；嚴格 DNS 驗證僅在 Create 時做，
        # 因為 Read schema 也吃這支：DB 既有資料可能來自 phpIPAM 匯入 / 早期版本，
        # 不該因為一筆中文 hostname 就讓整個 list endpoint 500。
        if v is None or v == "":
            return None
        return v

    @field_validator("mac", mode="before")
    @classmethod
    def _mac_valid(cls, v: object) -> str | None:
        if v is None or v == "":
            return None
        v = str(v)   # asyncpg macaddr → str
        if not _MAC_RE.match(v):
            raise ValueError("Invalid MAC address")
        return v

    @field_validator("state")
    @classmethod
    def _state_valid(cls, v: str) -> str:
        # active/used/dhcp/offline 是既有字彙（混了觀測與意圖，維持相容不動）；
        # deprecated/quarantine 是生命週期用的意圖標記：
        #   deprecated — 準備退場，不要再拿來配新服務
        #   quarantine — 有疑慮（資安事件／衝突），暫時不得使用
        if v not in IP_STATES:
            raise ValueError(f"state must be one of {sorted(IP_STATES)}")
        return v


class IPAddressCreate(IPAddressBase):
    @field_validator("hostname")
    @classmethod
    def _hostname_strict(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _HOSTNAME_RE.match(v):
            raise ValueError("Invalid hostname")
        return v


class IPAddressAllocate(StrictModel):
    """配發第一個空閒 IP（不需指定 IP）。"""

    subnet_id: uuid.UUID
    hostname: str | None = None
    description: str | None = None
    mac: str | None = None
    state: str = "active"


class IPAddressUpdate(StrictModel):
    hostname: str | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    state: str | None = None
    mac: str | None = None
    owner: Annotated[str | None, Field(max_length=128)] = None
    device_id: uuid.UUID | None = None
    # 共用給裝置群組：給群組 id＝共用給它（設備欄自動是代表裝置）；給 null＝退出群組（留在原本的代表裝置上）
    device_group_id: uuid.UUID | None = None
    switch_port: Annotated[str | None, Field(max_length=64)] = None
    exclude_from_ping: bool | None = None
    liveness_from_device: bool | None = None
    excluded_probes: list[str] | None = None
    ptr_ignore: bool | None = None
    note: Annotated[str | None, Field(max_length=2048)] = None
    customer_id: uuid.UUID | None = None
    #: 主控台的連線出口（issue #24）：空＝繼承上層或直連
    jump_host_id: uuid.UUID | None = None
    #: 或經由掃描代理中繼（階段二）：必須是所屬子網路的掃描代理；與 jump_host_id 擇一
    console_agent_id: uuid.UUID | None = None
    custom_fields: dict[str, Any] | None = None
    # feature A：固定以某來源 hostname 為準（"" / null = 跟全域優先序）
    hostname_source_pin: Annotated[str | None, Field(max_length=16)] = None
    # SSH 連線管理開關（沿用 IP 編輯權限）
    ssh_enabled: bool | None = None
    # SFTP 檔案傳輸開關（與 SSH 各自獨立：可以只開傳檔、不開終端機）
    sftp_enabled: bool | None = None
    # RDP 連線管理開關（沿用 IP 編輯權限）
    rdp_enabled: bool | None = None
    # VNC 連線管理開關（沿用 IP 編輯權限）
    vnc_enabled: bool | None = None
    # PVE 主控台（noVNC/xterm）連線管理開關（沿用 IP 編輯權限；僅對應到 PVE VM/CT 的 IP 有意義）
    novnc_enabled: bool | None = None
    # BMC OOB主控台（IPMI SOL）開關
    bmc_enabled: bool | None = None
    # 「以 RustDesk 連線」按鈕開關（沿用 IP 編輯權限）
    rustdesk_enabled: bool | None = None
    # 手動標記此 IP 是 DHCP 伺服器（清單視覺化用）
    is_dhcp_server: bool | None = None
    # ip / subnet_id 不允許更新；如要搬移走專用 endpoint


class PveConsoleTarget(StrictModel):
    """此 IP 對應到的 Proxmox VE 主控台目標（前端用來決定 noVNC vs xterm + 顯示 PVE 小標）。"""
    kind: str            # "vm"（qemu→noVNC）/ "ct"（lxc→xterm）
    node: str            # PVE 節點 host
    vmid: int            # Proxmox VMID
    cluster: str | None = None   # 叢集名稱（顯示用）


class IPAddressRead(IPAddressBase):
    id: uuid.UUID
    discovery_source: str
    #: 「跟著設備」這一輪的依據是同設備的哪個位址（每輪同步更新）
    device_evidence_ip: str | None = None

    @field_validator("device_evidence_ip", mode="before")
    @classmethod
    def _evidence_ip(cls, v: object) -> str | None:
        return None if v is None else str(v).split("/")[0]
    in_dhcp_lease: bool = False   # 自動判定：目前有 DHCP 租約（由各 DHCP 來源推導，見 services/dhcp_leases.py）
    # DHCP 上有把這個位址固定綁給某張網卡。與「有租約」意義不同：有租約＝現在有人在用，
    # 固定分配＝這個位址不會被換人用（位址被回收再發給別台，正是資料張冠李戴的來源）。
    dhcp_reserved: bool = False
    dhcp_reservation: dict[str, Any] | None = None   # 明細：綁哪張網卡、哪台 DHCP（詳細資料頁用）
    # DHCP 伺服器把這個位址排除在發放範圍外（Windows Exclusion、FortiGate exclude-range…，0207）：
    # 不會發給任何人，通常是設了固定 IP 的設備。{source_type, source_name, engine, start, end}
    dhcp_exclusion: dict[str, Any] | None = None
    # ── 清單視覺化用的特殊角色旗標 ──
    is_dhcp_server: bool = False     # 手動標記為 DHCP 伺服器
    dhcp_server_auto: bool = False   # 自動：此 IP = 整合中的 DHCP 來源指向的主機（讀取端推導，services/dhcp_known）
    dhcp_server_integration: str | None = None   # 對到哪一種整合（產品名稱，不帶整合名稱），給提示用
    is_gateway: bool = False         # 此 IP = 所屬子網路的閘道（讀取端推導）
    in_dhcp_range: bool = False      # 此 IP 落在 OPNsense DHCP pool 範圍內（讀取端推導）
    # 掃描代理實際觀測到這個位址在回應 DHCP 的時間（讀取端推導）。
    # 跟 is_dhcp_server 是兩回事：那個是「有沒有登記」，這個是「有沒有真的在發」。
    dhcp_observed_at: str | None = None
    hostname_source_pin: str | None = None
    switch_port_confident: bool | None = None
    os_guess: str | None = None
    os_family: str | None = None
    os_source: str | None = None   # 有效 OS 來自哪個來源（scanner/librenms/wazuh）
    device_kind: str | None = None             # 掃描代理判讀出的設備類型（services/device_identity）
    device_model: str | None = None            # 廠牌型號
    device_identified_at: datetime | None = None
    # 目前這台設備從什麼時候開始用這個位址（MAC 換成另一台時寫；之前的推導資料不採用）
    device_since: datetime | None = None
    probe_last_run: dict[str, Any] | None = None
    # 此 IP 實際會被執行的探測（subnet.scan_method − excluded − ∩ agent 能力），後端算好
    effective_probes: list[str] | None = None
    last_seen_scanner: datetime | None
    last_seen_librenms: datetime | None
    # ARP 證據獨立回傳：它沒有時間概念，前端要能標成弱證據而不是混進 LibreNMS
    last_seen_arp: datetime | None = None
    #: Wazuh agent 的 keep-alive（manager 端維護，會過期 → 可宣稱上線）
    last_seen_wazuh: datetime | None = None
    #: Zabbix 最後一次回報可用（server 端輪詢維護）
    last_seen_zabbix: datetime | None = None
    #: 防火牆給的逐來源觀測時間：`{"arp:opnsense": "…", "lease:pfsense": "…"}`。
    #: 這些原本全都被寫進 last_seen_scanner，畫面上就變成沒有掃描代理卻顯示
    #: 「上線 (scanner)」（見 services/arp_seen.py）。
    arp_seen: dict[str, str] = {}
    last_seen_dns: datetime | None
    #: 「各來源最後出現」的來源有沒有設定整合（只在單筆讀取帶）：{"librenms": bool, "adguard": bool}。
    #: 沒設定的整合不列那一列（使用者 2026-10-08）；只有布林值，看得到這個 IP 的人都可以拿
    seen_integrations: dict[str, bool] | None = None
    #: OCS Inventory 最後一次盤點到這個 IP（顯示用、非活性訊號）
    last_seen_ocs: datetime | None = None
    effective_status: str | None
    # 所屬 subnet 是否啟用掃描；前端用來判定「沒掃描的網段不該標離線紅燈」
    subnet_scan_enabled: bool | None = None
    # SSH 連線管理：是否已啟用 + 目前使用者是否可用（後端依權限算好給前端顯示按鈕）
    ssh_enabled: bool = False
    # 虛擬化對應：這個 IP 是虛擬化平台回報的某台 VM →（顯示用）。
    # None＝比對不到，**不代表實體機**（整合可能沒涵蓋），前端不得反向標示。
    virt_vm: dict | None = None
    # 上線判定規則（只在單筆讀取帶）：{minutes, sources}，與 recompute_effective_status 用同一份系統設定。
    # 畫面「各來源最後出現」據此標出每個來源算不算上線證據、是否還在時限內
    liveness_rule: dict | None = None
    # RustDesk Server（開源版）整合：對應到這個 IP 的 RustDesk ID。connect_uri 只給有遠端主控台權限的人
    rustdesk: dict[str, Any] | None = None
    # 這個 IP 沒有 RustDesk 裝置、但同一台裝置的其他 IP 有：[{address_id, ip, rustdesk_id, enabled}]（只列看得到的）
    rustdesk_elsewhere: list[dict[str, Any]] = Field(default_factory=list)
    ssh_available: bool = False
    # SFTP 檔案傳輸：是否已啟用 + 目前使用者是否可用
    sftp_enabled: bool = False
    sftp_available: bool = False
    # RDP 連線管理：是否已啟用 + 目前使用者是否可用
    rdp_enabled: bool = False
    rdp_available: bool = False
    # VNC 連線管理：是否已啟用 + 目前使用者是否可用
    vnc_enabled: bool = False
    vnc_available: bool = False
    # PVE 主控台：是否已啟用 + 目前使用者是否可用 + 對應的 PVE 目標（None＝此 IP 不是 PVE VM/CT）
    novnc_enabled: bool = False
    novnc_available: bool = False
    pve: PveConsoleTarget | None = None
    # BMC OOB主控台（IPMI SOL）：是否已啟用 + 目前使用者是否可用
    bmc_enabled: bool = False
    bmc_available: bool = False
    # RustDesk 連線按鈕是否已啟用（可不可用看 rustdesk.connect_uri 有沒有值）
    rustdesk_enabled: bool = False
    # 連線管理頁：相容 RustDesk 的網頁連線可用（IP 啟用、對應到裝置、伺服器開放網頁連線、使用者有權限）
    rustdesk_web_available: bool = False
    # 後端從 oui_vendors 表 lookup 帶上來；前端不用自己查
    mac_vendor: str | None = None
    # 關聯裝置名稱（清單顯示用，前端不用再查）
    device_name: str | None = None
    # 共用給裝置群組（堆疊／HA；device_id 是群組的代表裝置）
    device_group_id: uuid.UUID | None = None
    device_group_name: str | None = None
    created_at: datetime
    updated_at: datetime
