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
    "oui",            # IEEE OUI 廠商庫（大、可重新產生）
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
    "ip_addresses": "core",
    "devices": "core",
    "nat_translations": "core",
    "custom_field_definitions": "core",
    # core — DNS（伺服器定義屬設定，但記錄/區域是核心資料，一起放 core 較單純）
    "dns_servers": "core",
    "dns_zones": "core",
    "dns_records": "core",
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
    "scan_agents": "integrations",
    "cert_agents": "integrations",
    "webhook_subscriptions": "integrations",
    "opnsense_alias_mappings": "integrations",
    "ssh_credentials": "integrations",
    # synced（可重新拉取的鏡像）
    "librenms_devices": "synced",
    "arp_entries": "synced",
    "fdb_entries": "synced",
    "device_vlans": "synced",
    "opnsense_rules": "synced",
    "opnsense_synced_aliases": "synced",
    "opnsense_rule_labels": "synced",
    "pfsense_synced_aliases": "synced",
    "wazuh_agents": "synced",
    "ip_hostname_observations": "synced",
    "virtual_machines": "synced",
    "vm_interfaces": "synced",
    "dhcp_pool_ranges": "synced",
    # operational（短暫／歷史）
    "audit_logs": "operational",
    "ip_change_log": "operational",
    "ip_requests": "operational",
    "ip_request_events": "operational",
    "ip_request_stage_approvals": "operational",
    "background_tasks": "operational",
    "notifications": "operational",
    "ai_chat_conversations": "operational",
    "ai_chat_messages": "operational",
    "phpipam_migration_mapping": "operational",
    # oui
    "oui_vendors": "oui",
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
