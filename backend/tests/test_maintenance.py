"""維護排程（2026-11-01）：維護期間範圍內不告警。

- 時段：一次性、每週例行（跨午夜、有效期間、提前結束這一次、延長這一次），用站台時區
- 範圍展開：IP、IP 範圍、子網路（含沒登記的位址）、區段含子區段、裝置含名下與主要 IP、機櫃、機房地點、單位、
  整合（直接選，或它連線的主機在範圍內）、掃描代理（直接選，或它的來源 IP 在範圍內）
- 狀態類告警：維護中不發「壞掉」也不發「恢復」；維護結束時還壞著，下一輪照常發（補發）
- 異常偵測：範圍內的發現標上維護、不通知；維護前就通知過的不重發；結束時還在的補發
- 只標示的維護不靜音
- 每輪：維護開始、結束各通知一次，結束時附「期間靜音幾則、仍未恢復幾則」
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
from app.models.address import IPAddress
from app.models.device import Device
from app.models.librenms import LibreNMSInstance
from app.models.location import Location, Rack
from app.models.maintenance import MaintenanceScope, MaintenanceSuppression, MaintenanceWindow
from app.models.notification import Notification
from app.models.scan_agent import ScanAgent
from app.models.section import Section
from app.models.subnet import Subnet
from app.services import maintenance as m
from app.services.state_alert import observe
from sqlalchemy import select

NOW = datetime.now(UTC)


def _once(minutes_before: int = 10, minutes_after: int = 60, behavior: str = "mute") -> MaintenanceWindow:
    return MaintenanceWindow(title="韌體更新", behavior=behavior, schedule_type="once",
                             starts_at=NOW - timedelta(minutes=minutes_before),
                             ends_at=NOW + timedelta(minutes=minutes_after))


async def _window(db, scopes: list[dict[str, Any]], **kw: Any) -> MaintenanceWindow:  # type: ignore[no-untyped-def]
    w = _once(**kw)
    db.add(w)
    await db.flush()
    for s in scopes:
        db.add(MaintenanceScope(window_id=w.id, **s))
    await db.commit()
    m.invalidate(db)
    return w


# ── 時段 ──

def test_weekly_occurrence_crosses_midnight_and_respects_validity() -> None:
    z = m.tz()
    w = MaintenanceWindow(title="例行", schedule_type="weekly", weekdays=[1], start_time=time(23, 0),
                          end_time=time(2, 0), valid_until=(NOW + timedelta(days=365)).date())
    tue = datetime(2026, 11, 3, 23, 30, tzinfo=z)          # 2026-11-03 是週二
    wed = datetime(2026, 11, 4, 1, 30, tzinfo=z)
    assert m.occurrence(w, tue) is not None
    assert m.occurrence(w, wed) is not None, "跨午夜：週三凌晨仍在週二那一次裡"
    assert m.occurrence(w, datetime(2026, 11, 4, 2, 30, tzinfo=z)) is None
    assert m.occurrence(w, datetime(2026, 11, 5, 23, 30, tzinfo=z)) is None, "週四不是例行日"
    w.valid_until = datetime(2026, 11, 2, tzinfo=z).date()
    assert m.occurrence(w, tue) is None, "過了有效期間"


def test_skip_and_extend_one_weekly_occurrence() -> None:
    z = m.tz()
    w = MaintenanceWindow(title="例行", schedule_type="weekly", weekdays=[1], start_time=time(2, 0),
                          end_time=time(4, 0), valid_until=(NOW + timedelta(days=365)).date())
    at = datetime(2026, 11, 3, 3, 0, tzinfo=z)
    w.skip_until = datetime(2026, 11, 3, 4, 0, tzinfo=z)
    assert m.occurrence(w, at) is None, "這一次提前結束"
    assert m.occurrence(w, datetime(2026, 11, 10, 3, 0, tzinfo=z)) is not None, "下週照常"
    w.skip_until = None
    w.extended_until = datetime(2026, 11, 3, 6, 0, tzinfo=z)
    assert m.occurrence(w, datetime(2026, 11, 3, 5, 0, tzinfo=z)) is not None, "這一次延長到 06:00"
    assert m.occurrence(w, datetime(2026, 11, 10, 5, 0, tzinfo=z)) is None, "延長只算那一次"


# ── 範圍 ──

async def _net(db):  # type: ignore[no-untyped-def]
    parent = Section(name=f"p-{uuid.uuid4().hex[:6]}")
    db.add(parent)
    await db.flush()
    child = Section(name=f"c-{uuid.uuid4().hex[:6]}", parent_id=parent.id)
    db.add(child)
    await db.flush()
    sub = Subnet(section_id=child.id, cidr="198.51.100.0/24")
    other = Subnet(section_id=parent.id, cidr="203.0.113.0/24")
    db.add_all([sub, other])
    await db.flush()
    loc = Location(name=f"dc-{uuid.uuid4().hex[:4]}")
    db.add(loc)
    await db.flush()
    rack = Rack(name="R1", location_id=loc.id, u_height=42)
    db.add(rack)
    await db.flush()
    dev = Device(name="sw-01", rack_id=rack.id)
    db.add(dev)
    await db.flush()
    a = IPAddress(subnet_id=sub.id, ip="198.51.100.10", state="active", hostname="librenms.example.com")
    b = IPAddress(subnet_id=other.id, ip="203.0.113.5", state="active", device_id=dev.id)
    c = IPAddress(subnet_id=other.id, ip="203.0.113.6", state="active")
    db.add_all([a, b, c])
    await db.flush()
    dev.primary_ip_id = c.id
    await db.commit()
    return {"parent": parent, "child": child, "sub": sub, "other": other, "loc": loc, "rack": rack, "dev": dev,
            "a": a, "b": b, "c": c}


@pytest.mark.anyio
async def test_scope_expansion(db_session) -> None:
    n = await _net(db_session)
    # 父區段 → 子區段的子網路也算；沒登記的位址靠 CIDR
    await _window(db_session, [{"scope_type": "section", "object_id": n["parent"].id}])
    idx = await m.build_index(db_session)
    assert idx.for_ip(ip_id=n["a"].id) and idx.for_ip(ip="198.51.100.99") and idx.for_ip(ip="203.0.113.77")
    assert idx.for_ip(ip="192.0.2.1") is None


@pytest.mark.anyio
async def test_rack_covers_devices_and_their_primary_ip(db_session) -> None:
    n = await _net(db_session)
    await _window(db_session, [{"scope_type": "rack", "object_id": n["rack"].id}])
    idx = await m.build_index(db_session)
    assert idx.for_ip(ip_id=n["b"].id), "裝置名下的 IP"
    assert idx.for_ip(ip="203.0.113.6"), "裝置的主要 IP"
    assert idx.for_ip(ip_id=n["a"].id) is None


@pytest.mark.anyio
async def test_ip_range_and_location(db_session) -> None:
    n = await _net(db_session)
    await _window(db_session, [{"scope_type": "ip_range", "range_start": "192.0.2.10", "range_end": "192.0.2.20"}])
    idx = await m.build_index(db_session)
    assert idx.for_ip(ip="192.0.2.15") and idx.for_ip(ip="192.0.2.21") is None
    await _window(db_session, [{"scope_type": "location", "object_id": n["loc"].id}])
    idx = await m.build_index(db_session)
    assert idx.for_ip(ip_id=n["b"].id)


@pytest.mark.anyio
async def test_integration_and_agent_follow_their_host(db_session) -> None:
    n = await _net(db_session)
    ln = LibreNMSInstance(name="ln", api_url="https://librenms.example.com", api_token_enc=b"x", api_token_nonce=b"x")
    ln2 = LibreNMSInstance(name="ln2", api_url="https://198.51.100.10:8443", api_token_enc=b"x", api_token_nonce=b"x")
    agent = ScanAgent(name="agent-x", enroll_key_hash=uuid.uuid4().hex * 2, enabled=True,
                      last_source_ip="198.51.100.10")
    db_session.add_all([ln, ln2, agent])
    await db_session.commit()
    await _window(db_session, [{"scope_type": "ip", "object_id": n["a"].id}])
    idx = await m.build_index(db_session)
    assert idx.for_key(f"integration:librenms:{ln.id}"), "用主機名稱對到 IPAM 紀錄"
    assert idx.for_key(f"integration:librenms:{ln2.id}"), "連線位址就是維護中的 IP"
    assert idx.for_key(f"agent:scan:{agent.id}") and idx.for_key(f"scan_load:{agent.id}")


# ── 狀態類告警 ──

@pytest.mark.anyio
async def test_state_alert_is_held_during_maintenance_and_fires_after(db_session) -> None:
    ln = LibreNMSInstance(name="ln", api_url="https://ln.example.org", api_token_enc=b"x", api_token_nonce=b"x")
    db_session.add(ln)
    await db_session.commit()
    key = f"integration:librenms:{ln.id}"
    w = await _window(db_session, [{"scope_type": "integration", "integration_kind": "librenms", "object_id": ln.id}])
    assert await observe(db_session, key=key, failing=True, threshold=2) is None
    assert await observe(db_session, key=key, failing=True, threshold=2) is None, "維護中：不發壞掉"
    sup = (await db_session.execute(select(MaintenanceSuppression))).scalars().all()
    assert [s.key for s in sup] == [key] and sup[0].window_id == w.id
    # 維護結束（改成已結束）→ 下一輪照常補發
    w.ends_at = NOW - timedelta(minutes=1)
    await db_session.commit()
    m.invalidate(db_session)
    assert await observe(db_session, key=key, failing=True, threshold=2) == "down"


@pytest.mark.anyio
async def test_recovery_during_maintenance_is_silent(db_session) -> None:
    key = "integration:wazuh:" + str(uuid.uuid4())
    assert await observe(db_session, key=key, failing=True, threshold=1) == "down"   # 維護前就壞了、已通知
    await _window(db_session, [{"scope_type": "integration", "integration_kind": "wazuh"}])
    assert await observe(db_session, key=key, failing=False) is None, "維護中恢復不另外發"


@pytest.mark.anyio
async def test_mark_only_does_not_mute(db_session) -> None:
    key = "integration:zabbix:" + str(uuid.uuid4())
    await _window(db_session, [{"scope_type": "integration", "integration_kind": "zabbix"}], behavior="mark")
    assert await observe(db_session, key=key, failing=True, threshold=1) == "down"


# ── 異常偵測 ──

@pytest.mark.anyio
async def test_anomaly_items_in_scope_are_tagged_and_not_notified(db_session, admin_user) -> None:
    from app.services.anomaly import _notify_categories
    from app.services.system_config import get_anomaly_seen
    n = await _net(db_session)
    await _window(db_session, [{"scope_type": "subnet", "object_id": n["sub"].id}])
    data = {"ghost_ips": [{"ip": "198.51.100.10", "ip_address_id": str(n["a"].id), "hostname": "x"}],
            "unauthorized_ips": [{"ip": "192.0.2.50"}]}
    sent = await _notify_categories(db_session, data, only_new=True)
    assert sent == 1, "只有範圍外的未授權 IP 發出"
    assert data["ghost_ips"][0]["maintenance"]["title"] == "韌體更新"
    seen = await get_anomaly_seen(db_session)
    assert not seen.get("ghost_ips"), "被靜音的不寫進看過清單，結束後還在就會發"
    notes = (await db_session.execute(select(Notification).where(Notification.object_type == "anomaly"))).scalars().all()
    assert notes and all("ghost" not in (x.params or {}).get("label_key", "") for x in notes)


# ── 每輪：開始、結束、摘要 ──

@pytest.mark.anyio
async def test_tick_notifies_start_and_end_with_summary(db_session, admin_user) -> None:
    key = "integration:proxmox:" + str(uuid.uuid4())
    w = await _window(db_session, [{"scope_type": "integration", "integration_kind": "proxmox"}])
    assert (await m.tick(db_session))["started"] == 1
    assert (await m.tick(db_session))["started"] == 0, "同一次不重發"
    await observe(db_session, key=key, failing=True, threshold=1)        # 被靜音，而且到結束都還壞著
    w.ends_at = NOW - timedelta(seconds=1)
    await db_session.commit()
    assert (await m.tick(db_session))["ended"] == 1
    rows = (await db_session.execute(select(Notification).where(Notification.object_type == "maintenance")
                                     .order_by(Notification.created_at))).scalars().all()
    keys = [r.title_key for r in rows]
    assert "notif.maint_started" in keys and "notif.maint_ended" in keys
    ended = next(r for r in rows if r.title_key == "notif.maint_ended")
    assert ended.params["suppressed"] == 1 and ended.params["failing"] == 1
    assert ended.link == f"/maintenance/{w.id}"


# ── API ──

async def _user(db):  # type: ignore[no-untyped-def]
    from app.core.security import hash_password
    from app.models.user import User
    u = User(username=f"u-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local", display_name="U",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True)
    db.add(u)
    await db.commit()
    return u


def _hdr(user) -> dict[str, str]:  # type: ignore[no-untyped-def]
    from app.services.auth import issue_access_token
    return {"Authorization": f"Bearer {issue_access_token(user)}"}


def _code(r) -> str:  # type: ignore[no-untyped-def]
    d = r.json().get("detail")
    return d.get("code") if isinstance(d, dict) else str(d)


@pytest.mark.anyio
async def test_quick_maintenance_then_extend_and_end(client, auth_headers, db_session) -> None:
    from app.models.audit import AuditLog
    n = await _net(db_session)
    r = await client.post("/api/v1/maintenance/quick", headers=auth_headers,
                          json={"scope": {"scope_type": "device", "object_id": str(n["dev"].id)}, "minutes": 120})
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["status"] == "active" and w["title"] == "sw-01 維護"
    act = (await client.get("/api/v1/maintenance/active", headers=auth_headers,
                            params={"object_type": "ip", "object_id": str(n["b"].id)})).json()
    assert [x["id"] for x in act] == [w["id"]], "裝置名下的 IP 看得到這個維護"
    assert (await client.get("/api/v1/maintenance/summary", headers=auth_headers)).json()["active"] == 1
    r = await client.post(f"/api/v1/maintenance/{w['id']}/extend", headers=auth_headers, json={"minutes": 60})
    assert r.status_code == 200
    assert datetime.fromisoformat(r.json()["current_end"]) > datetime.fromisoformat(w["current_end"])
    r = await client.post(f"/api/v1/maintenance/{w['id']}/end", headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "past"
    acts = {x.action for x in (await db_session.execute(
        select(AuditLog).where(AuditLog.object_type == "maintenance"))).scalars()}
    assert acts >= {"create", "extend", "end"}


@pytest.mark.anyio
async def test_create_once_weekly_and_validation(client, auth_headers, db_session) -> None:
    n = await _net(db_session)
    start = (NOW + timedelta(days=1)).isoformat()
    body = {"title": "機房斷電", "ticket": "CHG-001", "schedule": {"type": "once", "starts_at": start,
            "ends_at": (NOW + timedelta(days=1, hours=4)).isoformat()},
            "scopes": [{"scope_type": "location", "object_id": str(n["loc"].id)}]}
    r = await client.post("/api/v1/maintenance", headers=auth_headers, json=body)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "upcoming"
    body["schedule"]["ends_at"] = (NOW + timedelta(days=10)).isoformat()
    r = await client.post("/api/v1/maintenance", headers=auth_headers, json=body)
    assert r.status_code == 422 and _code(r) == "maintenance_too_long"
    weekly = {"title": "例行", "schedule": {"type": "weekly", "weekdays": [1], "start_time": "02:00",
                                          "end_time": "04:00"},
              "scopes": [{"scope_type": "subnet", "object_id": str(n["sub"].id)}]}
    r = await client.post("/api/v1/maintenance", headers=auth_headers, json=weekly)
    assert r.status_code == 422 and _code(r) == "maintenance_weekly_incomplete", "每週例行一定要到期日"
    weekly["schedule"]["valid_until"] = (NOW + timedelta(days=90)).date().isoformat()
    r = await client.post("/api/v1/maintenance", headers=auth_headers, json=weekly)
    assert r.status_code == 201, r.text
    lst = (await client.get("/api/v1/maintenance", headers=auth_headers)).json()["items"]
    assert {x["title"] for x in lst} == {"機房斷電", "例行"}


@pytest.mark.anyio
async def test_large_scope_needs_confirmation_and_preview_counts(client, auth_headers, db_session) -> None:
    n = await _net(db_session)
    await client.put("/api/v1/maintenance/settings", headers=auth_headers, json={"max_hours": 168, "large_threshold": 10})
    scopes = [{"scope_type": "ip_range", "range": "192.0.2.0/24"}, {"scope_type": "section", "object_id": str(n["parent"].id)}]
    p = (await client.post("/api/v1/maintenance/preview", headers=auth_headers, json=scopes)).json()
    assert p["subnets"] == 2 and p["ips"] == 3 and p["range_addresses"] >= 256
    db_session.add_all([IPAddress(subnet_id=n["sub"].id, ip=f"198.51.100.{i}", state="active") for i in range(100, 112)])
    await db_session.commit()
    body = {"title": "大範圍", "schedule": {"type": "once", "starts_at": NOW.isoformat(),
            "ends_at": (NOW + timedelta(hours=1)).isoformat()}, "scopes": scopes}
    r = await client.post("/api/v1/maintenance", headers=auth_headers, json=body)
    assert r.status_code == 409 and _code(r) == "maintenance_scope_large"
    body["confirm_large"] = True
    assert (await client.post("/api/v1/maintenance", headers=auth_headers, json=body)).status_code == 201


@pytest.mark.anyio
async def test_permissions(client, db_session) -> None:
    from app.models.permission import Permission
    n = await _net(db_session)
    u = await _user(db_session)
    db_session.add(Permission(object_type="subnet", object_id=n["sub"].id, principal_type="user",
                              principal_id=u.id, level="write"))
    await db_session.commit()
    ok = {"scope": {"scope_type": "subnet", "object_id": str(n["sub"].id)}, "minutes": 30}
    r = await client.post("/api/v1/maintenance/quick", headers=_hdr(u), json=ok)
    assert r.status_code == 201, r.text
    r = await client.post("/api/v1/maintenance/quick", headers=_hdr(u),
                          json={"scope": {"scope_type": "subnet", "object_id": str(n["other"].id)}, "minutes": 30})
    assert r.status_code == 403 and _code(r) == "maintenance_scope_forbidden"
    r = await client.post("/api/v1/maintenance/quick", headers=_hdr(u),
                          json={"scope": {"scope_type": "integration", "integration_kind": "librenms"}, "minutes": 30})
    assert r.status_code == 403, "整合只限管理員"
    assert [x["title"] for x in (await client.get("/api/v1/maintenance", headers=_hdr(u))).json()["items"]] \
        == [r0 for r0 in ["198.51.100.0/24 維護"]]


@pytest.mark.anyio
async def test_scope_search(client, auth_headers, db_session) -> None:
    n = await _net(db_session)
    r = (await client.get("/api/v1/maintenance/scope-search", headers=auth_headers,
                          params={"type": "ip", "q": "198.51.100.1"})).json()
    assert [x["label"] for x in r["items"]] == ["198.51.100.10（librenms.example.com）"]
    r = (await client.get("/api/v1/maintenance/scope-search", headers=auth_headers,
                          params={"type": "integration", "q": "LibreNMS"})).json()
    assert {"id": None, "integration_kind": "librenms", "label": "LibreNMS（全部）"} in r["items"]
    u = await _user(db_session)
    r = (await client.get("/api/v1/maintenance/scope-search", headers=_hdr(u),
                          params={"type": "integration"}))
    assert r.status_code in (200, 403) and (r.status_code == 403 or r.json()["items"] == [])
    assert n


@pytest.mark.anyio
async def test_ai_tool_lists_maintenance_the_user_can_see(db_session, admin_user) -> None:
    """AI 問「現在有什麼在維護」：工具沒有就會回答系統沒有這種資料；只列看得到範圍的維護。"""
    from app.mcp.tools import TOOLS
    from app.models.permission import Permission
    n = await _net(db_session)
    await _window(db_session, [{"scope_type": "subnet", "object_id": n["sub"].id, "label": "198.51.100.0/24"}])
    await _window(db_session, [{"scope_type": "subnet", "object_id": n["other"].id, "label": "203.0.113.0/24"}])
    fn = TOOLS["list_maintenance"]["fn"]
    out = await fn(db_session, user=admin_user)
    assert out["count"] == 2 and all(x["status"] == "active" for x in out["maintenance"])
    assert {"title", "behavior", "current_end", "scopes", "suppressed", "ticket"} <= set(out["maintenance"][0])
    u = await _user(db_session)
    db_session.add(Permission(object_type="subnet", object_id=n["sub"].id, principal_type="user",
                              principal_id=u.id, level="read"))
    await db_session.commit()
    out = await fn(db_session, user=u, status="active")
    assert [s["label"] for x in out["maintenance"] for s in x["scopes"]] == ["198.51.100.0/24"]


@pytest.mark.anyio
async def test_audit_entries_are_readable(client, auth_headers, db_session) -> None:
    """稽核明細要看得懂：時間用站台時區、精確到分；範圍寫名稱不是 UUID；目標欄是標題；修改記改前改後。"""
    n = await _net(db_session)
    z = m.tz()
    start = (datetime.now(z) + timedelta(hours=1)).replace(second=0, microsecond=0)
    end = start + timedelta(hours=2)
    body = {"title": "韌體更新", "ticket": "CHG-1", "behavior": "mute",
            "schedule": {"type": "once", "starts_at": start.isoformat(), "ends_at": end.isoformat()},
            "scopes": [{"scope_type": "device", "object_id": str(n["dev"].id)}]}
    r = await client.post("/api/v1/maintenance", headers=auth_headers, json=body)
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    items = (await client.get("/api/v1/audit", headers=auth_headers,
                              params={"object_type": "maintenance"})).json()["items"]
    created = next(x for x in items if x["action"] == "create" and x["object_id"] == wid)
    assert created["object_label"] == "韌體更新"
    d = created["diff"]
    assert d["schedule"] == f"{start:%Y-%m-%d %H:%M} - {end:%H:%M}" if start.date() == end.date() \
        else d["schedule"] == f"{start:%Y-%m-%d %H:%M} - {end:%Y-%m-%d %H:%M}"
    assert d["scopes"] == ["裝置 sw-01"]
    assert "confirm_large" not in d and "object_id" not in str(d)
    body["schedule"] = {"type": "weekly", "weekdays": [6, 1], "start_time": "02:00", "end_time": "04:00",
                        "valid_until": (start + timedelta(days=30)).date().isoformat()}
    r = await client.patch(f"/api/v1/maintenance/{wid}", headers=auth_headers, json=body)
    assert r.status_code == 200, r.text
    items = (await client.get("/api/v1/audit", headers=auth_headers,
                              params={"object_type": "maintenance"})).json()["items"]
    upd = next(x for x in items if x["action"] == "update" and x["object_id"] == wid)["diff"]
    assert set(upd["changes"]) == {"schedule"}, "沒改的欄位不列"
    assert upd["before"]["schedule"].startswith(f"{start:%Y-%m-%d %H:%M}")
    assert upd["changes"]["schedule"].startswith("每週日、二 02:00 - 04:00")
