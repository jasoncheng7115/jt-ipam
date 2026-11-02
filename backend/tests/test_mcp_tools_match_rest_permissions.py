"""AI 工具的權限不可以比對應的 REST 端點寬（2026-09-30 盤點 API 手冊時發現）。

REST 端點收成管理員專用時，對應的 AI／MCP 工具常常沒跟著收 —— 具「全域讀取」的帳號從網頁打不開
Wazuh 代理清單、OCS 電腦、掃描代理、憑證，卻能在 AI 對話裡直接問到（v0.5.137 巡檢收成 admin 時
就漏過一次）。這裡把工具和它讀的 REST 端點綁在一起，REST 是 admin 的，工具也要是 admin。
"""
from __future__ import annotations

import pytest

#: 工具 → 它讀的資料在 REST 上的清單端點（GET）
TOOL_REST = {
    "impact_list_plans": "/api/v1/change-plans",
    "impact_get_run": "/api/v1/impact-runs/{run_id}",
    "impact_list_findings": "/api/v1/impact-runs/{run_id}/findings",
    "impact_get_evidence": "/api/v1/impact-runs/{run_id}/evidence/{evidence_id}",
    "list_scan_agents": "/api/v1/scan-agents",
    "list_certificates": "/api/v1/certificates",
    "list_domains": "/api/v1/domains",
    "list_maintenance": "/api/v1/maintenance",
    "list_cert_distribution": "/api/v1/cert-agents",
    "list_wazuh_agents": "/api/v1/wazuh/agents",
    "wazuh_missing_agents": "/api/v1/wazuh/missing-agents",
    "list_ocs_computers": "/api/v1/ocs/agents",
    "list_rustdesk_peers": "/api/v1/rustdesk/servers",
    "list_rustdesk_audit": "/api/v1/rustdesk/servers",
    "list_isoinsight_leases": "/api/v1/isoinsight/leases",
    "list_dns_records": "/api/v1/dns/records",
    "list_vms": "/api/v1/virt/vms",
    "list_arp": "/api/v1/librenms/arp",
    "list_fdb": "/api/v1/librenms/fdb",
    "list_nat": "/api/v1/nat",
    "list_vpn_tunnels": "/api/v1/vpn-tunnels",
    "list_dhcp_ranges": "/api/v1/dhcp-ranges",
    "list_attack_surface": "/api/v1/anomalies/attack-surface",
    "list_anomalies": "/api/v1/anomalies/scan",
    "list_ai_findings": "/api/v1/ai-audit/findings",
    # 依物件授權的兩支（2026-10-15）：工具內部再依子網路／交換器可見性逐筆遮蔽，與 REST 相同
    "switch_port_for_ip": "/api/v1/addresses/{address_id}/switch-port",
    "list_connection_targets": "/api/v1/addresses/connections/targets",
}


def _rest_level(path: str) -> str:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from app.main import app
    from route_walk import iter_routes

    def deps(d, acc):
        for x in d.dependencies:
            acc.add(getattr(x.call, "__name__", "")); deps(x, acc)
        return acc
    for p, r in iter_routes(app):
        if p == path and ("GET" in (getattr(r, "methods", None) or set()) or path.endswith("/scan")):
            s = deps(r.dependant, set())
            return "admin" if "require_admin" in s else ("global" if any("global" in x for x in s) else "object")
    raise AssertionError(f"找不到 REST 端點 {path}")


@pytest.mark.parametrize(("tool", "path"), sorted(TOOL_REST.items()))
def test_tool_is_not_looser_than_its_rest_endpoint(tool, path) -> None:
    from app.mcp.tools import ADMIN_TOOLS, GLOBAL_READ_TOOLS, TOOLS
    assert tool in TOOLS
    rest = _rest_level(path)
    tier = "admin" if tool in ADMIN_TOOLS else ("global" if tool in GLOBAL_READ_TOOLS else "object")
    rank = {"object": 0, "global": 1, "admin": 2}
    assert rank[tier] >= rank[rest], f"{tool} 是 {tier}，但 {path} 要 {rest}"
