"""DNS 比對群組在顯示端的合併：異常偵測與 IP 變更評估（紀錄頁的合併在 test_dns_compare_group_api.py）。

兩台同步的 DNS 都接進來時，同一筆紀錄以前在每個地方都出現兩次；IP 變更評估更要處置兩次。
同一組裡正規化後完全相同的紀錄是同一筆，列出哪幾台都有；不同組（或沒分組）的照舊分開。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.models.address import IPAddress
from app.models.dns import DNSRecord, DNSServer, DNSZone
from app.models.dns_compare_group import DNSCompareGroup
from app.models.section import Section
from app.models.subnet import Subnet
from app.services import dns_compare
from app.services.change_impact.config import DEFAULTS


async def _server(db, name: str, group: DNSCompareGroup | None, recs: list[tuple[str, str, str]],
                  zone: str = "example.net", ztype: str = "forward") -> DNSServer:
    srv = DNSServer(name=f"{name}-{uuid.uuid4().hex[:4]}", type="powerdns", enabled=True,
                    last_sync_at=datetime.now(UTC), compare_group_id=group.id if group else None)
    db.add(srv)
    await db.flush()
    z = DNSZone(server_id=srv.id, name=zone, type=ztype)
    db.add(z)
    await db.flush()
    for n, t, v in recs:
        db.add(DNSRecord(zone_id=z.id, name=n, type=t, value=v, ttl=300, source="from_dns_pulled",
                         name_norm=dns_compare.normalize_name(n, zone), value_norm=dns_compare.normalize_value(t, v)))
    await db.flush()
    return srv


async def _group(db, **kw: Any) -> DNSCompareGroup:
    g = DNSCompareGroup(name=f"g-{uuid.uuid4().hex[:6]}", **kw)
    db.add(g)
    await db.flush()
    return g


async def test_mismatch_category_lists_only_confirmed_diffs(db_session) -> None:
    from app.services.anomaly import detect_dns_compare_mismatch
    g = await _group(db_session, grace_minutes=30, notify_enabled=False)
    a = await _server(db_session, "ns1", g, [("old.example.net", "A", "192.0.2.1")])
    b = await _server(db_session, "ns2", g, [])
    now = datetime.now(UTC)
    a.last_sync_at = b.last_sync_at = now - timedelta(minutes=40)
    await dns_compare.check_group(db_session, g, now=now - timedelta(minutes=40))
    b.last_sync_at = now                       # 寬限時間過後重新拉取，仍然沒有 → 確認
    await dns_compare.check_group(db_session, g, now=now)
    # 剛出現的（ns1 剛拉取到、ns2 之後還沒重新拉取）不列
    za = (await db_session.execute(DNSZone.__table__.select().where(DNSZone.server_id == a.id))).first()
    db_session.add(DNSRecord(zone_id=za.id, name="new.example.net", type="A", value="192.0.2.2",
                             source="from_dns_pulled", name_norm="new.example.net", value_norm="192.0.2.2"))
    a.last_sync_at = now
    await db_session.flush()
    await dns_compare.check_group(db_session, g, now=now)
    items = await detect_dns_compare_mismatch(db_session)
    assert [(i["name"], i["group"]) for i in items] == [("old.example.net", g.name)]
    assert items[0]["present_on"] == a.name and items[0]["missing_on"] == b.name


async def test_run_detection_reports_and_counts_the_category(db_session) -> None:
    from app.services.anomaly import run_detection
    g = await _group(db_session, grace_minutes=0, notify_enabled=False)
    await _server(db_session, "ns1", g, [("x.example.net", "A", "192.0.2.9")])
    await _server(db_session, "ns2", g, [])
    await dns_compare.check_group(db_session, g)
    rep = (await run_detection(db_session, notify_admins=False)).to_dict()
    assert len(rep["dns_compare_mismatch"]) == 1
    assert rep["total"] >= 1


async def test_dangling_dns_is_merged_within_a_group_only(db_session) -> None:
    from app.services.anomaly import detect_dangling_dns
    g = await _group(db_session)
    a = await _server(db_session, "ns1", g, [("Ghost.example.net", "A", "203.0.113.77")])
    b = await _server(db_session, "ns2", g, [("ghost.example.net", "A", "203.0.113.77")])
    lone = await _server(db_session, "other", None, [("ghost.example.net", "A", "203.0.113.77")])
    rows = [r for r in await detect_dangling_dns(db_session) if r["value"] == "203.0.113.77"]
    assert len(rows) == 2, "同一組合成一筆；不同組的另外一筆"
    grouped = next(r for r in rows if r.get("compare_group") == g.name)
    assert grouped["server"] == ", ".join(sorted([a.name, b.name]))
    assert next(r for r in rows if not r.get("compare_group"))["server"] == lone.name


async def test_change_impact_has_one_finding_per_record_shared_by_a_group(db_session, admin_user) -> None:
    from app.services.change_impact.engine import analyze
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db_session.add(sub)
    await db_session.flush()
    ip = IPAddress(subnet_id=sub.id, ip="198.51.100.10", state="active", hostname="erp.example.net")
    db_session.add(ip)
    await db_session.flush()
    g = await _group(db_session)
    a = await _server(db_session, "ns1", g, [("erp.example.net", "A", "198.51.100.10")])
    b = await _server(db_session, "ns2", g, [("ERP.example.net", "A", "198.51.100.10")])
    lone = await _server(db_session, "other", None, [("erp.example.net", "A", "198.51.100.10")])
    await db_session.commit()
    res = await analyze(db_session, user=admin_user, scenario_type="ip_renumber", target_type="ip_address",
                        target_id=ip.id, parameters={"new_ip": "198.51.100.80"}, cfg={**DEFAULTS, "enabled": True})
    found = [f for f in res.findings if f.rule_id == "dns.address_record"]
    assert len(found) == 2, "同一組兩台合成一個發現，另一台（沒分組）自己一個"
    merged = next(f for f in found if len(f.evidence_keys) == 2)
    assert merged.params["server"] == ", ".join(sorted([a.name, b.name]))
    assert merged.params.get("compare_group") == g.name
    single = next(f for f in found if len(f.evidence_keys) == 1)
    assert single.params["server"] == lone.name
    # 每一筆紀錄都還是一份證據（可以追到是哪一台的哪一筆）
    assert {e.payload["server"] for e in res.evidence if e.object_type == "dns_record"} == {a.name, b.name, lone.name}


def test_every_anomaly_category_is_reachable_from_the_ai_tool() -> None:
    """MCP 的 list_anomalies 自己維護一份類別表，漏過好幾次（畫面上有、AI 問不到）。
    異常報告的每一個類別（參考用的除外）都要查得到；新增 dns_compare_mismatch 時一併守住。"""
    import dataclasses
    import inspect

    from app.mcp import tools
    from app.services.anomaly import AnomalyReport

    src = inspect.getsource(tools.list_anomalies)
    cats = {f.name for f in dataclasses.fields(AnomalyReport)
            if f.name not in ("mac_drift_reference", "unauthorized_total")}
    missing = sorted(c for c in cats if f'"{c}":' not in src)
    assert not missing, f"list_anomalies 查不到這些類別：{missing}"
