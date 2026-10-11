"""匯出表清單 —— 相依序 + 分類。

用 SQLAlchemy metadata 的 `sorted_tables`（外鍵相依序，父表在前）當基準順序，匯出照此順序、
匯入也照此順序（replace 模式清空則反序）。分類讓使用者在匯出時勾選要帶哪些資料。

每張表都必須在 `CATEGORY` 裡有分類 —— 少一張會在 import 時 raise（見 `validate_registry`），
確保未來新增資料表不會被默默漏掉。
"""

from __future__ import annotations

import app.models  # noqa: F401 —— 觸發所有 model 註冊進 metadata
from app.models.base import Base

# 使用者可勾選的匯出分類
SCOPES: tuple[str, ...] = (
    "settings",       # 系統設定 + 整合設定（system_settings；含加密機密）
    "users_rbac",     # 使用者 / 群組 / 權限 / API token / 偏好
    "core",           # 核心 IPAM 資料：客戶 / 網段 / IP / 裝置 / 實體層 / 進階資源 / 憑證
    "integrations",   # 整合連線設定（含加密金鑰）：LibreNMS/OPNsense/pfSense/Proxmox/Wazuh/AdGuard/掃描代理/憑證代理/SSH 憑證/Webhook
    "synced",         # 由整合拉回、可重新同步的鏡像資料：ARP/FDB/同步別名/規則/VM/hostname 觀測…
    "operational",    # 短暫／歷史資料：稽核記錄 / IP 異動 / 申請 / 背景作業 / 通知 / AI 對話
    "oui",            # 參考資料庫（大、可重新下載）：IEEE OUI 廠商庫、Recog 指紋庫
)

DEFAULT_SCOPE: tuple[str, ...] = ("settings", "users_rbac", "core", "integrations")

# 中央機密表：任一「可能擁有機密」的分類被選時就一起帶（否則機密會遺失）
_SECRET_OWNING_SCOPES = frozenset({"settings", "core", "integrations"})
ENCRYPTED_SECRETS_TABLE = "encrypted_secrets"

# 每張資料表 → 分類。涵蓋 metadata 內全部資料表。
CATEGORY: dict[str, str] = {
    # settings
    "system_settings": "settings",
    # users_rbac
    "users": "users_rbac",
    "groups": "users_rbac",
    "user_group_members": "users_rbac",
    "api_tokens": "users_rbac",
    # 復原碼只存 argon2 雜湊，搬到別台照樣能用
    "user_recovery_codes": "users_rbac",
    "user_preferences": "users_rbac",
    "permissions": "users_rbac",
    # core — IPAM
    "customers": "core",
    "locations": "core",
    "racks": "core",
    "sections": "core",
    "vrfs": "core",
    "vlan_domains": "core",
    "vlans": "core",
    "subnets": "core",
    # 子網路內的位址範圍（集區，issue #40）：使用者自己定義的資料，跟著子網路搬
    "ip_ranges": "core",
    "ip_addresses": "core",
    "devices": "core",
    "nat_translations": "core",
    "custom_field_definitions": "core",
    # core — DNS（伺服器定義屬設定，但記錄/區域是核心資料，一起放 core 較單純）
    "dns_servers": "core",
    "dns_zones": "core",
    "dns_records": "core",
    "dns_compare_groups": "core",          # 比對群組設定（成員是 dns_servers.compare_group_id）
    "dns_compare_group_diffs": "synced",   # 比對結果：下一輪同步就重算
    # core — 憑證集中保管
    "certificates": "core",
    "cert_versions": "core",
    # core — 實體層
    "cables": "core",
    "cable_terminations": "core",
    "device_ports": "core",
    "device_power_ports": "core",
    "power_panels": "core",
    "power_feeds": "core",
    "power_outlets": "core",
    "vpn_tunnels": "core",
    "virt_clusters": "core",
    # core — 進階資源
    "tenant_groups": "core",
    "tenants": "core",
    "contact_groups": "core",
    "contact_roles": "core",
    "contacts": "core",
    "contact_assignments": "core",
    "providers": "core",
    "circuit_types": "core",
    "circuits": "core",
    "asns": "core",
    "wireless_ssids": "core",
    "wireless_links": "core",
    # integrations（連線設定，含加密金鑰）
    "adguard_instances": "integrations",
    "librenms_instances": "integrations",
    "pfsense_firewalls": "integrations",
    "wazuh_instances": "integrations",
    "opnsense_firewalls": "integrations",
    "proxmox_instances": "integrations",
    # PVE 防火牆：整合拉回、可重新同步的鏡像資料（與 ARP/FDB/規則同性質）
    "pve_firewall_rules": "synced",
    "pve_firewall_state": "synced",
    "pve_firewall_groups": "synced",
    "pve_firewall_ipsets": "synced",
    "scan_agents": "integrations",
    "zabbix_instances": "integrations",
    "zabbix_hosts": "synced",
    # 探測工作是執行紀錄（工具頁的兩分鐘就過期；IP 探測的歷次結果也屬歷史），與稽核／背景作業同歸短暫資料
    "agent_probe_jobs": "operational",
    # 掃描代理每一輪的耗時（負載面板的趨勢，只保留 7 天）
    "scan_agent_cycles": "operational",
    "cert_agents": "integrations",
    "webhook_subscriptions": "integrations",
    "windows_dhcp_servers": "integrations",
    "kea_dhcp_servers": "integrations",
    "isc_dhcp_servers": "integrations",
    # Technitium DHCP：設定跟著整合走；範圍鏡像可以重新拉
    "technitium_dhcp_servers": "integrations",
    "technitium_dhcp_scopes": "synced",
    # ISOinsight 整合：來源設定跟著整合走；來源租約觀察是可重新拉取的鏡像；同步記錄是歷史
    "isoinsight_sources": "integrations",
    "isoinsight_leases": "synced",
    "isoinsight_sync_runs": "operational",
    "rustdesk_servers": "integrations",
    "fortigate_firewalls": "integrations",
    "paloalto_firewalls": "integrations",
    # Check Point：管理伺服器設定跟著整合走；閘道／物件／規則是可重新拉的鏡像
    "checkpoint_servers": "integrations",
    # 第二階段：每台閘道的 Gaia API 連線跟著整合走；DHCP 子網路鏡像可以重新拉
    "checkpoint_gaia_targets": "integrations",
    "mikrotik_routers": "integrations",
    "ocs_servers": "integrations",
    "opnsense_alias_mappings": "integrations",
    "ssh_credentials": "integrations",
    # 跳板主機（issue #24）：算基礎設施設定，跟著整合一起搬
    "jump_hosts": "integrations",
    # synced（可重新拉取的鏡像）
    "librenms_devices": "synced",
    # RustDesk 裝置清單：代理下一輪回報就重建；last_online_at（我們自己記的）搬不過去也只是要重新看到一次上線
    "rustdesk_peers": "synced",
    "librenms_links": "synced",
    "arp_entries": "synced",
    # 沒有納管、但看得到在用的位址：掃描代理下一輪就重建
    "unmanaged_sightings": "synced",
    "fdb_entries": "synced",
    "device_vlans": "synced",
    "opnsense_rules": "synced",
    "fw_rule_snapshots": "synced",
    "opnsense_synced_aliases": "synced",
    "opnsense_rule_labels": "synced",
    "pfsense_synced_aliases": "synced",
    "fortigate_policies": "synced",
    "fortigate_address_objects": "synced",
    "paloalto_policies": "synced",
    "paloalto_address_objects": "synced",
    "checkpoint_gateways": "synced",
    "checkpoint_objects": "synced",
    "checkpoint_rules": "synced",
    "checkpoint_dhcp_subnets": "synced",
    "mikrotik_rules": "synced",
    "mikrotik_neighbors": "synced",
    "mikrotik_address_lists": "synced",
    "wazuh_agents": "synced",
    "ip_hostname_observations": "synced",
    "ip_hostname_reports": "synced",       # 逐來源實例的主機名稱目擊（觀測由它推導）
    "virtual_machines": "synced",
    "vm_interfaces": "synced",
    "dhcp_pool_ranges": "synced",
    "dhcp_reservations": "synced",
    "dhcp_lease_sightings": "synced",      # 逐來源的 DHCP 租約目擊（in_dhcp_lease 由它推導）
    "esxi_instances": "integrations",
    # operational（短暫／歷史）
    "audit_logs": "operational",
    # 登入工作階段：搬到別台沒有意義（Cookie 屬於原本的網址），只在選了「營運資料」時才帶
    "user_sessions": "operational",
    # IP 變更評估（0187）：計畫、每次分析的快照、待辦、覆核、AI 產出 —— 歷史資料
    "change_plans": "operational",
    "change_plan_revisions": "operational",
    "impact_runs": "operational",
    "impact_evidence": "operational",
    "impact_relations": "operational",
    "impact_findings": "operational",
    "impact_gaps": "operational",
    "change_tasks": "operational",
    "impact_reviews": "operational",
    "impact_ai_artifacts": "operational",
    # IP 變更評估 M2（0189）：人工登錄的服務與依賴 —— 跟網段、裝置一樣是要搬的核心資料（引用物件時保留型別＋id）
    "impact_services": "core",
    "impact_service_endpoints": "core",
    "impact_dependency_groups": "core",
    "impact_dependency_members": "core",
    # RustDesk 客戶端回報的連線／檔案／告警稽核：與 jt-ipam 自己的稽核記錄同性質
    "rustdesk_audit_events": "operational",
    # RustDesk「刪除舊註冊」的請求與結果：執行紀錄（等待中的過一天就逾時），不是要跟著搬的設定
    "rustdesk_peer_deletes": "operational",
    "ip_change_log": "operational",
    # 逐日存活觀測：可重建的運維資料，不隨設定搬移
    "ip_liveness_days": "operational",
    "ip_cooldowns": "operational",
    "event_rules": "settings",
    "ip_requests": "operational",
    "ip_request_events": "operational",
    "ip_request_stage_approvals": "operational",
    "background_tasks": "operational",
    "notifications": "operational",
    "ai_chat_conversations": "operational",
    "ai_chat_messages": "operational",
    # 巡檢發現是對「某一刻的資料」下的推測，搬到另一台機器未必還成立 —— 屬歷史紀錄，
    # 不是要跟著搬的設定
    "ai_findings": "operational",
    # DHCP 觀測是「某個時刻在那個網路上看到的事」，換一台機器就不成立
    "dhcp_sightings": "operational",
    "phpipam_migration_mapping": "operational",
    # oui（參考資料庫）
    "oui_vendors": "oui",
    # Recog 指紋庫：每一列是一整個指紋檔，合併匯入時整檔覆蓋，不會出現兩版混在一起
    "recog_databases": "oui",
    # 中央機密（特別處理；分類僅供 validate 檢查完整性）
    ENCRYPTED_SECRETS_TABLE: "_secrets",
}


def all_tablenames() -> list[str]:
    """metadata 內全部資料表名（相依序）。"""
    return [t.name for t in Base.metadata.sorted_tables]


def validate_registry() -> list[str]:
    """回傳 metadata 內尚未分類的資料表名（應為空）。CI / 測試會斷言為空。"""
    known = set(CATEGORY)
    return [name for name in all_tablenames() if name not in known]


def tables_for_scope(scope: list[str] | tuple[str, ...]) -> list[str]:
    """依所選分類，回相依序的資料表名清單（含中央機密表，若適用）。"""
    picked = set(scope)
    out: list[str] = []
    for t in Base.metadata.sorted_tables:
        name = t.name
        if name == ENCRYPTED_SECRETS_TABLE:
            if picked & _SECRET_OWNING_SCOPES:
                out.append(name)
            continue
        cat = CATEGORY.get(name)
        if cat in picked:
            out.append(name)
    return out


def table_by_name(name: str):  # -> sqlalchemy.Table
    return Base.metadata.tables[name]
