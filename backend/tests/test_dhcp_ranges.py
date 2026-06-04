"""DHCP pool range 同步：解析 Kea pools（多段）並鏡像進 dhcp_pool_ranges。"""

from __future__ import annotations

import pytest
from app.models.firewall import OPNsenseFirewall
from app.services import opnsense_firewall as fw


async def _mk_fw(session):  # type: ignore[no-untyped-def]
    f = OPNsenseFirewall(
        name="fw-test", api_url="https://10.0.0.1",
        api_key_enc=b"x", api_key_nonce=b"x", api_secret_enc=b"x", api_secret_nonce=b"x",
    )
    session.add(f)
    await session.flush()
    return f


@pytest.mark.anyio
async def test_sync_dhcp_ranges_parses_multi_pool(db_session, admin_user, monkeypatch) -> None:
    f = await _mk_fw(db_session)

    async def fake_get(_fw, path, timeout=8.0):  # type: ignore[no-untyped-def]
        if path == "/api/kea/dhcpv4/searchSubnet":
            return {"rows": [
                {"subnet": "192.168.1.0/24", "pools": "192.168.1.150-192.168.1.200"},
                {"subnet": "10.0.0.0/24", "pools": "10.0.0.10-10.0.0.50\n10.0.0.100-10.0.0.150"},
                {"subnet": "172.16.0.0/24", "pools": ""},  # 無 pool → 跳過
            ]}
        raise fw.OPNsenseError("not found")  # v6 endpoint

    monkeypatch.setattr(fw, "_api_get", fake_get)
    res = await fw.sync_dhcp_ranges(db_session, f)
    assert res["ranges"] == 3   # 1 + 2 段

    from app.models.dhcp import DHCPPoolRange
    from sqlalchemy import select
    rows = (await db_session.execute(
        select(DHCPPoolRange).where(DHCPPoolRange.firewall_id == f.id)
    )).scalars().all()
    pools = sorted((r.subnet_cidr, r.start_ip, r.end_ip) for r in rows)
    assert ("10.0.0.0/24", "10.0.0.10", "10.0.0.50") in pools
    assert ("10.0.0.0/24", "10.0.0.100", "10.0.0.150") in pools
    assert ("192.168.1.0/24", "192.168.1.150", "192.168.1.200") in pools


@pytest.mark.anyio
async def test_sync_dhcp_ranges_mirror_replaces(db_session, admin_user, monkeypatch) -> None:
    """再次同步應鏡像取代，不重複堆疊。"""
    f = await _mk_fw(db_session)

    async def fake_get(_fw, path, timeout=8.0):  # type: ignore[no-untyped-def]
        if path == "/api/kea/dhcpv4/searchSubnet":
            return {"rows": [{"subnet": "192.168.1.0/24", "pools": "192.168.1.150-192.168.1.200"}]}
        raise fw.OPNsenseError("nf")

    monkeypatch.setattr(fw, "_api_get", fake_get)
    await fw.sync_dhcp_ranges(db_session, f)
    await fw.sync_dhcp_ranges(db_session, f)

    from app.models.dhcp import DHCPPoolRange
    from sqlalchemy import func, select
    n = await db_session.scalar(
        select(func.count()).select_from(DHCPPoolRange).where(DHCPPoolRange.firewall_id == f.id)
    )
    assert n == 1
