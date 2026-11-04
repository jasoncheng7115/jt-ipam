"""來源能力矩陣與時效（規格 §4.2）。

每個已設定的整合列出：啟用與否、能力、最後成功同步、錯誤、時效分類。時效門檻是
max(2 × 同步間隔, 15 分鐘)；`last_sync_at` 在部分區段失敗時也會更新，所以 `last_error` 非空＝部分成功。

`not_configured` 表示這類資料沒有納入觀測 —— 不可以當成「沒有依賴」。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# 分析類別 → 會被哪些整合餵資料。ipam、config、cert、physical 是 jt-ipam 自己的資料，一定有
CATEGORIES = ("ipam", "dns", "dhcp", "firewall", "nat", "librenms", "virt", "monitoring", "cert", "config", "physical",
              "network", "workload", "service")
# 全域基礎設施：沒有全域讀取權的帳號看不到（結果標 permission_limited）。
# 服務（M2）是跨單位的共享基礎設施：現有權限模型表達不了跨單位依賴，要全域讀取才分析（規格 §11.1）
GLOBAL_CATEGORIES = frozenset({"dns", "dhcp", "firewall", "nat", "librenms", "virt", "service"})
# 只有管理員看得到的資料
ADMIN_CATEGORIES = frozenset({"monitoring", "cert"})


@dataclass
class SourceStatus:
    kind: str                       # opnsense / dns_server / kea_dhcp …
    id: uuid.UUID
    name: str
    categories: tuple[str, ...]
    last_sync_at: datetime | None
    last_error: str | None
    interval_seconds: int | None
    scope_subnet_ids: list[str] | None
    freshness: str = "unknown"
    capabilities: list[str] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.id}"

    def manifest(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": str(self.id), "name": self.name, "categories": list(self.categories),
                "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
                "has_error": bool(self.last_error), "freshness": self.freshness,
                "interval_seconds": self.interval_seconds, "scoped": bool(self.scope_subnet_ids),
                "capabilities": self.capabilities}


def classify(last_sync_at: datetime | None, last_error: str | None, interval: int | None, now: datetime,
             *, default_stale_hours: int = 24) -> str:
    if last_sync_at is None:
        return "failed" if last_error else "never_synced"
    limit = timedelta(seconds=max(2 * interval, 900)) if interval else timedelta(hours=default_stale_hours)
    if now - last_sync_at > limit:
        return "stale"
    return "partial" if last_error else "fresh"


# (模型路徑, 類別, kind, 分析類別, 間隔欄位, 額外條件與能力)
_SPECS: tuple[tuple[str, str, str, tuple[str, ...], str, tuple[str, ...]], ...] = (
    ("app.models.dns", "DNSServer", "dns_server", ("dns",), "sync_interval_seconds", ("a_aaaa", "ptr")),
    ("app.models.dhcp_standalone", "KeaDhcpServer", "kea_dhcp", ("dhcp",), "sync_interval_seconds",
     ("reservations", "leases", "pools", "ipv4_only")),
    ("app.models.dhcp_standalone", "IscDhcpServer", "isc_dhcp", ("dhcp",), "report_interval_seconds",
     ("reservations", "leases", "pools", "ipv4_only")),
    ("app.models.windows_dhcp", "WindowsDhcpServer", "windows_dhcp", ("dhcp",), "sync_interval_seconds",
     ("reservations", "leases", "pools")),
    ("app.models.firewall", "OPNsenseFirewall", "opnsense", ("firewall", "nat", "dhcp"), "sync_interval_seconds",
     ("rules", "aliases", "nat")),
    ("app.models.pfsense", "PfSenseFirewall", "pfsense", ("firewall", "nat", "dhcp"), "sync_interval_seconds",
     ("rules", "aliases")),
    ("app.models.fortigate", "FortiGateFirewall", "fortigate", ("firewall", "nat", "dhcp"), "sync_interval_seconds",
     ("rules", "address_objects", "vdom")),
    ("app.models.paloalto", "PaloAltoFirewall", "paloalto", ("firewall", "nat", "dhcp"), "sync_interval_seconds",
     ("rules", "address_objects", "vsys")),
    # Check Point：管理伺服器上的存取規則、位址物件、NAT（第一階段）
    ("app.models.checkpoint", "CheckPointServer", "checkpoint", ("firewall", "nat"), "sync_interval_seconds",
     ("rules", "address_objects", "domains")),
    # Check Point 第二階段：閘道的 Gaia API（同一個 kind；共用表的 source_id 是這台閘道的連線設定）。
    # 發放範圍、發給用戶端的閘道/DNS（adapters_net.dhcp）；租約要打開 run-script 才有
    ("app.models.checkpoint_gaia", "CheckPointGaiaTarget", "checkpoint", ("dhcp",), "sync_interval_seconds",
     ("leases", "pools", "scope_options", "ipv4_only")),
    ("app.models.mikrotik", "MikroTikRouter", "mikrotik", ("firewall", "nat", "dhcp"), "sync_interval_seconds",
     ("rules", "address_lists")),
    ("app.models.librenms", "LibreNMSInstance", "librenms", ("librenms", "monitoring"), "sync_interval_seconds",
     ("devices", "arp", "fdb")),
    ("app.models.virt", "ProxmoxInstance", "proxmox", ("virt",), "sync_interval_seconds", ("vms", "interfaces")),
    ("app.models.esxi", "ESXiInstance", "esxi", ("virt",), "sync_interval_seconds", ("vms", "interfaces")),
    ("app.models.hyperv", "HyperVInstance", "hyperv", ("virt",), "sync_interval_seconds", ("vms", "interfaces")),
    # CubeCOS／OpenStack：VM 與網卡寫進共用的虛擬化表（vm_interfaces 已讀）；port 的配置 IP 另存 openstack_ports
    ("app.models.openstack", "OpenStackInstance", "openstack", ("virt",), "sync_interval_seconds",
     ("vms", "interfaces", "ports")),
    ("app.models.zabbix", "ZabbixInstance", "zabbix", ("monitoring",), "sync_interval_seconds", ("hosts",)),
    ("app.models.wazuh", "WazuhInstance", "wazuh", ("monitoring",), "sync_interval_seconds", ("agents",)),
    ("app.models.ocs", "OcsServer", "ocs", ("monitoring",), "sync_interval_seconds", ("inventory",)),
    # ISOinsight：租約有真正的起訖時間，也保留對不到 IP 記錄的租約（services/isoinsight）；IPv6 只存觀察、未驗證
    ("app.models.isoinsight", "IsoInsightSource", "isoinsight", ("dhcp",), "sync_interval_seconds",
     ("leases", "lease_expiry", "unmatched_leases", "ipv4_only")),
    # Technitium DHCP：保留、發放範圍、租約走共用表；另有範圍鏡像給出發給用戶端的閘道／DNS／NTP／WINS（adapters_net.dhcp）
    ("app.models.technitium", "TechnitiumDhcpServer", "technitium", ("dhcp",), "sync_interval_seconds",
     ("reservations", "leases", "pools", "scope_options", "ipv4_only")),
    # AdGuard：DNS 改寫與用戶端設定（從主機名稱觀測得知，adapters_net._adguard）
    ("app.models.adguard", "AdGuardInstance", "adguard", ("dns",), "sync_interval_seconds", ("rewrites", "clients")),
    # RustDesk：受控端的登記（adapters_net.monitoring）；同步狀態要納入資料不足的判斷
    ("app.models.rustdesk", "RustDeskServer", "rustdesk", ("monitoring",), "report_interval_seconds", ("peers",)),
)
# 沒有 last_sync_at 欄位的整合：用哪一欄當「最近一次成功同步」
_LAST_SYNC_ATTR = {"isoinsight": "last_commit_at", "rustdesk": "last_report_at"}

# 整合清單（/system/integration-presence 的鍵，跟管理選單「外部系統整合」同一份）→ 評估的來源種類。
# 新增整合時：能提供引用的就加進 _SPECS 並在 adapter 讀它；不適用就寫進 NOT_SOURCES 並附理由。
# tests/test_change_impact_coverage.py 會比對，漏了就擋（使用者 2026-10-08：新整合都要考慮評估）。
PRESENCE_KIND = {
    "dns": "dns_server", "adguard": "adguard", "librenms": "librenms", "opnsense": "opnsense", "pfsense": "pfsense",
    "fortigate": "fortigate", "paloalto": "paloalto", "mikrotik": "mikrotik", "windows_dhcp": "windows_dhcp",
    "kea_dhcp": "kea_dhcp", "isc_dhcp": "isc_dhcp", "isoinsight": "isoinsight", "rustdesk": "rustdesk",
    "technitium": "technitium", "checkpoint": "checkpoint",
    "proxmox": "proxmox", "esxi": "esxi", "hyperv": "hyperv", "openstack": "openstack", "wazuh": "wazuh", "zabbix": "zabbix", "ocs": "ocs",
}
NOT_SOURCES = {
    "graylog": "只把 IPAM 的資料匯出給 Graylog 查表，本身不存任何引用；改址後匯出內容自動跟著變",
    # SNMP 收集（0217）：讀到的 MAC 表寫進共用的 fdb_entries（交換器維護評估本來就讀，不分來源）；管理位址覆寫列在
    # adapters_ipam._INTEGRATION_URLS（連線位址）；LLDP/CDP 鄰居是觀察值。本身沒有另外的引用資料
    "snmp": "MAC 表寫進共用的 fdb_entries（交換器維護評估已讀）、管理位址覆寫已列為連線位址；鄰居是觀察值",
}


async def collect_sources(session: AsyncSession, *, now: datetime | None = None,
                          default_stale_hours: int = 24) -> list[SourceStatus]:
    """所有「已啟用」的整合與其時效。停用的整合不算來源（也不算缺口）。"""
    import importlib
    now = now or datetime.now(UTC)
    out: list[SourceStatus] = []
    for mod, cls, kind, cats, interval_attr, caps in _SPECS:
        model = getattr(importlib.import_module(mod), cls)
        rows = (await session.execute(select(model).where(model.enabled.is_(True)))).scalars().all()
        for r in rows:
            interval = getattr(r, interval_attr, None)
            scope = getattr(r, "scope_subnet_ids", None)
            # 防火牆的 DHCP／NAT 要另外開，沒開的那一類不算這台的能力
            eff = tuple(c for c in cats if not (
                (c == "dhcp" and kind in ("opnsense", "pfsense", "fortigate", "paloalto", "mikrotik", "checkpoint")
                 and not getattr(r, "sync_dhcp", False))
                or (c == "nat" and kind in ("opnsense", "fortigate", "paloalto", "mikrotik")
                    and not getattr(r, "sync_nat", False))
                # pfSense 的 NAT port forward 跟著「同步規則」一起同步，沒有獨立開關（2026-11-04）
                or (c == "nat" and kind == "pfsense" and not getattr(r, "sync_rules", False))))
            st = SourceStatus(kind=kind, id=r.id, name=str(getattr(r, "name", "") or kind), categories=eff,
                              last_sync_at=getattr(r, _LAST_SYNC_ATTR.get(kind, "last_sync_at")),
                              last_error=getattr(r, "last_error", None),
                              interval_seconds=int(interval) if interval else None,
                              scope_subnet_ids=[str(x) for x in scope] if scope else None,
                              capabilities=list(caps))
            st.freshness = classify(st.last_sync_at, st.last_error, st.interval_seconds, now,
                                    default_stale_hours=default_stale_hours)
            out.append(st)
    return out


def by_category(sources: list[SourceStatus]) -> dict[str, list[SourceStatus]]:
    out: dict[str, list[SourceStatus]] = {c: [] for c in CATEGORIES}
    for s in sources:
        for c in s.categories:
            out.setdefault(c, []).append(s)
    return out


def watermarks(sources: list[SourceStatus]) -> dict[str, Any]:
    """來源水位：核准之後任一來源同步出新資料，就要求重跑（規格 §9）。"""
    return {s.ref: s.last_sync_at.isoformat() if s.last_sync_at else None for s in sources}
