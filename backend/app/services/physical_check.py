"""實體資料檢查：列出佈線與供電的既有問題，只讀（2026-11-21，交接包 physical-v1.2）。

新的寫入規則（services/physical_links.py）只擋之後的錯；升級前就存在的重複占用、不對稱配對、半條線、
舊式插座直連，依交接包 UPGRADE.md 不自動刪除、合併或改接，列在這裡讓管理員自己決定怎麼修。

每一類回 `{total, items}`，items 最多 LIMIT 筆；total 是完整數量（不受 items 上限影響）。
全部用 GROUP BY / JOIN 一次查完，不逐筆查。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LIMIT = 500


# 每支查詢都是完整的字面字串（不拼接），沒有任何外部輸入；上限用參數綁定

# 一個埠接了兩條以上「已接線」的纜線
_MULTI_CABLE_COUNT = """
    WITH m AS (
        SELECT t.object_id AS port_id FROM cable_terminations t JOIN cables c ON c.id = t.cable_id
        WHERE t.object_type = 'device_port' AND c.status = 'connected'
        GROUP BY t.object_id HAVING count(DISTINCT c.id) > 1
    )
SELECT count(*) FROM m
"""
_MULTI_CABLE_ITEMS = """
    WITH m AS (
        SELECT t.object_id AS port_id FROM cable_terminations t JOIN cables c ON c.id = t.cable_id
        WHERE t.object_type = 'device_port' AND c.status = 'connected'
        GROUP BY t.object_id HAVING count(DISTINCT c.id) > 1
    )

    SELECT d.id AS device_id, d.name AS device, p.id AS port_id, p.name AS port,
           array_agg(DISTINCT coalesce(c.label, '') ORDER BY coalesce(c.label, '')) AS cables
    FROM m JOIN device_ports p ON p.id = m.port_id JOIN devices d ON d.id = p.device_id
    JOIN cable_terminations t ON t.object_id = p.id AND t.object_type = 'device_port'
    JOIN cables c ON c.id = t.cable_id AND c.status = 'connected'
    GROUP BY d.id, d.name, p.id, p.name ORDER BY d.name, p.name LIMIT :lim
"""

# 一個插座接了兩顆以上電源
_MULTI_PSU_COUNT = """
    WITH m AS (
        SELECT outlet_id FROM device_power_ports WHERE outlet_id IS NOT NULL
        GROUP BY outlet_id HAVING count(*) > 1
    )
SELECT count(*) FROM m
"""
_MULTI_PSU_ITEMS = """
    WITH m AS (
        SELECT outlet_id FROM device_power_ports WHERE outlet_id IS NOT NULL
        GROUP BY outlet_id HAVING count(*) > 1
    )

    SELECT o.id AS outlet_id, o.label AS outlet,
           array_agg(d.name || ' ' || pp.name ORDER BY d.name, pp.name) AS ports
    FROM m JOIN power_outlets o ON o.id = m.outlet_id
    JOIN device_power_ports pp ON pp.outlet_id = o.id JOIN devices d ON d.id = pp.device_id
    GROUP BY o.id, o.label ORDER BY o.label LIMIT :lim
"""

# A 指向 B，B 卻沒指回 A
_ASYM_COUNT = """
SELECT count(*)
    FROM device_ports p JOIN device_ports q ON q.id = p.peer_port_id
    LEFT JOIN device_ports z ON z.id = q.peer_port_id
    JOIN devices d ON d.id = p.device_id
    WHERE q.peer_port_id IS DISTINCT FROM p.id
"""
_ASYM_ITEMS = """
SELECT d.id AS device_id, d.name AS device, p.id AS port_id, p.name AS port,
       q.name AS peer, z.name AS peer_points_to
    FROM device_ports p JOIN device_ports q ON q.id = p.peer_port_id
    LEFT JOIN device_ports z ON z.id = q.peer_port_id
    JOIN devices d ON d.id = p.device_id
    WHERE q.peer_port_id IS DISTINCT FROM p.id
 ORDER BY d.name, p.name LIMIT :lim
"""

# 已接線的纜線：少一端，或端點指向已經不存在的埠
_INCOMPLETE_COUNT = """
SELECT count(*)
    FROM cables c
    LEFT JOIN LATERAL (SELECT count(*) AS n FROM cable_terminations t WHERE t.cable_id = c.id) tc ON true
    LEFT JOIN LATERAL (
        SELECT count(*) AS n FROM cable_terminations t
        LEFT JOIN device_ports p ON p.id = t.object_id
        WHERE t.cable_id = c.id AND t.object_type = 'device_port' AND p.id IS NULL
    ) miss ON true
    WHERE c.status = 'connected' AND (tc.n < 2 OR miss.n > 0)
"""
_INCOMPLETE_ITEMS = """
SELECT c.id AS cable_id, coalesce(c.label, '') AS cable,
       CASE WHEN miss.n > 0 THEN 'missing_port' ELSE 'one_end' END AS reason
    FROM cables c
    LEFT JOIN LATERAL (SELECT count(*) AS n FROM cable_terminations t WHERE t.cable_id = c.id) tc ON true
    LEFT JOIN LATERAL (
        SELECT count(*) AS n FROM cable_terminations t
        LEFT JOIN device_ports p ON p.id = t.object_id
        WHERE t.cable_id = c.id AND t.object_type = 'device_port' AND p.id IS NULL
    ) miss ON true
    WHERE c.status = 'connected' AND (tc.n < 2 OR miss.n > 0)
 ORDER BY c.label NULLS LAST, c.id LIMIT :lim
"""

# 舊式插座直連：插座記著裝置，但那台裝置沒有電源埠接在這個插座
_LEGACY_COUNT = """
SELECT count(*)
    FROM power_outlets o JOIN devices d ON d.id = o.device_id
    LEFT JOIN LATERAL (
        SELECT bool_or(pp.device_id = o.device_id) AS own, bool_or(pp.device_id <> o.device_id) AS other
        FROM device_power_ports pp WHERE pp.outlet_id = o.id
    ) x ON true
    WHERE o.device_id IS NOT NULL AND (x.own IS NOT TRUE)
"""
_LEGACY_ITEMS = """
SELECT o.id AS outlet_id, o.label AS outlet, d.id AS device_id, d.name AS device,
       CASE WHEN x.other THEN 'psu_of_other_device' ELSE 'no_psu' END AS reason
    FROM power_outlets o JOIN devices d ON d.id = o.device_id
    LEFT JOIN LATERAL (
        SELECT bool_or(pp.device_id = o.device_id) AS own, bool_or(pp.device_id <> o.device_id) AS other
        FROM device_power_ports pp WHERE pp.outlet_id = o.id
    ) x ON true
    WHERE o.device_id IS NOT NULL AND (x.own IS NOT TRUE)
 ORDER BY o.label LIMIT :lim
"""

_SECTIONS = (
    ("ports_multi_cable", _MULTI_CABLE_COUNT, _MULTI_CABLE_ITEMS),
    ("outlets_multi_psu", _MULTI_PSU_COUNT, _MULTI_PSU_ITEMS),
    ("asymmetric_peers", _ASYM_COUNT, _ASYM_ITEMS),
    ("incomplete_cables", _INCOMPLETE_COUNT, _INCOMPLETE_ITEMS),
    ("legacy_outlet_links", _LEGACY_COUNT, _LEGACY_ITEMS),
)


async def physical_check(session: AsyncSession) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, count_sql, items_sql in _SECTIONS:
        total = int((await session.execute(text(count_sql))).scalar_one())
        items = [dict(r) for r in (await session.execute(text(items_sql), {"lim": LIMIT})).mappings()] if total else []
        out[key] = {"total": total, "items": items}
    out["total"] = sum(v["total"] for v in out.values())
    out["limit"] = LIMIT
    return out
