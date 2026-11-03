"""可用 IP 不可以給「未納管」的位址（使用者 11-02：另一支 AI 用 MCP 找空位，拿到的 .231–.233 其實有回應、.237 有 ARP）。

子網路指示計早就把這些畫成「未納管」—— 系統知道它們在用，但算「下一個可用」的地方只看 IP 清單有沒有記錄，
所以 REST 的 first-free、IP 申請核准、AI 的 find_free_ip(s)、phpIPAM 相容 API（PVE SDN 用它配發）都會把在用的位址配出去。
跟指示計同一個定義：最近 7 天掃描代理目擊或 ARP 看得到、IPAM 沒有記錄的位址。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.models.address import IPAddress
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.unmanaged_sighting import UnmanagedSighting
from app.services.subnet import find_first_free_address, find_free_addresses


async def _net(db):  # type: ignore[no-untyped-def]
    sec = Section(name=f"free-{uuid.uuid4().hex[:6]}")
    db.add(sec)
    await db.flush()
    sub = Subnet(section_id=sec.id, cidr="198.51.100.0/29")
    db.add(sub)
    await db.flush()
    db.add(IPAddress(subnet_id=sub.id, ip="198.51.100.1", state="active"))
    now = datetime.now(UTC)
    # .2 掃描代理剛看到在用、.3 一個月前看到（已經不算）
    db.add(UnmanagedSighting(subnet_id=sub.id, ip="198.51.100.2", source="scan_agent", mac="02:00:5e:00:53:02",
                             first_seen_at=now - timedelta(hours=1), last_seen_at=now - timedelta(minutes=3)))
    db.add(UnmanagedSighting(subnet_id=sub.id, ip="198.51.100.3", source="scan_agent",
                             first_seen_at=now - timedelta(days=20), last_seen_at=now - timedelta(days=10)))
    await db.commit()
    return sub


async def test_first_free_skips_an_address_seen_in_use(db_session) -> None:
    sub = await _net(db_session)
    assert await find_first_free_address(db_session, sub) == "198.51.100.3", ".2 有人在用；.3 十天前看到的不算"


async def test_free_list_skips_addresses_seen_in_use(db_session) -> None:
    sub = await _net(db_session)
    assert await find_free_addresses(db_session, sub, count=3) == ["198.51.100.3", "198.51.100.4", "198.51.100.5"]
    assert await find_free_addresses(db_session, sub, count=2, consecutive=True) == ["198.51.100.3", "198.51.100.4"]


async def test_ai_tool_says_which_addresses_were_skipped(db_session, admin_user) -> None:
    """AI 要知道為什麼跳過 .2：不說的話它會以為系統沒有這份資料，又自己去 ping。"""
    from app.mcp.tools import TOOLS
    sub = await _net(db_session)
    out = await TOOLS["find_free_ips"]["fn"](db_session, user=admin_user, subnet_cidr=str(sub.cidr), count=2)
    assert out["ips"] == ["198.51.100.3", "198.51.100.4"]
    skipped = out["skipped_in_use"]
    assert [x["ip"] for x in skipped] == ["198.51.100.2"]
    assert skipped[0]["sources"] == ["scan_agent"] and skipped[0]["mac"] == "02:00:5e:00:53:02"
    one = await TOOLS["find_free_ip"]["fn"](db_session, user=admin_user, subnet_cidr=str(sub.cidr))
    assert one["ip"] == "198.51.100.3" and [x["ip"] for x in one["skipped_in_use"]] == ["198.51.100.2"]


async def test_rest_first_free_skips_it_too(client, auth_headers, db_session) -> None:
    sub = await _net(db_session)
    r = await client.get(f"/api/v1/subnets/{sub.id}/first_free_address", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "198.51.100.3" in r.text and "198.51.100.2" not in r.text
