"""系統診斷的「資料統計」：各類資料目前各有幾筆（使用者 2026-10-06，比照 LibreNMS 的統計頁）。

只在本機計算、只顯示給管理員看，不回傳、不蒐集到任何地方。

大表（稽核、IP 異動、ARP/FDB）在超大規模站台上 count(*) 可能要好幾秒：每個計數各自限時，
逾時就改用 PostgreSQL 的統計估計值並標成「約」，不讓整頁卡住。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import Table, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

Cond = Callable[[Table], ColumnElement[bool]] | None


def _v4(t: Table) -> ColumnElement[bool]:
    return func.family(t.c.ip) == 4


def _v6(t: Table) -> ColumnElement[bool]:
    return func.family(t.c.ip) == 6


# (群組, [(項目代碼, [(資料表, 條件 or None), …])])。資料表名稱是寫死的常數，不接受外部輸入。
STATS: list[tuple[str, list[tuple[str, list[tuple[str, Cond]]]]]] = [
    ("ipam", [
        ("sections", [("sections", None)]),
        ("subnets", [("subnets", None)]),
        ("ipv4", [("ip_addresses", _v4)]),
        ("ipv6", [("ip_addresses", _v6)]),
        ("ip_ranges", [("ip_ranges", None)]),
        ("vlans", [("vlans", None)]),
        ("vrfs", [("vrfs", None)]),
        ("customers", [("customers", None)]),
        ("ip_requests", [("ip_requests", None)]),
    ]),
    ("physical", [
        ("locations", [("locations", None)]),
        ("racks", [("racks", None)]),
        ("devices", [("devices", None)]),
        ("device_ports", [("device_ports", None)]),
        ("cables", [("cables", None)]),
        ("circuits", [("circuits", None)]),
        ("power_outlets", [("power_outlets", None)]),
    ]),
    ("network", [
        ("dns_zones", [("dns_zones", None)]),
        ("dns_records", [("dns_records", None)]),
        ("dhcp_leases", [("dhcp_lease_sightings", None)]),
        ("dhcp_reservations", [("dhcp_reservations", None)]),
        ("arp_entries", [("arp_entries", None)]),
        ("fdb_entries", [("fdb_entries", None)]),
        ("firewall_rules", [("opnsense_rules", None), ("fortigate_policies", None), ("paloalto_policies", None),
                            ("checkpoint_rules", None),
                            ("mikrotik_rules", None), ("pve_firewall_rules", None)]),
        ("nat_translations", [("nat_translations", None)]),
        ("vpn_tunnels", [("vpn_tunnels", None)]),
        ("wireless_ssids", [("wireless_ssids", None)]),
    ]),
    ("sources", [
        ("virtual_machines", [("virtual_machines", None)]),
        ("librenms_devices", [("librenms_devices", None)]),
        ("zabbix_hosts", [("zabbix_hosts", None)]),
        ("checkmk_hosts", [("checkmk_hosts", None)]),
        ("wazuh_agents", [("wazuh_agents", None)]),
        ("rustdesk_peers", [("rustdesk_peers", None)]),
        ("scan_agents", [("scan_agents", None)]),
        ("cert_agents", [("cert_agents", None)]),
        ("certificates", [("certificates", None)]),
    ]),
    ("system", [
        ("users", [("users", None)]),
        ("groups", [("groups", None)]),
        ("api_tokens", [("api_tokens", None)]),
        ("webhooks", [("webhook_subscriptions", None)]),
        ("notifications", [("notifications", None)]),
        ("audit_logs", [("audit_logs", None)]),
        ("ip_changes", [("ip_change_log", None)]),
        ("background_tasks", [("background_tasks", None)]),
        ("ai_conversations", [("ai_chat_conversations", None)]),
        ("oui_vendors", [("oui_vendors", None)]),
    ]),
]

COUNT_TIMEOUT_MS = 3000


def _table(name: str) -> Table:
    import app.models  # noqa: F401  （確保所有模型都註冊進 metadata）
    from app.models.base import Base
    return Base.metadata.tables[name]


async def _count(session: AsyncSession, table: str, where: Cond) -> tuple[int, bool]:
    """(筆數, 是否為估計值)。逾時改用統計估計（有條件的計數沒有估計可用，回 -1）。"""
    t = _table(table)
    stmt = select(func.count()).select_from(t)
    if where is not None:
        stmt = stmt.where(where(t))
    try:
        async with session.begin_nested():
            await session.execute(text(f"SET LOCAL statement_timeout = {COUNT_TIMEOUT_MS}"))
            return int((await session.execute(stmt)).scalar_one() or 0), False
    except DBAPIError:
        if where:
            return -1, True
        est = (await session.execute(
            text("SELECT reltuples::bigint FROM pg_class WHERE relname = :t AND relkind = 'r'"),
            {"t": table})).scalar_one_or_none()
        return max(int(est or 0), 0), True


async def collect(session: AsyncSession) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for gkey, items in STATS:
        out = []
        for key, parts in items:
            total, approx = 0, False
            for table, where in parts:
                n, est = await _count(session, table, where)
                if n < 0:
                    total, approx = -1, True
                    break
                total += n
                approx = approx or est
            out.append({"key": key, "count": total if total >= 0 else None, "approx": approx})
        groups.append({"key": gkey, "items": out})
    return groups
