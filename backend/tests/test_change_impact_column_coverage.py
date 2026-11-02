"""守門：每個存位址、主機或網址的欄位，IP 變更評估都要讀，或寫明為什麼不用讀。

使用者 2026-10-08：「以後只要有新功能、新整合，都要同步考慮 IP 變更分析功能必須也被支援」。
整合有 test_change_impact_coverage.py 守著選單；這支守的是資料表：新增一個存 IP／主機／網址的欄位
（INET/CIDR 型別，或名稱看起來是位址的文字欄位），沒有加進 COVERED 或 EXEMPT 這支就會失敗，
逼著寫功能的人當下決定「改址、除役時這裡要不要列出來」。

COVERED 不是自己說了算：表名或模型類別名稱、欄位名稱都要真的出現在 services/change_impact 的程式裡。
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import CIDR, INET

_NAME = re.compile(r"(url|host|address|server|endpoint|^ip$|_ip$|^ip_|gateway|router)", re.I)
# 名稱像位址、其實不是：MAC、金鑰、指紋、版本、類型、標籤、外部系統的編號
_NOISE = re.compile(r"(mac|_key$|fingerprint|version|_type$|label|hostid|_id$)", re.I)

# 評估有讀的欄位（改址、除役、維護時會列出來）
COVERED: set[tuple[str, str]] = {
    # IPAM 本身
    ("ip_addresses", "ip"), ("ip_addresses", "hostname"),
    ("subnets", "cidr"), ("subnets", "gateway"), ("subnets", "dns_servers"),
    ("ip_cooldowns", "ip"), ("ip_cooldowns", "previous_hostname"),
    ("ip_ranges", "start_ip"), ("ip_ranges", "end_ip"),
    ("ip_requests", "requested_ip"), ("ip_requests", "hostname"),
    ("unmanaged_sightings", "ip"), ("unmanaged_sightings", "hostname"),
    ("arp_entries", "ip"),
    ("ip_hostname_observations", "hostname"),
    # 進階資源與設定
    ("circuits", "ip_address"), ("circuits", "gateway"), ("circuits", "dns_servers"),
    ("vpn_tunnels", "a_endpoint"), ("vpn_tunnels", "b_endpoint"),
    ("jump_hosts", "host"),
    ("impact_service_endpoints", "hostname"),
    ("webhook_subscriptions", "target_url"),
    ("scan_agents", "agent_url"),
    # DHCP
    ("dhcp_reservations", "ip"), ("dhcp_reservations", "hostname"),
    ("dhcp_pool_ranges", "start_ip"), ("dhcp_pool_ranges", "end_ip"),
    ("dhcp_sightings", "server_ip"), ("dhcp_sightings", "router"),
    ("isoinsight_leases", "ip"),
    # Technitium 範圍發給用戶端的選項（adapters_net.dhcp 的 scope_options）
    ("technitium_dhcp_scopes", "router"), ("technitium_dhcp_scopes", "server_address"),
    # 防火牆（MikroTik 的位址清單與規則；其他廠牌的規則欄位名稱不像位址，由 adapters_net 讀）
    ("mikrotik_address_lists", "address"),
    ("mikrotik_rules", "src_address"), ("mikrotik_rules", "dst_address"), ("mikrotik_rules", "to_addresses"),
    # 監控與資產
    ("librenms_devices", "hostname"), ("librenms_devices", "primary_ip"),
    ("zabbix_hosts", "host"), ("zabbix_hosts", "ip"),
    ("wazuh_agents", "ip"), ("wazuh_agents", "register_ip"),
    ("rustdesk_peers", "hostname"),
    ("vm_interfaces", "primary_ip"),
    # 各整合的連線位址（adapters_ipam._INTEGRATION_URLS）
    ("adguard_instances", "api_url"), ("dns_servers", "api_url"), ("dns_servers", "server_address"),
    ("esxi_instances", "api_url"), ("esxi_instances", "extra_api_urls"),
    ("hyperv_instances", "host"), ("hyperv_instances", "extra_hosts"),
    ("openstack_instances", "auth_url"), ("openstack_instances", "cubecos_url"),
    ("proxmox_instances", "api_url"), ("proxmox_instances", "extra_api_urls"),
    ("fortigate_firewalls", "api_url"), ("opnsense_firewalls", "api_url"), ("paloalto_firewalls", "api_url"),
    ("pfsense_firewalls", "api_url"), ("mikrotik_routers", "api_url"),
    ("isoinsight_sources", "base_url"), ("kea_dhcp_servers", "api_url"), ("windows_dhcp_servers", "host"),
    ("technitium_dhcp_servers", "api_url"),
    ("cert_push_targets", "api_url"), ("cert_push_targets", "verify_endpoint"),
    # Check Point：管理伺服器網址與它管的閘道位址（閘道才是發 DHCP、做 NAT 的那台）
    ("checkpoint_servers", "api_url"), ("checkpoint_gateways", "ipv4_address"),
    # Check Point 第二階段：閘道 Gaia API 的網址、閘道 DHCP 發給用戶端的預設閘道（adapters_net.dhcp）
    ("checkpoint_gaia_targets", "gaia_url"), ("checkpoint_dhcp_subnets", "default_gateway"),
    ("librenms_instances", "api_url"), ("zabbix_instances", "api_url"), ("wazuh_instances", "api_url"),
    ("ocs_servers", "base_url"), ("ocs_servers", "db_host"),
    ("rustdesk_servers", "client_address"), ("rustdesk_servers", "hbbs_host"), ("rustdesk_servers", "relay_host"),
    # SNMP 收集目標的管理位址覆寫（0217，代理連這個位址讀設備）
    ("snmp_targets", "ip_override"),
}

# 不用讀的欄位與理由
EXEMPT: dict[tuple[str, str], str] = {
    ("api_tokens", "last_used_ip"): "歷史：API 呼叫者上次的來源位址",
    ("audit_logs", "actor_ip"): "歷史：稽核記錄不可改",
    ("integration_own_addresses", "address"): "鏡像：整合回報的設備自身介面位址，每輪同步重抓；整合本身的連線位址已在評估內",
    ("users", "last_login_ip"): "歷史：使用者上次登入的來源位址",
    ("user_sessions", "ip"): "歷史：登入工作階段的來源位址（使用者從哪裡登入，不是網路上的設備或服務）",
    ("ip_change_log", "ip_text"): "歷史：IP 異動記錄",
    ("anomaly_observations", "ip"): "歷史：異常偵測每一筆出現到消失的期間（戰情室時序用），不是設定裡的引用",
    ("rustdesk_audit_events", "ip"): "歷史：RustDesk 稽核事件",
    ("rustdesk_audit_events", "src_ip"): "歷史：RustDesk 稽核事件",
    ("cert_agents", "last_source_ip"): "觀察值：代理下次回報自己更新",
    ("scan_agents", "last_source_ip"): "觀察值：代理下次回報自己更新",
    ("rustdesk_servers", "agent_source_ip"): "觀察值：代理下次回報自己更新",
    ("rustdesk_servers", "agent_hostname"): "觀察值：代理回報的主機名稱",
    ("mikrotik_neighbors", "address"): "觀察值：鄰居探索結果，每次同步重抓",
    ("snmp_neighbors", "remote_mgmt_address"): "觀察值：LLDP/CDP 鄰居宣告的管理位址，每次收集重抓（維護評估走佈線與 FDB）",
    ("ip_addresses", "device_evidence_ip"): "觀察值：「上線狀態跟著設備」的依據，每輪同步由同設備的位址重算（services/device_liveness）",
    ("dhcp_exclusions", "start_ip"): "排除區間：發放範圍寫進 dhcp_pool_ranges 前已扣掉，評估讀那一份；排除的位址不會發給任何人",
    ("dhcp_exclusions", "end_ip"): "排除區間：發放範圍寫進 dhcp_pool_ranges 前已扣掉，評估讀那一份；排除的位址不會發給任何人",
    ("technitium_dhcp_scopes", "start_ip"): "鏡像：發放範圍另外寫進 dhcp_pool_ranges（扣掉排除區間），評估讀那一份",
    ("technitium_dhcp_scopes", "end_ip"): "鏡像：發放範圍另外寫進 dhcp_pool_ranges（扣掉排除區間），評估讀那一份",
    ("rustdesk_peers", "registered_ip"): "觀察值：客戶端下次回報自己更新（評估以對應的 IP 記錄找它）",
    ("rustdesk_peers", "report_ip"): "觀察值：客戶端下次回報自己更新（評估以對應的 IP 記錄找它）",
    ("librenms_links", "remote_hostname"): "觀察值：LLDP/CDP 鄰居的名稱，不是位址引用（維護評估走佈線與 FDB）",
    ("dhcp_sightings", "offered_ip"): "探測時 DHCP 發給探測封包自己的位址，用完即放",
    ("ip_hostname_reports", "hostname"): "回報給某筆 IP 記錄的主機名稱，跟著記錄走（以 ip_address_id 關聯）",
    ("ip_addresses", "hostname_source_pin"): "主機名稱來源的釘選設定（來源名稱），不是位址",
    ("checkpoint_gaia_targets", "gateway_uid"): "Check Point 閘道物件的 uid（對照第一階段同步回來的閘道），不是位址",
    ("openstack_subnets", "cidr"): "鏡像：Neutron 子網路，每輪同步重抓；對應到的 jt-ipam 子網路（mapped_subnet_id）本身已在評估內",
    ("openstack_subnets", "gateway_ip"): "鏡像：Neutron 子網路的閘道（虛擬路由器的介面），每輪同步重抓；不是 jt-ipam 設定裡的引用",
    ("openstack_ports", "binding_host"): "觀察值：Neutron port binding 的運算節點名稱，每輪同步重抓（VM 的位址走 vm_interfaces）",
    ("contacts", "address"): "郵寄地址",
    ("customers", "address"): "郵寄地址",
    ("locations", "address"): "郵寄地址",
    ("providers", "portal_url"): "電信業者的客戶入口網站，不在管理的網路內",
    ("domains", "registrar_url"): "網域註冊商的管理網站，不在管理的網路內",
    ("maintenance_scopes", "range_start"): "維護排程的 IP 範圍：暫時性的靜音規則，到期就失效；改址不需要跟著改",
    ("maintenance_scopes", "range_end"): "同上",
}


def _models() -> list[type]:
    import app.models as m
    for mi in pkgutil.iter_modules(m.__path__):
        importlib.import_module(f"app.models.{mi.name}")
    from app.models.base import Base
    return [mp.class_ for mp in Base.registry.mappers]


def _address_columns() -> dict[tuple[str, str], str]:
    """(表, 欄) → 模型類別名稱。"""
    out: dict[tuple[str, str], str] = {}
    for cls in _models():
        t = cls.__table__
        for c in t.columns:
            if c.foreign_keys:
                continue
            if isinstance(c.type, (INET, CIDR)) or (
                    isinstance(c.type, (String, Text)) and _NAME.search(c.name) and not _NOISE.search(c.name)):
                out[(t.name, c.name)] = cls.__name__
    return out


def _impact_source() -> str:
    root = Path(__file__).resolve().parents[1] / "app" / "services" / "change_impact"
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(root.glob("*.py")))


def test_every_address_column_is_read_or_exempt() -> None:
    cols = _address_columns()
    assert len(cols) > 50, "一個欄位都找不到 → 掃描本身壞了"
    missing = sorted(set(cols) - COVERED - set(EXEMPT))
    assert not missing, (
        "這些欄位存了位址／主機／網址，IP 變更評估沒有讀、也沒有寫明理由。"
        "請在 services/change_impact 讀它（改址、除役、維護時列出來），加進 COVERED；"
        f"或加進 EXEMPT 並寫理由：{missing}")


def test_lists_have_no_stale_entries() -> None:
    cols = _address_columns()
    stale = sorted((COVERED | set(EXEMPT)) - set(cols))
    assert not stale, f"這些欄位已經不存在（或不再像位址），請從清單移除：{stale}"
    both = sorted(COVERED & set(EXEMPT))
    assert not both, f"同一個欄位不可以同時算有讀又豁免：{both}"


def test_covered_columns_are_really_read() -> None:
    src = _impact_source()
    cols = _address_columns()
    fake = []
    for (table, col) in sorted(COVERED):
        cls = cols.get((table, col), "")
        if not ((table in src or (cls and cls in src)) and col in src):
            fake.append((table, col))
    assert not fake, f"清單說有讀，但 services/change_impact 裡找不到表名（或類別名）與欄位名：{fake}"
