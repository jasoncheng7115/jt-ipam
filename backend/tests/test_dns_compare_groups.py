"""DNS 比對群組（2026-10-10 使用者需求）。

互相同步的 DNS 伺服器標成一組：
- 每次同步完，用已經同步下來的紀錄比對組內每一台，不一樣的列出來；持續超過寬限時間才算確認、
  才用通知功能發告警（吸收伺服器之間的同步延遲），同一批只發一次，全部恢復一致時再說一聲
- 有成員還沒同步成功時先不判定（不然一台連不上就整組「不一致」）
- 不同廠牌的名稱寫法不同（大小寫、結尾點、IPv6 縮寫），比對前先正規化
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.dns import DNSRecord, DNSServer, DNSZone
from app.models.dns_compare_group import DNSCompareGroup, DNSCompareGroupDiff
from app.models.notification import Notification
from app.services import dns_compare

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


# ── 正規化 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("name", "zone", "want"), [
    ("Host01.Example.COM", "example.com", "host01.example.com"),
    ("host01.example.com.", "example.com", "host01.example.com"),
    ("host01", "example.com", "host01.example.com"),          # 相對名稱補上 zone
    ("@", "example.com", "example.com"),
    ("", "Example.com.", "example.com"),
    ("5.0.10.10.in-addr.arpa", "0.10.10.in-addr.arpa", "5.0.10.10.in-addr.arpa"),
])
def test_normalize_name(name, zone, want) -> None:
    assert dns_compare.normalize_name(name, zone) == want


@pytest.mark.parametrize(("rtype", "value", "want"), [
    ("A", " 192.0.2.5 ", "192.0.2.5"),
    ("AAAA", "2001:0db8:0000:0000:0000:0000:0000:0001", "2001:db8::1"),
    ("AAAA", "2001:DB8::1", "2001:db8::1"),
    ("PTR", "Host01.Example.com.", "host01.example.com"),
    ("PTR", "host01.example.com", "host01.example.com"),
    ("CNAME", "Web.Example.com.", "web.example.com"),
])
def test_normalize_value(rtype, value, want) -> None:
    assert dns_compare.normalize_value(rtype, value) == want


# ── 資料準備 ────────────────────────────────────────────────────────────

async def _group(session, *, grace=30, notify=True, name=None) -> DNSCompareGroup:
    g = DNSCompareGroup(name=name or f"grp-{uuid.uuid4().hex[:6]}", grace_minutes=grace, notify_enabled=notify)
    session.add(g)
    await session.flush()
    return g


async def _server(session, group, name, *, synced=True, error=None, stype="bind9") -> DNSServer:
    s = DNSServer(name=f"{name}-{uuid.uuid4().hex[:4]}", type=stype, compare_group_id=group.id if group else None,
                  last_sync_at=NOW - timedelta(minutes=1) if synced else None, last_error=error)
    session.add(s)
    await session.flush()
    return s


async def _records(session, server, zone_name, recs) -> DNSZone:
    z = DNSZone(server_id=server.id, name=zone_name, type="reverse" if "arpa" in zone_name else "forward")
    session.add(z)
    await session.flush()
    for name, rtype, value in recs:
        session.add(DNSRecord(zone_id=z.id, name=name, type=rtype, value=value, source="from_dns_pulled",
                              name_norm=dns_compare.normalize_name(name, zone_name),
                              value_norm=dns_compare.normalize_value(rtype, value)))
    await session.flush()
    return z


async def _alerts(session, event_title_key: str) -> int:
    rows = (await session.execute(select(Notification).where(Notification.title_key == event_title_key))).scalars().all()
    return len(rows)


# ── 比對 ────────────────────────────────────────────────────────────────

async def test_single_member_is_not_compared(db_session) -> None:
    g = await _group(db_session)
    await _server(db_session, g, "ns1")
    r = await dns_compare.check_group(db_session, g, now=NOW)
    assert r["status"] == "single"


async def test_member_without_a_successful_sync_is_incomplete(db_session) -> None:
    g = await _group(db_session)
    a = await _server(db_session, g, "ns1")
    b = await _server(db_session, g, "ns2", error="zone example.com: refused")
    await _records(db_session, a, "example.com", [("www.example.com", "A", "192.0.2.10")])
    await _records(db_session, b, "example.com", [])
    r = await dns_compare.check_group(db_session, g, now=NOW)
    assert r["status"] == "incomplete", "一台連不上不等於兩台不一致"
    assert not (await db_session.execute(select(DNSCompareGroupDiff))).scalars().all()


async def test_identical_records_written_differently_are_consistent(db_session) -> None:
    """BIND 保留大小寫、PTR 帶結尾點；Technitium 全小寫不帶點 —— 內容一樣就是一樣。"""
    g = await _group(db_session)
    a = await _server(db_session, g, "ns1", stype="bind9")
    b = await _server(db_session, g, "ns2", stype="technitium")
    await _records(db_session, a, "Example.com", [("WWW.example.com", "A", "192.0.2.10"),
                                                  ("v6.example.com", "AAAA", "2001:0db8:0:0:0:0:0:1")])
    await _records(db_session, b, "example.com", [("www.example.com", "A", "192.0.2.10"),
                                                  ("v6.example.com", "AAAA", "2001:db8::1")])
    await _records(db_session, a, "2.0.192.in-addr.arpa", [("10.2.0.192.in-addr.arpa", "PTR", "WWW.example.com.")])
    await _records(db_session, b, "2.0.192.in-addr.arpa", [("10.2.0.192.in-addr.arpa", "PTR", "www.example.com")])
    r = await dns_compare.check_group(db_session, g, now=NOW)
    assert r["status"] == "ok", r


async def test_record_on_one_member_is_a_diff_confirmed_after_grace_and_alerted_once(db_session, admin_user) -> None:
    g = await _group(db_session, grace=30)
    a = await _server(db_session, g, "ns1")
    b = await _server(db_session, g, "ns2")
    await _records(db_session, a, "example.com", [("www.example.com", "A", "192.0.2.10"),
                                                  ("new.example.com", "A", "192.0.2.11")])
    await _records(db_session, b, "example.com", [("www.example.com", "A", "192.0.2.10")])

    r = await dns_compare.check_group(db_session, g, now=NOW)
    assert r["status"] == "pending", "剛出現的差異可能只是同步延遲，先不告警"
    d = (await db_session.execute(select(DNSCompareGroupDiff))).scalars().one()
    assert (d.kind, d.zone, d.name, d.type, d.value) == ("record", "example.com", "new.example.com", "A", "192.0.2.11")
    assert d.present_on == [str(a.id)] and d.missing_on == [str(b.id)]
    assert await _alerts(db_session, "notif.dns_compare_mismatch") == 0

    r = await dns_compare.check_group(db_session, g, now=NOW + timedelta(minutes=31))
    assert r["status"] == "pending", "沒有的那台在寬限時間之後還沒重新拉取過：不知道它現在有沒有，不可以判定"
    b.last_sync_at = NOW + timedelta(minutes=30)          # 寬限時間過後又拉了一次，還是沒有
    r = await dns_compare.check_group(db_session, g, now=NOW + timedelta(minutes=31))
    assert r["status"] == "mismatch"
    assert await _alerts(db_session, "notif.dns_compare_mismatch") == 1
    d2 = (await db_session.execute(select(DNSCompareGroupDiff))).scalars().one()
    assert d2.first_seen_at == a.last_sync_at, "第一次看到＝有這筆的那台拉取的時間（資料的時間，不是比對的時鐘）"
    assert d2.confirmed_at == NOW + timedelta(minutes=31)

    await dns_compare.check_group(db_session, g, now=NOW + timedelta(minutes=45))
    assert await _alerts(db_session, "notif.dns_compare_mismatch") == 1, "同一批差異不重複通知"

    # 補上之後恢復一致：清掉差異、發一則恢復通知
    zb = (await db_session.execute(select(DNSZone).where(DNSZone.server_id == b.id))).scalars().one()
    db_session.add(DNSRecord(zone_id=zb.id, name="new.example.com", type="A", value="192.0.2.11",
                             source="from_dns_pulled", name_norm="new.example.com", value_norm="192.0.2.11"))
    await db_session.flush()
    r = await dns_compare.check_group(db_session, g, now=NOW + timedelta(minutes=50))
    assert r["status"] == "ok"
    assert not (await db_session.execute(select(DNSCompareGroupDiff))).scalars().all()
    assert await _alerts(db_session, "notif.dns_compare_resolved") == 1


async def test_member_not_pulled_since_the_diff_appeared_is_not_judged(db_session, admin_user) -> None:
    """真實環境（2026-10-11）：三台依序拉取，第一台一拉完就比對 —— 那時另外兩台用的還是舊資料。
    以前寬限 0 分鐘就當場告警「另外兩台沒有」，但其中一台其實有（還沒輪到它拉取而已）。
    沒有的那台，要在差異出現之後（再加寬限時間）重新拉取過、仍然沒有，才算確認；通知只列真的沒有的那台。"""
    g = await _group(db_session, grace=0)
    p = await _server(db_session, g, "primary")
    s1 = await _server(db_session, g, "sec1")
    s2 = await _server(db_session, g, "sec2")
    zp = await _records(db_session, p, "repl.example", [("www.repl.example", "A", "198.51.100.10")])
    z1 = await _records(db_session, s1, "repl.example", [("www.repl.example", "A", "198.51.100.10")])
    await _records(db_session, s2, "repl.example", [("www.repl.example", "A", "198.51.100.10")])
    assert (await dns_compare.check_group(db_session, g, now=NOW))["status"] == "ok"

    # 主要伺服器多了一筆、它剛被拉取（另外兩台還沒輪到）
    db_session.add(DNSRecord(zone_id=zp.id, name="new1.repl.example", type="A", value="198.51.100.20",
                             source="from_dns_pulled", name_norm="new1.repl.example", value_norm="198.51.100.20"))
    p.last_sync_at = NOW + timedelta(minutes=1)
    await db_session.flush()
    r = await dns_compare.check_group(db_session, g, now=NOW + timedelta(minutes=1))
    assert r["status"] == "pending"
    assert await _alerts(db_session, "notif.dns_compare_mismatch") == 0

    # sec1 拉取：複寫正常，它也有
    db_session.add(DNSRecord(zone_id=z1.id, name="new1.repl.example", type="A", value="198.51.100.20",
                             source="from_dns_pulled", name_norm="new1.repl.example", value_norm="198.51.100.20"))
    s1.last_sync_at = NOW + timedelta(minutes=2)
    await db_session.flush()
    assert (await dns_compare.check_group(db_session, g, now=NOW + timedelta(minutes=2)))["status"] == "pending"

    # sec2 拉取：複寫斷了，它沒有 → 現在才確認，通知只說 sec2 沒有
    s2.last_sync_at = NOW + timedelta(minutes=3)
    r = await dns_compare.check_group(db_session, g, now=NOW + timedelta(minutes=3))
    assert r["status"] == "mismatch"
    n = (await db_session.execute(select(Notification).where(
        Notification.title_key == "notif.dns_compare_mismatch"))).scalars().all()
    assert len(n) == 1 and s2.name in (n[0].body or "") and s1.name not in (n[0].body or "")
    # 內文由前端依語言組句（以前把英文的 "missing on" 組好塞進參數，中文畫面也顯示英文）
    assert n[0].body_key == "notif.dns_compare_mismatch_body"
    assert n[0].params["what"] == "new1.repl.example A 198.51.100.20"
    assert n[0].params["missing"] == s2.name and n[0].params["more"] == 0
    assert "missing on" not in str(n[0].params)


async def test_new_diff_after_an_alert_alerts_again(db_session, admin_user) -> None:
    g = await _group(db_session, grace=0)
    a = await _server(db_session, g, "ns1")
    b = await _server(db_session, g, "ns2")
    za = await _records(db_session, a, "example.com", [("one.example.com", "A", "192.0.2.1")])
    await _records(db_session, b, "example.com", [])
    await dns_compare.check_group(db_session, g, now=NOW)
    assert await _alerts(db_session, "notif.dns_compare_mismatch") == 1
    db_session.add(DNSRecord(zone_id=za.id, name="two.example.com", type="A", value="192.0.2.2",
                             source="from_dns_pulled", name_norm="two.example.com", value_norm="192.0.2.2"))
    await db_session.flush()
    await dns_compare.check_group(db_session, g, now=NOW + timedelta(minutes=5))
    assert await _alerts(db_session, "notif.dns_compare_mismatch") == 2, "之後又多出來的差異要再通知"


async def test_alert_with_several_new_differences_says_how_many_more(db_session, admin_user) -> None:
    g = await _group(db_session, grace=0)
    a = await _server(db_session, g, "ns1")
    b = await _server(db_session, g, "ns2")
    b.last_sync_at = a.last_sync_at
    await _records(db_session, a, "example.com", [(f"h{i}.example.com", "A", f"192.0.2.{i}") for i in range(1, 4)])
    await _records(db_session, b, "example.com", [])
    await _records(db_session, a, "lab.example", [("x.lab.example", "A", "192.0.2.9")])
    await dns_compare.check_group(db_session, g, now=NOW)
    n = (await db_session.execute(select(Notification).where(
        Notification.title_key == "notif.dns_compare_mismatch"))).scalars().one()
    assert n.body_key == "notif.dns_compare_mismatch_body_more"
    assert n.params["more"] == 3 and n.params["count"] == 4
    assert n.params["missing"] == b.name


async def test_zone_missing_on_a_member_is_one_diff(db_session) -> None:
    g = await _group(db_session, grace=0, notify=False)
    a = await _server(db_session, g, "ns1")
    b = await _server(db_session, g, "ns2")
    await _records(db_session, a, "example.com", [("www.example.com", "A", "192.0.2.10")])
    await _records(db_session, b, "example.com", [("www.example.com", "A", "192.0.2.10")])
    await _records(db_session, a, "lab.example.net", [(f"h{i}.lab.example.net", "A", f"198.51.100.{i}")
                                                      for i in range(1, 6)])
    r = await dns_compare.check_group(db_session, g, now=NOW)
    diffs = (await db_session.execute(select(DNSCompareGroupDiff))).scalars().all()
    assert [(d.kind, d.zone) for d in diffs] == [("zone", "lab.example.net")], "整個 zone 不見只算一筆，不是每筆紀錄各一筆"
    assert r["status"] == "mismatch"


async def test_windows_dns_and_ucs_can_be_compared(db_session) -> None:
    """使用者 2026-10-10：不同軟體（Windows DNS＋UCS）也要能比對。Windows 回相對名稱補成的完整名稱、
    PTR 帶結尾點；UCS 的 PTR 是 zone 內的位址＋FQDN 清單。內容一樣就是一致。"""
    g = await _group(db_session)
    win = await _server(db_session, g, "dc01", stype="windows_dns")
    ucs = await _server(db_session, g, "ucs1", stype="univention_ucs")
    await _records(db_session, win, "example.com", [("DC01.example.com", "A", "192.0.2.5")])
    await _records(db_session, ucs, "example.com", [("dc01.example.com", "A", "192.0.2.5")])
    await _records(db_session, win, "2.0.192.in-addr.arpa", [("5.2.0.192.in-addr.arpa", "PTR", "dc01.example.com.")])
    await _records(db_session, ucs, "2.0.192.in-addr.arpa", [("5.2.0.192.in-addr.arpa", "PTR", "dc01.example.com")])
    r = await dns_compare.check_group(db_session, g, now=NOW)
    assert r["status"] == "ok", r


async def test_ad_locator_data_is_not_compared(db_session) -> None:
    """Windows 與 Samba（UCS）登記 AD 服務定位資料的方式不同：_msdcs 與 TrustAnchors 這兩個 zone、
    DomainDnsZones/ForestDnsZones 這種分割區定位紀錄不是 IPAM 的主機資料，不比對（否則混搭一定滿滿差異）。"""
    g = await _group(db_session)
    win = await _server(db_session, g, "dc01", stype="windows_dns")
    ucs = await _server(db_session, g, "ucs1", stype="univention_ucs")
    await _records(db_session, win, "example.com", [("www.example.com", "A", "192.0.2.10"),
                                                    ("DomainDnsZones.example.com", "A", "192.0.2.5"),
                                                    ("ForestDnsZones.example.com", "A", "192.0.2.5")])
    await _records(db_session, ucs, "example.com", [("www.example.com", "A", "192.0.2.10")])
    await _records(db_session, win, "_msdcs.example.com", [("gc._msdcs.example.com", "A", "192.0.2.5")])
    await _records(db_session, win, "TrustAnchors", [])
    r = await dns_compare.check_group(db_session, g, now=NOW)
    assert r["status"] == "ok", r
    assert not (await db_session.execute(select(DNSCompareGroupDiff))).scalars().all()


async def test_empty_zone_on_one_member_is_not_a_difference(db_session) -> None:
    """沒有任何可比對紀錄的 zone（例如只放了 SRV、或剛建立）只在一台上：對 IPAM 沒有內容差異，不列。
    有紀錄的 zone 缺在別台才列（test_zone_missing_on_a_member_is_one_diff）。"""
    g = await _group(db_session)
    a = await _server(db_session, g, "ns1")
    b = await _server(db_session, g, "ns2")
    await _records(db_session, a, "example.com", [("www.example.com", "A", "192.0.2.10")])
    await _records(db_session, b, "example.com", [("www.example.com", "A", "192.0.2.10")])
    await _records(db_session, a, "empty.example", [])
    r = await dns_compare.check_group(db_session, g, now=NOW)
    assert r["status"] == "ok", r


async def test_unbound_mixed_with_zone_servers_cannot_be_compared(db_session, admin_user) -> None:
    """Unbound（OPNsense）只有主機覆寫、沒有完整的 zone：和權威 DNS 比一定滿滿差異。
    API 會擋；萬一已經在同組（例如伺服器類型改了），比對顯示「無法比對」、不列差異、不告警。"""
    g = await _group(db_session, grace=0)
    a = await _server(db_session, g, "ns1", stype="bind9")
    u = await _server(db_session, g, "fw1", stype="unbound_opnsense")
    await _records(db_session, a, "example.com", [("www.example.com", "A", "192.0.2.10")])
    await _records(db_session, u, "example.com", [])
    r = await dns_compare.check_group(db_session, g, now=NOW)
    assert r["status"] == "incompatible"
    assert u.name in (r["message"] or "")
    assert not (await db_session.execute(select(DNSCompareGroupDiff))).scalars().all()
    assert await _alerts(db_session, "notif.dns_compare_mismatch") == 0


async def test_excluded_zones_are_not_compared(db_session) -> None:
    """真實環境（2026-10-11）：Technitium 另外放了一個沒有複寫的 zone，整組永遠「不一致」。
    群組可以設定不比對的 zone：那個 zone 只在一台不算差異，裡面的紀錄也不比（大小寫、結尾點都正規化）。"""
    g = await _group(db_session, grace=0, notify=False)
    g.excluded_zones = ["Own.Example."]
    a = await _server(db_session, g, "ns1", stype="powerdns")
    b = await _server(db_session, g, "ns2", stype="technitium")
    await _records(db_session, a, "repl.example", [("www.repl.example", "A", "198.51.100.10")])
    await _records(db_session, b, "repl.example", [("www.repl.example", "A", "198.51.100.10")])
    await _records(db_session, b, "own.example", [("x.own.example", "A", "198.51.100.99")])
    r = await dns_compare.check_group(db_session, g, now=NOW)
    assert r["status"] == "ok", r
    # 兩邊都有、內容不同的 zone 被排除時，裡面的差異也不列
    await _records(db_session, a, "own.example", [("y.own.example", "A", "198.51.100.98")])
    r = await dns_compare.check_group(db_session, g, now=NOW)
    assert r["status"] == "ok", r
    # 拿掉排除就又列出來
    g.excluded_zones = []
    r = await dns_compare.check_group(db_session, g, now=NOW)
    assert r["status"] == "mismatch"


@pytest.mark.parametrize(("raw", "want"), [
    (["Own.Example.", "own.example", " lab.example "], ["lab.example", "own.example"]),
    ([], []),
    (["", "  "], []),
])
def test_normalize_zone_list(raw, want) -> None:
    assert dns_compare.normalize_zone_list(raw) == want


def test_compatibility_rule() -> None:
    assert dns_compare.incompatible_types(["windows_dns", "univention_ucs", "bind9", "powerdns", "technitium"]) == []
    assert dns_compare.incompatible_types(["unbound_opnsense", "unbound_opnsense"]) == []
    assert dns_compare.incompatible_types(["bind9", "unbound_opnsense"]) == ["unbound_opnsense"]


async def test_notify_disabled_sends_nothing(db_session, admin_user) -> None:
    g = await _group(db_session, grace=0, notify=False)
    a = await _server(db_session, g, "ns1")
    b = await _server(db_session, g, "ns2")
    await _records(db_session, a, "example.com", [("x.example.com", "A", "192.0.2.9")])
    await _records(db_session, b, "example.com", [])
    r = await dns_compare.check_group(db_session, g, now=NOW)
    assert r["status"] == "mismatch"
    assert await _alerts(db_session, "notif.dns_compare_mismatch") == 0


async def test_disabled_member_is_left_out(db_session) -> None:
    g = await _group(db_session, grace=0)
    a = await _server(db_session, g, "ns1")
    b = await _server(db_session, g, "ns2")
    b.enabled = False
    await _records(db_session, a, "example.com", [("x.example.com", "A", "192.0.2.9")])
    await db_session.flush()
    r = await dns_compare.check_group(db_session, g, now=NOW)
    assert r["status"] == "single", "停用的成員不參與比對"


async def test_events_are_in_the_notification_matrix() -> None:
    from app.services.system_config import NOTIFY_EVENTS
    keys = {e[0] for e in NOTIFY_EVENTS}
    assert {"dns.compare_mismatch", "dns.compare_resolved"} <= keys


# ── 同步時填正規化欄位，同步完檢查群組 ────────────────────────────────

async def test_pull_fills_normalized_columns_and_checks_the_group(db_session, monkeypatch) -> None:
    from app.services import dns_sync
    from app.services.dns.base import DNSRecordOp, DNSZoneInfo

    class _Fake:
        async def list_zones(self):
            return [DNSZoneInfo(name="Example.com", kind="forward")]

        async def list_records(self, _z):
            return [DNSRecordOp(name="WWW.Example.com", type="A", value="192.0.2.10")]

        async def close(self):
            return None

    async def _adapter(_s, _srv):
        return _Fake()
    monkeypatch.setattr(dns_sync, "get_adapter", _adapter)
    g = await _group(db_session)
    a = await _server(db_session, g, "ns1", synced=False)
    b = await _server(db_session, g, "ns2", synced=False)
    await dns_sync.pull_server(db_session, a)
    rec = (await db_session.execute(select(DNSRecord).join(DNSZone).where(DNSZone.server_id == a.id))).scalars().one()
    assert (rec.name_norm, rec.value_norm) == ("www.example.com", "192.0.2.10")
    r = await dns_compare.check_group_of_server(db_session, a)
    assert r["status"] == "incomplete", "另一台還沒同步過"
    await dns_sync.pull_server(db_session, b)
    r = await dns_compare.check_group_of_server(db_session, b)
    assert r["status"] == "ok"
