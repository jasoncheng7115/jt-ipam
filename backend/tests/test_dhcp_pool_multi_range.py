"""同一台 DHCP 伺服器、同一個網段的多段發放範圍算成一個集區（GitHub issue #57，2026-11-03）。

FortiGate 60F 的 OA 介面（/23）設了 .50.50–.50.200 與 .51.50–.51.200 兩段。FortiGate 從第一段開始發，
第一段一定先滿：以前每段各算一個集區，戰情室報第一段 91%、「集區快用完」也照單段發，兩段合計其實只用了
一半多。Kea／ISC 在一個 subnet 下設多個 pool 也是同一段分組邏輯。

守的是：
1. 同一個來源＋同一個網段的各段範圍合併：總數加總、已用加總、顯示列出每段範圍
2. 不同網段照舊分開；來源沒有網段資訊時一段一個（不知道是不是同一個網段就不合）
3. 告警的識別鍵以網段為單位，範圍增減或順序改變仍是同一個集區
4. 排除區間切段（0210）照樣扣掉，跟多段範圍可以同時存在
"""
from __future__ import annotations

import uuid

import pytest
from app.models.address import IPAddress
from app.models.section import Section
from app.models.subnet import Subnet

pytestmark = pytest.mark.anyio


async def _subnet(db_session, cidr: str) -> Subnet:  # type: ignore[no-untyped-def]
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr=cidr)
    db_session.add(sub)
    await db_session.flush()
    return sub


async def _fortigate(db_session, monkeypatch, servers):  # type: ignore[no-untyped-def]
    from app.models.fortigate import FortiGateFirewall
    from app.services import fortigate as fg
    fw = FortiGateFirewall(name=f"fg-{uuid.uuid4().hex[:6]}", api_url="https://192.0.2.1",
                           api_token_enc=b"x", api_token_nonce=b"x", vdoms=["root"])
    db_session.add(fw)
    await db_session.flush()

    async def fake(_fw, path, *, vdom=None, timeout=15.0):  # type: ignore[no-untyped-def]
        return servers if path == fg.EP_DHCP_SERVERS else []
    monkeypatch.setattr(fg, "_api_get", fake)
    await fg.sync_dhcp_ranges(db_session, fw, ["root"])
    await db_session.flush()
    return fw


OA = {"interface": "OA", "status": "enable",
      "ip-range": [{"id": 1, "start-ip": "10.50.50.50", "end-ip": "10.50.50.200"},
                   {"id": 2, "start-ip": "10.50.51.50", "end-ip": "10.50.51.200"}],
      "exclude-range": []}


async def _leases(db_session, sub, prefix: str, first: int, n: int) -> None:  # type: ignore[no-untyped-def]
    for i in range(n):
        db_session.add(IPAddress(subnet_id=sub.id, ip=f"{prefix}.{first + i}", in_dhcp_lease=True))
    await db_session.flush()


async def test_two_ranges_on_one_interface_are_one_pool(db_session, monkeypatch) -> None:
    from app.services.dhcp_usage import pool_usage
    sub = await _subnet(db_session, "10.50.50.0/23")
    await _fortigate(db_session, monkeypatch, [OA])
    await _leases(db_session, sub, "10.50.50", 50, 140)
    await _leases(db_session, sub, "10.50.51", 50, 36)
    (pool, used, size), = await pool_usage(db_session)
    assert (used, size) == (176, 302), "兩段合計：151＋151 個位址、140＋36 個在用"
    assert pool.ranges == [("10.50.50.50", "10.50.50.200"), ("10.50.51.50", "10.50.51.200")]
    assert pool.excluded == 0


async def test_first_range_full_does_not_alert_when_the_pool_has_room(db_session, monkeypatch, admin_user) -> None:
    from app.services.capacity_alert import check_dhcp_pools
    from app.services.dhcp_usage import pool_usage
    sub = await _subnet(db_session, "10.50.50.0/23")
    await _fortigate(db_session, monkeypatch, [OA])
    await _leases(db_session, sub, "10.50.50", 50, 151)      # 第一段全滿
    assert await check_dhcp_pools(db_session, await pool_usage(db_session), threshold_pct=90) == 0


async def test_alert_names_every_range_when_the_whole_pool_is_full(db_session, monkeypatch, admin_user) -> None:
    from app.models.notification import Notification
    from app.services.capacity_alert import check_dhcp_pools
    from app.services.dhcp_usage import pool_usage
    from sqlalchemy import select
    sub = await _subnet(db_session, "10.50.50.0/23")
    await _fortigate(db_session, monkeypatch, [OA])
    await _leases(db_session, sub, "10.50.50", 50, 151)
    await _leases(db_session, sub, "10.50.51", 50, 140)
    assert await check_dhcp_pools(db_session, await pool_usage(db_session), threshold_pct=90) == 1
    n = (await db_session.execute(select(Notification).where(Notification.user_id == admin_user.id))).scalars().first()
    assert "10.50.50.50–10.50.50.200" in n.params["where"]
    assert "10.50.51.50–10.50.51.200" in n.params["where"]
    assert (n.params["used"], n.params["size"]) == (291, 302)


async def test_kea_style_pools_in_one_subnet_merge_but_other_subnets_do_not(db_session) -> None:
    from app.services.dhcp_standalone import write_pools
    from app.services.dhcp_usage import pool_usage
    await _subnet(db_session, "10.60.0.0/24")
    await _subnet(db_session, "10.61.0.0/24")
    sid = uuid.uuid4()
    await write_pools(db_session, source_type="kea_dhcp", source_id=sid, source_name="kea", engine="kea",
                      pools=[{"subnet": "10.60.0.0/24", "start": "10.60.0.10", "end": "10.60.0.19"},
                             {"subnet": "10.60.0.0/24", "start": "10.60.0.100", "end": "10.60.0.109"},
                             {"subnet": "10.61.0.0/24", "start": "10.61.0.10", "end": "10.61.0.19"}])
    await db_session.flush()
    got = sorted((p.subnet_cidr, size) for p, _u, size in await pool_usage(db_session))
    assert got == [("10.60.0.0/24", 20), ("10.61.0.0/24", 10)]


async def test_ranges_without_a_subnet_stay_separate(db_session) -> None:
    from app.models.dhcp import DHCPPoolRange
    from app.services.dhcp_standalone import write_pools
    from app.services.dhcp_usage import group_pools
    from sqlalchemy import select
    sid = uuid.uuid4()
    await write_pools(db_session, source_type="isc_dhcp", source_id=sid, source_name="isc", engine="isc",
                      pools=[{"subnet": None, "start": "10.62.0.10", "end": "10.62.0.19"},
                             {"subnet": None, "start": "10.63.0.10", "end": "10.63.0.19"}])
    await db_session.flush()
    rows = (await db_session.execute(select(DHCPPoolRange).where(DHCPPoolRange.source_id == sid))).scalars().all()
    assert len(group_pools(list(rows))) == 2


async def test_exclusions_and_several_ranges_together(db_session, monkeypatch) -> None:
    from app.services.dhcp_usage import pool_usage
    await _subnet(db_session, "10.50.50.0/23")
    oa = dict(OA, **{"exclude-range": [{"id": 1, "start-ip": "10.50.51.100", "end-ip": "10.50.51.109",
                                        "vci-match": "disable", "uci-match": "disable"}]})
    await _fortigate(db_session, monkeypatch, [oa])
    (pool, _used, size), = await pool_usage(db_session)
    assert size == 292
    assert pool.excluded == 10


def test_alert_key_is_per_subnet_and_ignores_range_order() -> None:
    from app.services.capacity_alert import pool_alert_key
    from app.services.dhcp_usage import PoolGroup

    def g(ranges, subnet="OA"):  # type: ignore[no-untyped-def]
        p = PoolGroup(source_type="fortigate", source_id="f1", source_name="fg", subnet_cidr=subnet,
                      start_ip=ranges[0][0], end_ip=ranges[-1][1])
        for a, b in ranges:
            p.add(a, b, a, b)
        return p

    a = [("10.50.50.50", "10.50.50.200"), ("10.50.51.50", "10.50.51.200")]
    assert pool_alert_key(g(a)) == pool_alert_key(g(list(reversed(a))))
    assert pool_alert_key(g(a)) == pool_alert_key(g(a[:1])), "範圍增減仍是同一個集區"
    assert pool_alert_key(g(a)) != pool_alert_key(g(a, subnet="LAN"))


async def test_war_room_capacity_lists_every_range(db_session, monkeypatch) -> None:
    from app.services.warroom.capacity import pool_items
    sub = await _subnet(db_session, "10.50.50.0/23")
    await _fortigate(db_session, monkeypatch, [OA])
    await _leases(db_session, sub, "10.50.50", 50, 140)
    await _leases(db_session, sub, "10.50.51", 50, 36)
    (it,) = await pool_items(db_session, None, 80, 90)
    assert (it["used"], it["capacity"]) == (176, 302)
    assert it["level"] is None or it["level"] == "ok"
    assert it["ident"] == "10.50.50.50 - 10.50.50.200, 10.50.51.50 - 10.50.51.200"
