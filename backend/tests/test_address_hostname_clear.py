"""清除某來源的 hostname 觀測：刪掉過時的「手動」名稱後，有效 hostname 依優先序
回退到次高來源；若被刪來源剛好是 pin，pin 也一併取消。"""

from __future__ import annotations

from app.models.address import IPAddress
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.hostname import apply_observation


async def _mk_ip(session) -> IPAddress:
    sec = Section(name="hclr-sec")
    session.add(sec)
    await session.flush()
    sub = Subnet(section_id=sec.id, cidr="10.66.0.0/24")
    session.add(sub)
    await session.flush()
    ip = IPAddress(subnet_id=sub.id, ip="10.66.0.8")
    session.add(ip)
    await session.flush()
    return ip


async def test_clear_manual_falls_back_to_dns(client, db_session, auth_headers):
    ip = await _mk_ip(db_session)
    await apply_observation(db_session, ip=ip, source="dns", hostname="good-dns")
    await apply_observation(db_session, ip=ip, source="manual", hostname="stale-manual")
    # manual 優先序最高 → 目前有效名是 stale-manual
    assert ip.hostname == "stale-manual"
    await db_session.commit()

    d = await client.delete(
        f"/api/v1/addresses/{ip.id}/hostname-sources/manual", headers=auth_headers,
    )
    assert d.status_code == 204, d.text

    g = await client.get(
        f"/api/v1/addresses/{ip.id}/hostname-sources", headers=auth_headers,
    )
    body = g.json()
    assert body["effective"] == "good-dns"
    sources = {o["source"] for o in body["observations"]}
    assert "manual" not in sources
    assert "dns" in sources


async def test_clear_pinned_source_also_clears_pin(client, db_session, auth_headers):
    ip = await _mk_ip(db_session)
    await apply_observation(db_session, ip=ip, source="dns", hostname="dns-name")
    await apply_observation(db_session, ip=ip, source="manual", hostname="pinned-manual")
    ip.hostname_source_pin = "manual"
    await db_session.commit()

    d = await client.delete(
        f"/api/v1/addresses/{ip.id}/hostname-sources/manual", headers=auth_headers,
    )
    assert d.status_code == 204, d.text

    g = await client.get(
        f"/api/v1/addresses/{ip.id}/hostname-sources", headers=auth_headers,
    )
    body = g.json()
    assert body["pin"] is None
    assert body["effective"] == "dns-name"
