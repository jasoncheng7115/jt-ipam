"""全域搜尋：VPN / 單位 / 機櫃 / 地點 也要找得到。"""

from __future__ import annotations

import uuid

from app.models.customer import Customer
from app.models.location import Location, Rack
from app.models.physical import VPNTunnel
from app.services.search import search


async def test_search_finds_vpn_customer_rack_location(db_session, admin_user):
    tag = uuid.uuid4().hex[:6]
    db_session.add(VPNTunnel(name=f"fwsearch-{tag}/wg/site", type="wireguard", status="active"))
    db_session.add(Customer(name=f"custsearch-{tag}"))
    loc = Location(name=f"locsearch-{tag}")
    db_session.add(loc); await db_session.flush()
    db_session.add(Rack(name=f"racksearch-{tag}", location_id=loc.id, u_height=42))
    await db_session.flush()

    async def _types(q):
        res = await search(db_session, user=admin_user, q=q)
        return {h["type"] for h in res["results"]}

    assert "vpn" in await _types(f"fwsearch-{tag}")
    assert "customer" in await _types(f"custsearch-{tag}")
    assert "rack" in await _types(f"racksearch-{tag}")
    assert "location" in await _types(f"locsearch-{tag}")
