"""發現主體的顯示名稱（使用者 2026-10-08：「proxmox proxmox」沒人看得懂）。

名稱存在資料庫裡、各語系共用，所以只放產品名稱與物件名稱這種不用翻譯的字；
「是什麼」由節點圖示與類型標籤講，「為什麼」由原因文字講。
"""
from __future__ import annotations

from typing import Any

#: 整合類型 → 產品名稱
PRODUCT: dict[str, str] = {
    "librenms": "LibreNMS", "opnsense": "OPNsense", "pfsense": "pfSense", "fortigate": "FortiGate",
    "paloalto": "Palo Alto", "checkpoint": "Check Point", "mikrotik": "MikroTik", "proxmox": "Proxmox VE",
    "esxi": "VMware ESXi", "cert_push": "憑證推送", "hyperv": "Hyper-V", "openstack": "OpenStack", "zabbix": "Zabbix", "checkmk": "Checkmk", "wazuh": "Wazuh", "dns_server": "DNS", "kea_dhcp": "Kea DHCP",
    "isc_dhcp": "ISC DHCP", "technitium": "Technitium", "windows_dhcp": "Windows DHCP", "ocs": "OCS Inventory",
    "adguard": "AdGuard Home", "isoinsight": "ISOinsight", "rustdesk": "RustDesk", "scan_agent": "掃描代理",
    "cert_agent": "憑證代理", "webhook": "Webhook", "snmp": "SNMP",
}


def integration_label(kind: str, name: str | None, host: str | None = None) -> str:
    """「產品 名稱」；名稱只是重複產品或類型代碼（例如 Proxmox 沒有名稱欄位、主機就叫 proxmox）時改用主機，
    主機也沒有就只寫產品。"""
    product = PRODUCT.get(kind, kind)
    for cand in (name, host):
        c = (cand or "").strip()
        if c and c.lower() not in (kind.lower(), product.lower()):
            return f"{product} {c}"
    return product


def display_label(subject_type: str, label: str, params: dict[str, Any] | None) -> str:
    """讀取時的整理：舊的評估結果把整合存成「類型代碼 名稱」（例：proxmox proxmox），換成產品名稱；
    OCS 電腦存成「OCS #6」（畫面會再加「OCS：」來源）→ 去掉重複的前綴。"""
    if subject_type == "ocs_computer" and label.startswith("OCS #"):
        return label[4:]
    if subject_type != "integration":
        return label
    kind = str((params or {}).get("kind") or "")
    if not kind:
        return label
    product = PRODUCT.get(kind, kind)
    if label.startswith(product):
        return label
    rest = label[len(kind) + 1:] if label.lower().startswith(kind.lower() + " ") else label
    return integration_label(kind, rest)
