"""RustDesk Server（開源版）整合的伺服器端：專用 RustDesk 代理回報 hbbs 的裝置清單與線上狀態，jt-ipam 存起來、
保守地對應到 IP 記錄，IP 詳細資料給「以 RustDesk 連線」的網址。

對應 ID → IP 的規則（不確定就不對應，寧可少對也不要對錯）：
- 登記 IP 要落在唯一一筆 IP 記錄（重疊網段兩筆都有 → 不猜）
- 這個 ID 要曾被 jt-ipam 看到在線、而且在最近 7 天內 —— 離線裝置的登記 IP 是它上次註冊時的位址，
  DHCP 位址可能早就發給別台了
- 同一個登記 IP 有 3 個以上的 ID ＝ NAT 回流（所有客戶端看起來都從防火牆來），全部不對應；
  2 個的話只對應唯一在線的那個

資料生命週期（照「上游不再回報就清掉」的原則，但只有完整讀到資料庫才清）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

from app.models.address import IPAddress
from app.models.rustdesk import RustDeskPeer, RustDeskServer
from app.models.section import Section
from app.models.subnet import Subnet
from sqlalchemy import select

PUB = "E2EfakeRustDeskKeyForTestsOnly0000000000000="


async def _subnet(db, cidr: str) -> Subnet:
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db.add(sec)
    await db.flush()
    sub = Subnet(section_id=sec.id, cidr=cidr)
    db.add(sub)
    await db.flush()
    return sub


async def _ip(db, sub: Subnet, addr: str, **kw) -> IPAddress:
    ip = IPAddress(subnet_id=sub.id, ip=addr, **kw)
    db.add(ip)
    await db.flush()
    return ip


async def _server(db, raw: str | None = None, **kw) -> RustDeskServer:
    """一台 RustDesk 伺服器；raw 是它專用代理的金鑰（None＝還沒有金鑰）。"""
    from app.services.rustdesk import agent_key_hash
    srv = RustDeskServer(name=f"rd-{uuid.uuid4().hex[:6]}",
                         agent_key_hash=agent_key_hash(raw) if raw else None, **kw)
    db.add(srv)
    await db.commit()
    return srv


async def _poll(client, raw: str, **body):
    return await client.post("/api/v1/rustdesk/agent/poll", headers={"X-Agent-Key": raw},
                             json={"version": "1.0.0", **body})


def _report(source_id, peers: list[tuple], *, db_ok=True, online_ok=True, truncated=False) -> dict:
    """peers: (id, ip, online)"""
    return {
        "source_id": str(source_id),
        "version": "1.1.16",
        "public_key": PUB,
        "files": {"db": {"path": "/var/lib/rustdesk-server/db_v2.sqlite3", "ok": db_ok,
                         "error": None if db_ok else "OperationalError: unable to open database file",
                         "truncated": truncated}},
        "online_ok": online_ok,
        "online_error": None if online_ok else "ConnectionRefusedError: [Errno 111] Connection refused",
        "peers": [{"id": i, "created_at": "2026-01-02 03:04:05", "ip": ip,
                   "online": (on if online_ok else None)} for i, ip, on in peers],
    }


async def _send(client, raw, body) -> dict:
    r = await client.post("/api/v1/rustdesk/agent/report", headers={"X-Agent-Key": raw}, json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _peers(db, srv) -> dict[str, RustDeskPeer]:
    rows = (await db.execute(select(RustDeskPeer).where(RustDeskPeer.server_id == srv.id)
                             .execution_options(populate_existing=True))).scalars().all()
    return {p.rustdesk_id: p for p in rows}


# ── 1. 專用代理：金鑰、輪詢、回報 ────────────────────────────────────────────

async def test_new_server_gets_its_own_agent_key_and_the_report_lands(client, auth_headers, db_session) -> None:
    r = await client.post("/api/v1/rustdesk/servers", headers=auth_headers, json={
        "name": f"rd-{uuid.uuid4().hex[:6]}", "report_interval_seconds": 120, "client_address": "rd.example.net"})
    assert r.status_code == 201, r.text
    sid, raw = r.json()["id"], r.json()["agent_key"]
    assert len(raw) >= 40 and r.json()["has_agent_key"] is True
    assert r.json()["agent_latest_version"], "畫面要拿得到伺服器上這一版代理的版本"
    listed = (await client.get("/api/v1/rustdesk/servers", headers=auth_headers)).json()["items"]
    assert all("agent_key" not in x for x in listed), "金鑰只在新增的回應裡出現一次"

    poll = await _poll(client, raw, hostname="rd-host", data_dir="/var/lib/rustdesk-server",
                       receiver={"listening": True, "port": 21114, "error": None})
    assert poll.status_code == 200, poll.text
    cfg = poll.json()
    assert cfg["source_id"] == sid and cfg["enabled"] is True and cfg["interval_seconds"] == 120
    assert cfg["api_listen"] is True and cfg["api_port"] == 21114 and cfg["report_now"] is False
    assert cfg["agent_sha"] and cfg["poll_seconds"] >= 5 and cfg["test_id"] is None

    out = await _send(client, raw, _report(sid, [("123456789", "192.0.2.10", True),
                                                 ("987654321", None, False)]))
    assert out["peers"] == 2 and out["online"] == 1
    srv = await db_session.get(RustDeskServer, uuid.UUID(sid))
    await db_session.refresh(srv)
    assert srv.last_error is None and srv.agent_version == "1.0.0" and srv.agent_hostname == "rd-host"
    assert srv.agent_last_seen_at is not None and srv.agent_status["receiver"]["listening"] is True
    assert srv.public_key == PUB and srv.server_version == "1.1.16"
    peers = await _peers(db_session, srv)
    assert peers["123456789"].online is True and peers["123456789"].last_online_at is not None
    assert str(peers["123456789"].registered_ip) == "192.0.2.10"
    assert peers["987654321"].online is False and peers["987654321"].last_online_at is None


async def test_a_key_only_works_for_its_own_server(client, db_session) -> None:
    mine, other = "s" * 40, "t" * 40
    srv = await _server(db_session, mine)
    await _server(db_session, other)
    r = await client.post("/api/v1/rustdesk/agent/report", headers={"X-Agent-Key": other},
                          json=_report(srv.id, [("123456789", "192.0.2.1", True)]))
    assert r.status_code == 409, "別台伺服器的金鑰不可以寫這台的資料"
    r = await client.post("/api/v1/rustdesk/agent/events", headers={"X-Agent-Key": other},
                          json={"source_id": str(srv.id), "dropped": {}, "events": []})
    assert r.status_code == 409
    assert (await _poll(client, "z" * 40)).status_code == 401
    assert (await client.post("/api/v1/rustdesk/agent/poll", json={})).status_code == 401


async def test_disabled_server_keeps_the_agent_polling_but_idle(client, db_session) -> None:
    raw = "u" * 40
    srv = await _server(db_session, raw, enabled=False)
    cfg = (await _poll(client, raw)).json()
    assert cfg["enabled"] is False and cfg["api_listen"] is False, "停用＝不讀也不聽，但照樣輪詢（重新啟用馬上生效）"
    r = await client.post("/api/v1/rustdesk/agent/report", headers={"X-Agent-Key": raw},
                          json=_report(srv.id, [("123456789", "192.0.2.1", True)]))
    assert r.json()["status"] == "disabled"
    assert not await _peers(db_session, srv)


async def test_sync_now_is_handed_to_the_agent_once(client, auth_headers, db_session) -> None:
    raw = "n1" * 20
    srv = await _server(db_session, raw)
    r = await client.post(f"/api/v1/rustdesk/servers/{srv.id}/sync-now", headers=auth_headers)
    assert r.status_code == 202 and r.json()["queued"] is True
    assert r.json()["agent_online"] is False, "代理從沒連上過 → 畫面要說出來，不是假裝排進去了"
    assert (await _poll(client, raw)).json()["report_now"] is True
    assert (await _poll(client, raw)).json()["report_now"] is False, "單次消費"
    srv.enabled = False
    await db_session.commit()
    r = await client.post(f"/api/v1/rustdesk/servers/{srv.id}/sync-now", headers=auth_headers)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "rustdesk_server_disabled"


async def test_test_round_trip(client, auth_headers, db_session) -> None:
    raw = "n2" * 20
    srv = await _server(db_session, raw)
    r = await client.post(f"/api/v1/rustdesk/servers/{srv.id}/test", headers=auth_headers)
    assert r.status_code == 202
    tid = r.json()["test_id"]
    assert tid and r.json()["result_at"] is None and r.json()["checks"] == []
    assert (await _poll(client, raw)).json()["test_id"] == tid
    assert (await _poll(client, raw)).json()["test_id"] == tid, "結果送到之前每次輪詢都帶著"
    stale = await client.post("/api/v1/rustdesk/agent/test-result", headers={"X-Agent-Key": raw},
                              json={"test_id": str(uuid.uuid4()), "checks": []})
    assert stale.json()["status"] == "stale", "舊的測試結果不可以蓋掉這一次的"
    checks = [{"key": "database", "ok": True, "detail": "/var/lib/rustdesk-server/db_v2.sqlite3: 26 IDs"},
              {"key": "receiver", "ok": False, "detail": "TCP 21114: OSError: [Errno 98] Address already in use"}]
    ok = await client.post("/api/v1/rustdesk/agent/test-result", headers={"X-Agent-Key": raw},
                           json={"test_id": tid, "checks": checks})
    assert ok.json()["status"] == "ok"
    st = (await client.get(f"/api/v1/rustdesk/servers/{srv.id}/test", headers=auth_headers)).json()
    assert st["test_id"] == tid and st["result_at"] is not None and st["checks"] == checks
    assert (await _poll(client, raw)).json()["test_id"] is None


async def test_agent_key_can_be_viewed_again_and_rotated(client, auth_headers, db_session) -> None:
    r = await client.post("/api/v1/rustdesk/servers", headers=auth_headers, json={"name": f"rd-{uuid.uuid4().hex[:6]}"})
    sid, raw = r.json()["id"], r.json()["agent_key"]
    again = await client.get(f"/api/v1/rustdesk/servers/{sid}/agent-key", headers=auth_headers)
    assert again.status_code == 200 and again.json()["agent_key"] == raw
    rot = await client.post(f"/api/v1/rustdesk/servers/{sid}/rotate-agent-key", headers=auth_headers)
    new = rot.json()["agent_key"]
    assert new != raw
    assert (await _poll(client, raw)).status_code == 401, "換金鑰後舊的立刻失效"
    assert (await _poll(client, new)).status_code == 200
    from app.models.audit import AuditLog
    acts = set((await db_session.execute(select(AuditLog.action).where(
        AuditLog.object_id == uuid.UUID(sid)))).scalars().all())
    assert {"create", "view_agent_key", "rotate_agent_key"} <= acts, "看過金鑰、換過金鑰都要留稽核"
    await client.delete(f"/api/v1/rustdesk/servers/{sid}", headers=auth_headers)
    assert (await _poll(client, new)).status_code == 401, "刪掉伺服器 → 代理被拒"
    from app.models.encrypted_secret import EncryptedSecret
    left = (await db_session.execute(select(EncryptedSecret).where(
        EncryptedSecret.object_id == uuid.UUID(sid)))).scalars().all()
    assert not left, "金鑰的加密明文要一起刪"


async def test_agent_files_are_downloadable(client) -> None:
    for path in ("/api/v1/rustdesk/agent/agent.py", "/api/v1/rustdesk/agent/installer.sh"):
        r = await client.get(path)
        assert r.status_code == 200 and len(r.text) > 1000, path
    assert "AGENT_VERSION" in (await client.get("/api/v1/rustdesk/agent/agent.py")).text


async def test_client_address_is_validated(client, auth_headers, db_session) -> None:
    for bad in ["rd.example.net/evil", "javascript:alert(1)", "a b", "x" * 300]:
        r = await client.post("/api/v1/rustdesk/servers", headers=auth_headers, json={
            "name": f"rd-{uuid.uuid4().hex[:6]}", "client_address": bad})
        assert r.status_code == 422, bad
    r = await client.post("/api/v1/rustdesk/servers", headers=auth_headers, json={
        "name": f"rd-{uuid.uuid4().hex[:6]}", "client_address": "rd.example.net:21116"})
    assert r.status_code == 201, r.text


async def test_writes_need_admin(client, db_session) -> None:
    from app.core.security import hash_password
    from app.models.user import User
    from app.services.auth import issue_access_token
    u = User(username=f"viewer-{uuid.uuid4().hex[:8]}", email=f"v-{uuid.uuid4().hex[:8]}@test.local",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True,
             is_admin=False)
    db_session.add(u)
    await db_session.commit()
    hdr = {"Authorization": f"Bearer {issue_access_token(u)}"}
    r = await client.post("/api/v1/rustdesk/servers", headers=hdr, json={"name": "rd-viewer"})
    assert r.status_code == 403
    srv = await _server(db_session, "x" * 40)
    for method, path in (("get", f"/api/v1/rustdesk/servers/{srv.id}/agent-key"),
                         ("post", f"/api/v1/rustdesk/servers/{srv.id}/sync-now"),
                         ("post", f"/api/v1/rustdesk/servers/{srv.id}/test"),
                         ("post", f"/api/v1/rustdesk/servers/{srv.id}/rotate-agent-key")):
        assert (await getattr(client, method)(path, headers=hdr)).status_code == 403, path


# ── 2. 資料生命週期 ───────────────────────────────────────────────────────

async def test_a_device_removed_from_hbbs_is_removed_here(client, db_session) -> None:
    raw = "y" * 40
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.1", True), ("222222222", "192.0.2.2", True)]))
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.1", True)]))
    assert set(await _peers(db_session, srv)) == {"111111111"}


async def test_unreadable_database_does_not_wipe_what_was_there(client, db_session) -> None:
    raw = "z" * 40
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.1", True)]))
    bad = _report(srv.id, [], db_ok=False)
    await _send(client, raw, bad)
    assert set(await _peers(db_session, srv)) == {"111111111"}
    await db_session.refresh(srv)
    assert "unable to open database file" in (srv.last_error or "")


async def test_truncated_report_does_not_delete(client, db_session) -> None:
    raw = "a1" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.1", True), ("222222222", "192.0.2.2", True)]))
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.1", True)], truncated=True))
    assert set(await _peers(db_session, srv)) == {"111111111", "222222222"}


async def test_online_query_failure_keeps_the_previous_state(client, db_session) -> None:
    raw = "b1" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.1", True)]))
    before = (await _peers(db_session, srv))["111111111"].last_online_at
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.1", True)], online_ok=False))
    p = (await _peers(db_session, srv))["111111111"]
    assert p.online is True and p.last_online_at == before, "查不到線上狀態 ≠ 離線"
    await db_session.refresh(srv)
    assert "Connection refused" in (srv.last_error or "")


async def test_deleting_the_server_takes_its_devices_and_mappings(client, auth_headers, db_session) -> None:
    sub = await _subnet(db_session, "192.0.2.0/24")
    ip = await _ip(db_session, sub, "192.0.2.10")
    raw = "c1" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.10", True)]))
    r = await client.delete(f"/api/v1/rustdesk/servers/{srv.id}", headers=auth_headers)
    assert r.status_code == 204, r.text
    assert await _peers(db_session, srv) == {}
    d = await client.get(f"/api/v1/addresses/{ip.id}", headers=auth_headers)
    assert d.json().get("rustdesk") is None


async def test_a_silent_agent_marks_the_server_as_failing(db_session) -> None:
    from app.services.rustdesk import mark_stale_rustdesk
    srv = await _server(db_session, "d1" * 20, report_interval_seconds=300,
                        last_report_at=datetime.now(UTC) - timedelta(minutes=30))
    assert await mark_stale_rustdesk(db_session) >= 1
    assert "no report from the RustDesk agent" in (srv.last_error or "")


# ── 3. 對應到 IP 記錄 ─────────────────────────────────────────────────────

async def test_online_device_on_a_unique_ip_is_matched(client, db_session) -> None:
    sub = await _subnet(db_session, "192.0.2.0/24")
    ip = await _ip(db_session, sub, "192.0.2.20")
    raw = "e1" * 20
    srv = await _server(db_session, raw)
    out = await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.20", True)]))
    assert out["matched"] == 1
    p = (await _peers(db_session, srv))["111111111"]
    assert p.address_id == ip.id and p.match_status == "matched"


async def test_device_never_seen_online_is_not_matched(client, db_session) -> None:
    sub = await _subnet(db_session, "192.0.2.0/24")
    await _ip(db_session, sub, "192.0.2.21")
    raw = "f1" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.21", False)]))
    p = (await _peers(db_session, srv))["111111111"]
    assert p.address_id is None and p.match_status == "not_seen_online"


async def test_mapping_survives_short_offline_but_not_a_week(client, db_session) -> None:
    sub = await _subnet(db_session, "192.0.2.0/24")
    ip = await _ip(db_session, sub, "192.0.2.22")
    raw = "g1" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.22", True)]))
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.22", False)]))
    p = (await _peers(db_session, srv))["111111111"]
    assert p.address_id == ip.id, "關機一下子不該讓對應消失"
    p.last_online_at = datetime.now(UTC) - timedelta(days=8)
    await db_session.commit()
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.22", False)]))
    p = (await _peers(db_session, srv))["111111111"]
    assert p.address_id is None and p.match_status == "stale", "一週沒上線：那個位址可能早就給別台了"


async def test_many_ids_on_one_ip_is_nat_and_not_matched(client, db_session) -> None:
    sub = await _subnet(db_session, "192.0.2.0/24")
    await _ip(db_session, sub, "192.0.2.1")
    raw = "h1" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [(f"{i}11111111", "192.0.2.1", True) for i in range(1, 4)]))
    peers = await _peers(db_session, srv)
    assert all(p.address_id is None and p.match_status == "shared" for p in peers.values())


async def test_old_never_online_ids_do_not_make_an_ip_shared(client, db_session) -> None:
    """2026-10-05 正式環境：一個位址底下 6 個 ID、另一個底下 4 個，全是 2023～2024 年的舊註冊（那時的 DHCP 主人
    重裝過 RustDesk），jt-ipam 從沒看過它們上線，卻都標「多台共用」。只算最近看過上線的 ID；舊的標「尚未看到上線」。"""
    sub = await _subnet(db_session, "192.0.2.0/24")
    await _ip(db_session, sub, "192.0.2.71")
    raw = "h9" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [(f"{i}71717171", "192.0.2.71", False) for i in range(1, 7)]))
    peers = await _peers(db_session, srv)
    assert {p.match_status for p in peers.values()} == {"not_seen_online"}
    # 其中一台回來上線 → 它照常對應（其他舊的不算共用）
    await _send(client, raw, _report(srv.id, [(f"{i}71717171", "192.0.2.71", i == 1) for i in range(1, 7)]))
    peers = await _peers(db_session, srv)
    assert peers["171717171"].match_status == "matched"


async def test_two_ids_on_one_ip_match_only_the_online_one(client, db_session) -> None:
    sub = await _subnet(db_session, "192.0.2.0/24")
    ip = await _ip(db_session, sub, "192.0.2.30")
    raw = "i1" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.30", True), ("222222222", "192.0.2.30", True)]))
    peers = await _peers(db_session, srv)
    assert all(p.address_id is None and p.match_status == "ambiguous" for p in peers.values()), "兩個都在線 → 不猜"
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.30", True), ("222222222", "192.0.2.30", False)]))
    peers = await _peers(db_session, srv)
    assert peers["111111111"].address_id == ip.id
    assert peers["222222222"].address_id is None


async def test_overlapping_subnets_are_not_guessed(client, db_session) -> None:
    s1, s2 = await _subnet(db_session, "10.9.0.0/24"), await _subnet(db_session, "10.9.0.0/24")
    await _ip(db_session, s1, "10.9.0.5")
    await _ip(db_session, s2, "10.9.0.5")
    raw = "j1" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "10.9.0.5", True)]))
    p = (await _peers(db_session, srv))["111111111"]
    assert p.address_id is None and p.match_status == "ambiguous"


async def test_unmanaged_ip_is_listed_but_not_matched(client, db_session) -> None:
    raw = "k1" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "203.0.113.50", True)]))
    p = (await _peers(db_session, srv))["111111111"]
    assert p.address_id is None and p.match_status == "unmanaged"


# ── 4. IP 詳細資料與連線網址 ──────────────────────────────────────────────

async def test_ip_detail_has_the_id_and_a_connect_link_without_password(client, auth_headers, db_session) -> None:
    sub = await _subnet(db_session, "192.0.2.0/24")
    ip = await _ip(db_session, sub, "192.0.2.40", rustdesk_enabled=True)
    raw = "l1" * 20
    srv = await _server(db_session, raw, client_address="rd.example.net")
    await _send(client, raw, _report(srv.id, [("123456789", "192.0.2.40", True)]))
    d = await client.get(f"/api/v1/addresses/{ip.id}", headers=auth_headers)
    rd = d.json()["rustdesk"]
    assert rd["id"] == "123456789" and rd["online"] is True and rd["server_name"] == srv.name
    assert rd["connect_uri"] == f"rustdesk://connect/123456789@rd.example.net?key={PUB}"
    assert "password" not in rd["connect_uri"]


async def test_connect_button_needs_the_per_ip_switch(client, auth_headers, db_session) -> None:
    """比照 SSH／RDP／VNC：IP 編輯裡勾了「啟用 RustDesk 連線」才給網址（管理員也一樣）；ID 照樣看得到。"""
    sub = await _subnet(db_session, "192.0.2.0/24")
    ip = await _ip(db_session, sub, "192.0.2.43")
    raw = "l2" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("123456780", "192.0.2.43", True)]))
    await _events(client, raw, srv.id, [_ev("heartbeat", "123456780", src_ip="192.0.2.43", ver=1004001, conns=0)])
    d = (await client.get(f"/api/v1/addresses/{ip.id}", headers=auth_headers)).json()
    assert d["rustdesk_enabled"] is False
    assert d["rustdesk"]["id"] == "123456780" and d["rustdesk"]["connect_uri"] is None
    assert d["rustdesk"]["last_heartbeat_at"], "「各來源最後出現」要列 RustDesk 的最後心跳"
    r = await client.patch(f"/api/v1/addresses/{ip.id}", headers=auth_headers, json={"rustdesk_enabled": True})
    assert r.status_code == 200, r.text
    d = (await client.get(f"/api/v1/addresses/{ip.id}", headers=auth_headers)).json()
    assert d["rustdesk_enabled"] is True and d["rustdesk"]["connect_uri"] == "rustdesk://connect/123456780"


async def test_connect_link_without_client_address_uses_the_clients_default_server(
        client, auth_headers, db_session) -> None:
    sub = await _subnet(db_session, "192.0.2.0/24")
    ip = await _ip(db_session, sub, "192.0.2.41", rustdesk_enabled=True)
    raw = "m1" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("123456789", "192.0.2.41", True)]))
    d = await client.get(f"/api/v1/addresses/{ip.id}", headers=auth_headers)
    assert d.json()["rustdesk"]["connect_uri"] == "rustdesk://connect/123456789"


# ── 5. 清單 ───────────────────────────────────────────────────────────────

async def test_peer_list_filters_and_pages(client, auth_headers, db_session) -> None:
    raw = "n1" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [(f"{100000000 + i}", f"198.51.100.{i}", i % 2 == 0)
                                              for i in range(1, 31)]))
    r = await client.get(f"/api/v1/rustdesk/servers/{srv.id}/peers?online=true&page_size=10",
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 15 and len(body["items"]) == 10
    r = await client.get(f"/api/v1/rustdesk/servers/{srv.id}/peers?q=100000007", headers=auth_headers)
    assert [i["rustdesk_id"] for i in r.json()["items"]] == ["100000007"]
    r = await client.get(f"/api/v1/rustdesk/servers/{srv.id}/peers?q=198.51.100.12", headers=auth_headers)
    assert [i["rustdesk_id"] for i in r.json()["items"]] == ["100000012"]


async def test_peer_list_sorts_on_the_server(client, auth_headers, db_session) -> None:
    """裝置清單是伺服器端分頁：排序也要在伺服器端做，否則只排到目前這一頁。"""
    raw = "s1" * 20
    srv = await _server(db_session, raw)
    # 999999999 比 1000000001 短：ID 要照數字順序，不是字典序；IP 要照位址順序（.9 在 .10 前面）
    await _send(client, raw, _report(srv.id, [("1000000001", "198.51.100.10", True), ("999999999", "198.51.100.9", True),
                                              ("1000000002", None, False)]))
    await _events(client, raw, srv.id, [_ev("sysinfo", "1000000001", hostname="zeta-pc"),
                                        _ev("sysinfo", "999999999", hostname="alpha-pc")])
    base = f"/api/v1/rustdesk/servers/{srv.id}/peers"

    async def ids(qs: str) -> list[str]:
        r = await client.get(f"{base}?{qs}", headers=auth_headers)
        assert r.status_code == 200, r.text
        return [i["rustdesk_id"] for i in r.json()["items"]]

    assert await ids("sort=rustdesk_id&order=asc") == ["999999999", "1000000001", "1000000002"]
    assert await ids("sort=rustdesk_id&order=desc") == ["1000000002", "1000000001", "999999999"]
    assert await ids("sort=hostname&order=asc") == ["999999999", "1000000001", "1000000002"], "沒有值的排最後"
    assert await ids("sort=hostname&order=desc") == ["1000000001", "999999999", "1000000002"], "降冪也是沒有值的排最後"
    assert await ids("sort=registered_ip&order=asc") == ["999999999", "1000000001", "1000000002"]
    assert (await ids("sort=online&order=desc"))[-1] == "1000000002", "離線的（沒有任何回報）排在最後"
    assert (await ids("sort=online&order=asc"))[0] == "1000000002"
    bad = await client.get(f"{base}?sort=uuid", headers=auth_headers)
    assert bad.status_code == 422, "只接受允許清單欄位"


async def test_audit_list_sorts_on_the_server(client, auth_headers, db_session) -> None:
    raw = "s2" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "198.51.100.1", True)]))
    await _events(client, raw, srv.id, [
        _ev("conn", "111111111", nonce="n-a", conn_id=1, action="new", ip="198.51.100.200"),
        _ev("conn", "111111111", nonce="n-b", conn_id=1, peer_id="222", peer_name="bob", type=0),
        _ev("conn", "111111111", nonce="n-c", conn_id=1, peer_id="333", peer_name="alice", type=0)])
    base = f"/api/v1/rustdesk/servers/{srv.id}/audit"
    r = await client.get(f"{base}?sort=peer_name&order=asc", headers=auth_headers)
    assert [i["peer_name"] for i in r.json()["items"]] == ["alice", "bob", None]
    r = await client.get(f"{base}?sort=peer_name&order=desc", headers=auth_headers)
    assert [i["peer_name"] for i in r.json()["items"]] == ["bob", "alice", None]
    assert (await client.get(f"{base}?sort=nonce", headers=auth_headers)).status_code == 422


async def test_connect_link_needs_remote_console_rights(client, db_session) -> None:
    """看得到這個 IP 就看得到 RustDesk ID；連線網址比照遠端主控台權限（admin、子網路可寫、或 can_ssh）。"""
    from app.core.security import hash_password
    from app.models.permission import Permission
    from app.models.user import User
    from app.services.auth import issue_access_token

    sub = await _subnet(db_session, "192.0.2.0/24")
    ip = await _ip(db_session, sub, "192.0.2.42", rustdesk_enabled=True)
    raw = "o1" * 20
    srv = await _server(db_session, raw, client_address="rd.example.net")
    await _send(client, raw, _report(srv.id, [("123456789", "192.0.2.42", True)]))

    async def reader(can_ssh: bool) -> dict:
        u = User(username=f"u-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local",
                 password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True,
                 is_admin=False, can_ssh=can_ssh)
        db_session.add(u)
        await db_session.flush()
        db_session.add(Permission(object_type="subnet", object_id=sub.id, principal_type="user",
                                  principal_id=u.id, level="read"))
        await db_session.commit()
        r = await client.get(f"/api/v1/addresses/{ip.id}",
                             headers={"Authorization": f"Bearer {issue_access_token(u)}"})
        assert r.status_code == 200, r.text
        return r.json()["rustdesk"]

    plain = await reader(False)
    assert plain["id"] == "123456789" and plain["connect_uri"] is None
    console = await reader(True)
    assert console["connect_uri"].startswith("rustdesk://connect/123456789@rd.example.net")


# ── 6. AI 對話工具 ────────────────────────────────────────────────────────

async def test_ai_tool_lists_peers_and_scopes_by_subnet(client, db_session, admin_user) -> None:
    from app.mcp.tools import get_ip_detail, list_rustdesk_peers

    s1, s2 = await _subnet(db_session, "192.0.2.0/24"), await _subnet(db_session, "198.51.100.0/24")
    await _ip(db_session, s1, "192.0.2.50", hostname="pc-50")
    await _ip(db_session, s2, "198.51.100.60", hostname="pc-60")
    raw = "p1" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.50", True),
                                              ("222222222", "198.51.100.60", False),
                                              ("333333333", "203.0.113.9", True)]))
    everything = await list_rustdesk_peers(db_session, user=admin_user)
    assert everything["count"] == 3
    scoped = await list_rustdesk_peers(db_session, user=admin_user, subnet_cidr="192.0.2.0/24")
    assert scoped["count"] == 1 and scoped["peers"][0]["hostname"] == "pc-50"
    assert "192.0.2.0/24" in scoped["scope"]
    online = await list_rustdesk_peers(db_session, user=admin_user, online=True)
    assert {p["rustdesk_id"] for p in online["peers"]} == {"111111111", "333333333"}
    detail = await get_ip_detail(db_session, user=admin_user, ip="192.0.2.50")
    assert detail["rustdesk"]["id"] == "111111111" and "connect_uri" not in detail["rustdesk"]


# ── 7. 客戶端回報（心跳／系統資訊／稽核，docs/SPEC_RUSTDESK_API_zh-TW.md）與多訊號對應 ──

def _ev(kind: str, rid: str, **kw) -> dict:
    from datetime import datetime as _dt
    return {"kind": kind, "at": _dt.now(UTC).isoformat(), "src_ip": kw.pop("src_ip", "192.0.2.250"), "id": rid, **kw}


async def _events(client, raw, sid, events, dropped=None) -> dict:
    r = await client.post("/api/v1/rustdesk/agent/events", headers={"X-Agent-Key": raw},
                          json={"source_id": str(sid), "dropped": dropped or {}, "events": events})
    assert r.status_code == 200, r.text
    return r.json()


async def test_poll_tells_the_agent_whether_to_listen(client, db_session) -> None:
    raw = "q1" * 20
    srv = await _server(db_session, raw, api_port=21115)
    cfg = (await _poll(client, raw)).json()
    assert cfg["api_listen"] is True and cfg["api_port"] == 21115
    srv.receive_reports = False
    await db_session.commit()
    assert (await _poll(client, raw)).json()["api_listen"] is False, "網頁上關掉 → 代理不聽那個埠"


async def test_sysinfo_and_heartbeat_update_the_device(client, db_session) -> None:
    raw = "q2" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "198.51.100.9", False)]))
    await _events(client, raw, srv.id, [
        _ev("sysinfo", "111111111", hostname="PC-71", username="alice", os="Windows 11 Pro", cpu="i5 6/4 cores",
            memory="16GB", version="1.5.0"),
        _ev("heartbeat", "111111111", src_ip="198.51.100.71", ver=1005000, conns=2),
    ])
    p = (await _peers(db_session, srv))["111111111"]
    assert (p.hostname, p.username, p.os_name, p.client_version) == ("PC-71", "alice", "Windows 11 Pro", "1.5.0")
    assert p.active_conns == 2 and p.last_heartbeat_at is not None
    assert str(p.report_ip) == "198.51.100.71"
    assert p.online is True and p.last_online_at is not None, "心跳＝這一刻在線"


async def test_dropped_counters_accumulate(client, db_session) -> None:
    raw = "q5" * 20
    srv = await _server(db_session, raw)
    await _events(client, raw, srv.id, [], dropped={"unverified": 2, "rate_limited": 1})
    await _events(client, raw, srv.id, [], dropped={"unverified": 3})
    await db_session.refresh(srv)
    assert srv.events_dropped["unverified"] == 5 and srv.events_dropped["rate_limited"] == 1
    assert srv.last_events_at is not None


async def test_connection_audit_is_stored_once_per_nonce_and_listed(client, auth_headers, db_session) -> None:
    sub = await _subnet(db_session, "192.0.2.0/24")
    await _ip(db_session, sub, "192.0.2.72", hostname="pc-72")
    raw = "q6" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "192.0.2.72", True)]))
    big = str(2**60 + 7)
    evs = [
        _ev("conn", "111111111", nonce="n-1", conn_id=7, session_id="0", action="new", ip="203.0.113.5"),
        _ev("conn", "111111111", nonce="n-2", conn_id=7, session_id=big, action="auth", peer_id="999888777",
            peer_name="helpdesk", type=0, ip=None),
        _ev("file", "111111111", nonce="n-3", conn_id=7, peer_id="999888777", type=0, path="C:/Users/a",
            is_file=True, ip="203.0.113.5", peer_name="helpdesk", num=1, files=[["a.txt", 123]]),
        _ev("conn", "111111111", nonce="n-4", conn_id=7, session_id=big, action="close"),
    ]
    await _events(client, raw, srv.id, evs)
    await _events(client, raw, srv.id, evs[:2])                    # 重送 → 不會多出來
    r = await client.get(f"/api/v1/rustdesk/servers/{srv.id}/audit", headers=auth_headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 4
    auth = next(i for i in items if i["action"] == "auth")
    assert auth["peer_id"] == "999888777" and auth["peer_name"] == "helpdesk" and auth["session_id"] == big
    assert auth["device_ip"] == "192.0.2.72" and auth["device_hostname"] == "pc-72", "受控端對應到的 IP 一起帶"
    f = next(i for i in items if i["kind"] == "file")
    assert f["detail"]["files"] == [["a.txt", 123]] and f["detail"]["path"] == "C:/Users/a"
    r = await client.get(f"/api/v1/rustdesk/servers/{srv.id}/audit?kind=file", headers=auth_headers)
    assert [i["kind"] for i in r.json()["items"]] == ["file"]
    r = await client.get(f"/api/v1/rustdesk/servers/{srv.id}/audit?q=helpdesk", headers=auth_headers)
    assert {i["nonce"] for i in r.json()["items"]} == {"n-2", "n-3"}


async def test_brute_force_alarm_notifies_admins_once(client, db_session, admin_user) -> None:
    from app.models.notification import Notification
    raw = "q7" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "198.51.100.9", True)]))
    alarm = lambda n: _ev("alarm", "111111111", nonce=n, conn_id=3, typ=2,      # noqa: E731
                          info={"ip": "203.0.113.66", "id": "555444333", "name": "attacker"})
    await _events(client, raw, srv.id, [alarm("a-1")])
    await _events(client, raw, srv.id, [alarm("a-2")])              # 10 分鐘內同一台同一類 → 不再通知
    notes = (await db_session.execute(select(Notification).where(
        Notification.user_id == admin_user.id, Notification.link.like("/rustdesk%")))).scalars().all()
    assert len(notes) == 1
    assert "203.0.113.66" in (notes[0].body or "")


async def test_report_ip_resolves_a_nat_shared_registered_ip(client, db_session) -> None:
    """登記 IP 是 NAT 共用的（3 個 ID 同一個 IP），但心跳直接從內網送來，來源位址是唯一的。"""
    sub = await _subnet(db_session, "192.0.2.0/24")
    ip = await _ip(db_session, sub, "192.0.2.80")
    raw = "q8" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [(f"{i}22222222", "192.0.2.1", True) for i in range(1, 4)]))
    await _events(client, raw, srv.id, [_ev("heartbeat", "122222222", src_ip="192.0.2.80", ver=1, conns=None)])
    p = (await _peers(db_session, srv))["122222222"]
    assert p.address_id == ip.id and p.match_status == "matched"
    assert "report_ip" in (p.match_evidence or [])


async def test_hostname_confirms_or_contradicts_the_ip(client, db_session) -> None:
    sub = await _subnet(db_session, "192.0.2.0/24")
    agree = await _ip(db_session, sub, "192.0.2.81", hostname="pc-81.corp.example")
    await _ip(db_session, sub, "192.0.2.82", hostname="printer-82")
    raw = "q9" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("181818181", "192.0.2.81", True), ("182828282", "192.0.2.82", True)]))
    await _events(client, raw, srv.id, [_ev("sysinfo", "181818181", hostname="PC-81", os="Windows 11"),
                                        _ev("sysinfo", "182828282", hostname="pc-82", os="Windows 11")])
    peers = await _peers(db_session, srv)
    ok = peers["181818181"]
    assert ok.address_id == agree.id and set(ok.match_evidence) == {"registered_ip", "hostname"}
    bad = peers["182828282"]
    assert bad.address_id is None and bad.match_status == "conflict", \
        "那個 IP 現在是印表機：DHCP 位址換了主人，不可以關聯"


async def test_fresh_heartbeat_ip_wins_over_a_different_record_name(client, db_session) -> None:
    """正式環境實例：心跳就是從這個位址送來的，IP 記錄寫 desk-22、客戶端回報 ws-ud22 → 以位址為準。"""
    sub = await _subnet(db_session, "192.0.2.0/24")
    rec = await _ip(db_session, sub, "192.0.2.83", hostname="desk-22")
    raw = "qa" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("183838383", "192.0.2.83", True)]))
    await _events(client, raw, srv.id, [
        _ev("sysinfo", "183838383", src_ip="192.0.2.83", hostname="ws-ud22", os="Ubuntu 22.04"),
        _ev("heartbeat", "183838383", src_ip="192.0.2.83", ver=1004001, conns=0)])
    p = (await _peers(db_session, srv))["183838383"]
    assert p.address_id == rec.id and p.match_status == "matched", "心跳剛從這個位址送來：名稱不同不擋"
    assert set(p.match_evidence) == {"registered_ip", "report_ip"}


async def test_reported_hostname_feeds_the_ip_record_last(client, auth_headers, db_session) -> None:
    """客戶端回報的名稱當成 IP 主機名稱的來源（優先序最後）：沒有名稱的 IP 會用它，已有別的來源就不蓋掉；
    不再對應時收回；刪掉伺服器也收回。"""
    from app.models.ip_hostname import IPHostnameObservation
    from app.services.hostname import apply_observation
    sub = await _subnet(db_session, "192.0.2.0/24")
    bare = await _ip(db_session, sub, "192.0.2.91")
    named = await _ip(db_session, sub, "192.0.2.92")
    await apply_observation(db_session, ip=named, source="dns", hostname="pc-92.corp.example")
    await db_session.commit()
    raw = "qb" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("191919191", "192.0.2.91", True), ("192929292", "192.0.2.92", True),
                                              ("193939393", "192.0.2.93", True)]))
    await _events(client, raw, srv.id, [
        _ev("sysinfo", "191919191", src_ip="192.0.2.91", hostname="lab-pc-91"),
        _ev("sysinfo", "192929292", src_ip="192.0.2.92", hostname="pc-92"),
        _ev("sysinfo", "193939393", src_ip="192.0.2.93", hostname="ubuntu")])
    await db_session.refresh(bare)
    await db_session.refresh(named)
    assert bare.hostname == "lab-pc-91", "沒有名稱的 IP 用客戶端回報的"
    assert named.hostname == "pc-92.corp.example", "已有 DNS 名稱：RustDesk 排最後，不蓋掉"
    obs = (await db_session.execute(select(IPHostnameObservation).where(
        IPHostnameObservation.source == "rustdesk"))).scalars().all()
    assert {o.hostname for o in obs} == {"lab-pc-91", "pc-92"}, "ubuntu 這類預設名不收"

    # hbbs 刪掉這台 → 不再對應 → 名稱收回
    await _send(client, raw, _report(srv.id, [("192929292", "192.0.2.92", True)]))
    await db_session.refresh(bare)
    assert bare.hostname is None
    r = await client.delete(f"/api/v1/rustdesk/servers/{srv.id}", headers=auth_headers)
    assert r.status_code == 204
    left = (await db_session.execute(select(IPHostnameObservation).where(
        IPHostnameObservation.source == "rustdesk").execution_options(populate_existing=True))).scalars().all()
    assert not left, "刪掉伺服器要收回它回報的名稱"


async def test_generic_hostname_is_not_evidence(client, db_session) -> None:
    sub = await _subnet(db_session, "192.0.2.0/24")
    ip = await _ip(db_session, sub, "192.0.2.83", hostname="pc-83")
    raw = "r1" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("183838383", "192.0.2.83", True)]))
    await _events(client, raw, srv.id, [_ev("sysinfo", "183838383", hostname="localhost")])
    p = (await _peers(db_session, srv))["183838383"]
    assert p.address_id == ip.id and p.match_evidence == ["registered_ip"]


async def test_hostname_alone_is_only_a_suggestion(client, db_session) -> None:
    """在家用公網 IP 的公司電腦：主機名稱對得上辦公室那筆，但只當建議、不關聯。"""
    sub = await _subnet(db_session, "192.0.2.0/24")
    office = await _ip(db_session, sub, "192.0.2.84", hostname="laptop-84")
    raw = "r2" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("184848484", "203.0.113.84", True)]))
    await _events(client, raw, srv.id, [_ev("sysinfo", "184848484", hostname="LAPTOP-84")])
    p = (await _peers(db_session, srv))["184848484"]
    assert p.address_id is None and p.match_status == "hostname_only"
    assert p.candidate_address_id == office.id


async def test_stale_report_ip_is_not_evidence(client, db_session) -> None:
    sub = await _subnet(db_session, "192.0.2.0/24")
    await _ip(db_session, sub, "192.0.2.85")
    raw = "r3" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("185858585", "203.0.113.85", False)]))
    await _events(client, raw, srv.id, [_ev("heartbeat", "185858585", src_ip="192.0.2.85", ver=1)])
    p = (await _peers(db_session, srv))["185858585"]
    p.report_ip_at = datetime.now(UTC) - timedelta(days=8)
    p.last_online_at = datetime.now(UTC) - timedelta(days=8)
    await db_session.commit()
    await _send(client, raw, _report(srv.id, [("185858585", "203.0.113.85", False)]))
    p = (await _peers(db_session, srv))["185858585"]
    assert p.address_id is None


async def test_one_bad_event_does_not_reject_the_batch(client, db_session) -> None:
    """代理送失敗會保留佇列重送；整批 422 會讓同一批永遠重送。壞的那筆跳過、計數，其他照收。"""
    raw = "r4" * 20
    srv = await _server(db_session, raw)
    out = await _events(client, raw, srv.id, [
        _ev("sysinfo", "111111111", hostname="pc-91"),
        {"kind": "bogus", "id": "111111111"},
        _ev("conn", "bad id with spaces", nonce="x", action="close"),
    ])
    assert out["rejected"] == 2 and out["sysinfo"] == 1
    await db_session.refresh(srv)
    assert srv.events_dropped["rejected"] == 2


async def test_device_page_shows_the_rustdesk_card_without_a_connect_link(client, auth_headers, db_session) -> None:
    """RustDesk 客戶端裝在機器上（跟 Wazuh／OCS 代理同一類）：裝置頁也要看得到；連線按鈕只在 IP 頁。"""
    from app.models.device import Device
    sub = await _subnet(db_session, "192.0.2.0/24")
    ip = await _ip(db_session, sub, "192.0.2.150", rustdesk_enabled=True)
    dev = Device(name=f"dev-{uuid.uuid4().hex[:6]}", primary_ip_id=ip.id)
    db_session.add(dev)
    await db_session.flush()
    ip.device_id = dev.id
    await db_session.commit()
    raw = "dv" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("150150150", "192.0.2.150", True)]))
    await _events(client, raw, srv.id, [_ev("sysinfo", "150150150", src_ip="192.0.2.150", hostname="pc-150",
                                            username="bob", os="windows / Windows 11 Pro", version="1.4.1")])
    r = await client.get(f"/api/v1/devices/{dev.id}/integrations", headers=auth_headers)
    assert r.status_code == 200, r.text
    rd = r.json()["rustdesk"]
    assert rd["id"] == "150150150" and rd["ip"] == "192.0.2.150" and rd["address_id"] == str(ip.id)
    assert rd["username"] == "bob" and "connect_uri" not in rd


async def test_peer_list_carries_the_mapped_ips_device_type_and_sorts_by_it(client, auth_headers, db_session) -> None:
    """設備類型欄（使用者：與 IP 有關的清單都要能選設備類型）：裝置清單帶對應 IP 的設備類型與型號，也能依它排序。"""
    sub = await _subnet(db_session, "192.0.2.0/24")
    cam = await _ip(db_session, sub, "192.0.2.161", device_kind="camera", device_model="Foscam FI9800P")
    pc = await _ip(db_session, sub, "192.0.2.162", device_kind="windows")
    raw = "dk" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("161161161", "192.0.2.161", True), ("162162162", "192.0.2.162", True),
                                              ("163163163", "203.0.113.5", True)]))
    r = await client.get(f"/api/v1/rustdesk/servers/{srv.id}/peers?sort=address_device_kind&order=asc",
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["rustdesk_id"] for i in items] == ["161161161", "162162162", "163163163"], "沒有類型的排最後"
    assert (items[0]["address_device_kind"], items[0]["address_device_model"]) == ("camera", "Foscam FI9800P")
    assert items[1]["address_device_kind"] == "windows" and items[2]["address_device_kind"] is None
    assert cam.id and pc.id



# ── 同一台裝置的另一個 IP ─────────────────────────────────────────────────

async def test_ip_without_peer_points_to_the_same_devices_ip_that_has_one(client, auth_headers, db_session) -> None:
    """一台電腦兩張網卡（實機 win10-desk-01：兩個位址同時上線），RustDesk 只會從其中一個位址連出去，
    所以只對應得到那一個；另一個 IP 的編輯畫面原本什麼都沒有，看起來像選項不見了（使用者 2026-10-05 問）。
    兩筆 IP 掛在同一台裝置上才算同一台；只是主機名稱相同不算（DHCP 回收後舊名字還留著）。"""
    from app.models.device import Device
    sub = await _subnet(db_session, "192.0.2.0/24")
    dev = Device(name="pc-two-nics")
    db_session.add(dev)
    await db_session.flush()
    a = await _ip(db_session, sub, "192.0.2.67", device_id=dev.id, hostname="pc-two-nics")
    b = await _ip(db_session, sub, "192.0.2.68", device_id=dev.id, rustdesk_enabled=True)
    lone = await _ip(db_session, sub, "192.0.2.69", hostname="pc-two-nics")   # 同名但沒掛裝置
    raw = "sd" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("123456788", "192.0.2.68", True)]))

    d = (await client.get(f"/api/v1/addresses/{a.id}", headers=auth_headers)).json()
    assert d["rustdesk"] is None
    assert d["rustdesk_elsewhere"] == [{"address_id": str(b.id), "ip": "192.0.2.68", "rustdesk_id": "123456788",
                                        "enabled": True}]
    d = (await client.get(f"/api/v1/addresses/{b.id}", headers=auth_headers)).json()
    assert d["rustdesk"]["id"] == "123456788" and d["rustdesk_elsewhere"] == []
    d = (await client.get(f"/api/v1/addresses/{lone.id}", headers=auth_headers)).json()
    assert d["rustdesk_elsewhere"] == [], "只有主機名稱相同不能當成同一台"


async def test_same_device_hint_does_not_reveal_ips_the_user_cannot_see(client, db_session) -> None:
    """另一個 IP 在使用者沒有權限的子網路：不能因為這個提示知道它的位址。"""
    from app.core.security import hash_password
    from app.models.device import Device
    from app.models.permission import Permission
    from app.models.user import User
    from app.services.auth import issue_access_token
    mine = await _subnet(db_session, "192.0.2.0/24")
    other = await _subnet(db_session, "198.51.100.0/24")
    dev = Device(name="pc-split")
    db_session.add(dev)
    await db_session.flush()
    a = await _ip(db_session, mine, "192.0.2.70", device_id=dev.id)
    await _ip(db_session, other, "198.51.100.70", device_id=dev.id)
    raw = "se" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("123456787", "198.51.100.70", True)]))
    u = User(username=f"u-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local", display_name="U",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True)
    db_session.add(u)
    await db_session.flush()
    db_session.add(Permission(object_type="subnet", object_id=mine.id, principal_type="user",
                              principal_id=u.id, level="read"))
    await db_session.commit()
    r = await client.get(f"/api/v1/addresses/{a.id}", headers={"Authorization": f"Bearer {issue_access_token(u)}"})
    assert r.status_code == 200, r.text
    assert r.json()["rustdesk_elsewhere"] == []


# ── 受控端 Key 設錯：代理從 hbbr／hbbs 日誌看到 invalid key ─────────────────────────

def _t8(minutes: int) -> str:
    """日誌時間（+08:00）：以「昨天這個時候」為基準加減分鐘。不可寫死日期 —— key 問題只算 30 天內的，
    寫死 2026-10-05 的版本在 11-04 起就整批失敗（2026-11-04）。"""
    base = (datetime.now(UTC) - timedelta(days=1)).replace(microsecond=0)
    return (base + timedelta(minutes=minutes)).astimezone(timezone(timedelta(hours=8))).isoformat()


def _kc(fails=(), ok=(), logs=None) -> dict:
    return {"fails": list(fails), "ok": list(ok), "dropped": 0,
            "logs": logs or {"dir": "/var/log/rustdesk-server", "hbbr": True, "hbbs": True, "error": None}}


async def test_key_failure_flags_the_only_peer_at_that_ip(client, auth_headers, db_session) -> None:
    """Key 錯的客戶端照樣註冊、照樣回報、區網直連也正常，只有走中繼被 hbbr 拒絕（2026-10-05 實機）。
    代理把日誌裡被拒的 IP 送上來，同一台伺服器上只有一個 RustDesk 裝置在那個 IP 時就標在它身上。"""
    sub = await _subnet(db_session, "192.0.2.0/24")
    ip = await _ip(db_session, sub, "192.0.2.54", rustdesk_enabled=True)
    raw = "ka" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("123450001", "192.0.2.54", True), ("123450002", "192.0.2.55", True)]))
    r = await _poll(client, raw, key_checks=_kc(fails=[
        {"scope": "relay", "ip": "192.0.2.54", "n": 2, "first": _t8(0),
         "last": _t8(1), "target": None}]))
    assert r.status_code == 200, r.text
    rows = {p["rustdesk_id"]: p for p in (await client.get(
        f"/api/v1/rustdesk/servers/{srv.id}/peers", headers=auth_headers)).json()["items"]}
    kp = rows["123450001"]["key_problem"]
    assert kp["scope"] == "relay" and kp["count"] == 2
    want = datetime.fromisoformat(_t8(1)).astimezone(UTC)
    assert datetime.fromisoformat(kp["at"]).astimezone(UTC) == want, "日誌的 +08:00 時間換成 UTC 存"
    assert rows["123450002"]["key_problem"] is None
    d = (await client.get(f"/api/v1/addresses/{ip.id}", headers=auth_headers)).json()
    assert d["rustdesk"]["key_problem"]["scope"] == "relay"
    servers = (await client.get("/api/v1/rustdesk/servers", headers=auth_headers)).json()
    srow = next(s for s in (servers["items"] if isinstance(servers, dict) else servers) if s["id"] == str(srv.id))
    assert srow["agent_status"]["logs"]["hbbr"] is True


async def test_key_failure_on_a_shared_ip_blames_nobody(client, auth_headers, db_session) -> None:
    """NAT 後面好幾台共用一個 IP：分不出是哪一台，不猜。"""
    raw = "kb" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("123450011", "203.0.113.9", True), ("123450012", "203.0.113.9", True)]))
    await _poll(client, raw, key_checks=_kc(fails=[
        {"scope": "relay", "ip": "203.0.113.9", "n": 1, "first": _t8(0),
         "last": _t8(0), "target": None}]))
    peers = await _peers(db_session, srv)
    assert all(p.key_fail_at is None for p in peers.values())


async def test_later_relay_success_clears_the_key_problem(client, auth_headers, db_session) -> None:
    """改好 Key 之後中繼一通過（日誌的 New relay request／got paired）就不再標；比被拒早的通過不算。"""
    raw = "kc" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("123450021", "192.0.2.60", True)]))
    fail = {"scope": "relay", "ip": "192.0.2.60", "n": 1, "first": _t8(0), "last": _t8(0), "target": None}
    await _poll(client, raw, key_checks=_kc(fails=[fail], ok=[{"ip": "192.0.2.60", "last": _t8(-2)}]))

    async def problem():
        rows = (await client.get(f"/api/v1/rustdesk/servers/{srv.id}/peers", headers=auth_headers)).json()["items"]
        return rows[0]["key_problem"]

    assert await problem() is not None, "比被拒還早的通過不能清掉"
    await _poll(client, raw, key_checks=_kc(ok=[{"ip": "192.0.2.60", "last": _t8(8)}]))
    assert await problem() is None
    # 又被拒：次數從 1 重新算
    await _poll(client, raw, key_checks=_kc(fails=[{**fail, "first": _t8(18), "last": _t8(18)}]))
    assert (await problem())["count"] == 1


async def test_hbbs_key_failure_flags_the_caller(client, auth_headers, db_session) -> None:
    """hbbs 的 `Authentication failed from <IP> for peer <ID>`：Key 錯的是發起連線的那台（IP 那一端）。"""
    raw = "kd" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("123450031", "192.0.2.70", True)]))
    await _poll(client, raw, key_checks=_kc(fails=[
        {"scope": "hbbs", "ip": "192.0.2.70", "n": 1, "first": _t8(0),
         "last": _t8(0), "target": "000000001"}]))
    peers = await _peers(db_session, srv)
    assert peers["123450031"].key_fail_scope == "hbbs"


async def test_poll_without_key_checks_still_works(client, db_session) -> None:
    """1.0.0 的代理不送 key_checks：照常輪詢（代理會在這一輪自我更新）。"""
    raw = "ke" * 20
    await _server(db_session, raw)
    r = await _poll(client, raw)
    assert r.status_code == 200, r.text


async def test_report_does_not_flip_a_heartbeating_device_offline(client, db_session) -> None:
    """hbbs 的線上狀態只看 30 秒內有沒有 UDP 註冊：UDP 掉包、或走 TCP／WebSocket 連 hbbs 的客戶端會被說成離線，
    但它每 15 秒的心跳照樣送到。以前每 5 分鐘的完整回報只信 hbbs → 在線的裝置被改成離線、下一個心跳又改回上線
    （使用者 2026-10-06：「明明都在線上，有時會變離線、有時又上線」）。45 秒內有心跳就算上線。"""
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    raw = "q9" * 20
    srv = await _server(db_session, raw)
    await _send(client, raw, _report(srv.id, [("111111111", "198.51.100.9", True), ("222222222", "198.51.100.10", True)]))
    await _events(client, raw, srv.id, [_ev("heartbeat", "111111111"), _ev("heartbeat", "222222222")])
    # 222222222 的心跳已經停了一陣子
    p2 = (await _peers(db_session, srv))["222222222"]
    p2.last_heartbeat_at = _dt.now(UTC) - _td(minutes=3)
    await db_session.commit()
    await _send(client, raw, _report(srv.id, [("111111111", "198.51.100.9", False), ("222222222", "198.51.100.10", False)]))
    peers = await _peers(db_session, srv)
    assert peers["111111111"].online is True, "剛剛還有心跳 → hbbs 說離線也不算離線"
    assert peers["222222222"].online is False, "hbbs 說離線、心跳也停了 → 離線"
