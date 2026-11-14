"""管理選單「外部系統整合」子樹：每個整合是否已設定（名稱後面的圖示，使用者 2026-10-07）。

選單裡的每個整合都要有一個鍵；漏了的話那一項永遠不會顯示「已設定」。
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from app.models.librenms import LibreNMSInstance

ROOT = Path(__file__).resolve().parents[2]
# 選單 key → 狀態鍵（前端 MainLayout.vue 的 INTEGRATION_PRESENCE_KEY 對照表要跟這裡一致）
MENU_KEYS = {
    "dns", "adguard", "librenms", "opnsense", "pfsense", "fortigate", "paloalto", "checkpoint", "mikrotik", "snmp",
    "windows_dhcp", "kea_dhcp", "isc_dhcp", "isoinsight", "technitium", "rustdesk", "proxmox", "esxi", "hyperv", "openstack", "wazuh", "zabbix", "checkmk", "ocs", "graylog",
}


async def test_every_menu_integration_reports_presence(client, auth_headers, db_session) -> None:
    body = (await client.get("/api/v1/system/integration-presence", headers=auth_headers)).json()
    assert MENU_KEYS <= set(body), MENU_KEYS - set(body)
    assert body["librenms"] is False
    db_session.add(LibreNMSInstance(name=f"nms-{uuid.uuid4().hex[:6]}", api_url="https://192.0.2.5",
                                    api_token_enc=b"x", api_token_nonce=b"y"))
    await db_session.commit()
    body = (await client.get("/api/v1/system/integration-presence", headers=auth_headers)).json()
    assert body["librenms"] is True and body["zabbix"] is False


def test_menu_maps_every_integration_to_a_presence_key() -> None:
    src = (ROOT / "frontend" / "src" / "components" / "layout" / "MainLayout.vue").read_text()
    block = re.search(r"const INTEGRATION_PRESENCE_KEY[^{]*\{(.*?)\};", src, re.S)
    assert block, "MainLayout.vue 要有 INTEGRATION_PRESENCE_KEY 對照表"
    mapped = set(re.findall(r':\s*"([a-z_]+)"', block.group(1)))
    assert mapped == MENU_KEYS, mapped ^ MENU_KEYS
