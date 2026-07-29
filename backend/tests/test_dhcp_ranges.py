"""DHCP 發放範圍：三個來源（OPNsense / pfSense / Windows DHCP）各自寫入同一張衍生表。

重點在「各自獨立」：每個來源只清除自己的列，不會互相蓋掉。
"""

from __future__ import annotations

import pytest
from app.models.firewall import OPNsenseFirewall
from app.models.pfsense import PfSenseFirewall
from app.services import opnsense_firewall as fw
from app.services import pfsense as pf
from sqlalchemy import func, select


async def _mk_fw(session):  # type: ignore[no-untyped-def]
    f = OPNsenseFirewall(
        name="fw-test", api_url="https://10.0.0.1",
        api_key_enc=b"x", api_key_nonce=b"x", api_secret_enc=b"x", api_secret_nonce=b"x",
    )
    session.add(f)
    await session.flush()
    return f


async def _mk_pf(session, name="pf-test"):  # type: ignore[no-untyped-def]
    f = PfSenseFirewall(
        name=name, api_url="https://10.0.0.2", api_key_enc=b"x", api_key_nonce=b"x",
    )
    session.add(f)
    await session.flush()
    return f


async def _ranges_of(session, source_type, source_id):  # type: ignore[no-untyped-def]
    from app.models.dhcp import DHCPPoolRange
    rows = (await session.execute(select(DHCPPoolRange).where(
        DHCPPoolRange.source_type == source_type, DHCPPoolRange.source_id == source_id,
    ))).scalars().all()
    return sorted((r.subnet_cidr, r.start_ip, r.end_ip) for r in rows)


# ─────────────────────────── OPNsense（回歸）───────────────────────────
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

    pools = await _ranges_of(db_session, "opnsense", f.id)
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
    n = await db_session.scalar(select(func.count()).select_from(DHCPPoolRange).where(
        DHCPPoolRange.source_type == "opnsense", DHCPPoolRange.source_id == f.id))
    assert n == 1


# ─────────────────────────── pfSense ───────────────────────────
@pytest.mark.anyio
async def test_pfsense_sync_dhcp_ranges(db_session, admin_user, monkeypatch) -> None:
    """主範圍 + 巢狀額外池都要抓；停用的介面略過。"""
    f = await _mk_pf(db_session)

    async def fake_get(_fw, path, timeout=10.0):  # type: ignore[no-untyped-def]
        if path == pf.EP_DHCP_SERVERS:
            return [
                {"interface": "lan", "enable": True,
                 "range_from": "192.168.1.100", "range_to": "192.168.1.199",
                 "pool": [{"range_from": "192.168.1.210", "range_to": "192.168.1.220"}]},
                {"interface": "opt1", "enable": False,      # 停用 → 不算
                 "range_from": "10.9.9.10", "range_to": "10.9.9.20"},
                {"interface": "wan"},                        # 沒有範圍 → 跳過
            ]
        return []

    monkeypatch.setattr(pf, "_api_get", fake_get)
    n = await pf.sync_dhcp_ranges(db_session, f)
    assert n == 2

    pools = await _ranges_of(db_session, "pfsense", f.id)
    assert ("lan", "192.168.1.100", "192.168.1.199") in pools
    assert ("lan", "192.168.1.210", "192.168.1.220") in pools


@pytest.mark.anyio
async def test_pfsense_ranges_do_not_touch_other_sources(db_session, admin_user, monkeypatch) -> None:
    """pfSense 同步只清自己的列 —— OPNsense 的範圍必須原封不動（各自獨立的重點）。"""
    o = await _mk_fw(db_session)
    p = await _mk_pf(db_session)

    async def opn_get(_fw, path, timeout=8.0):  # type: ignore[no-untyped-def]
        if path == "/api/kea/dhcpv4/searchSubnet":
            return {"rows": [{"subnet": "192.168.1.0/24", "pools": "192.168.1.150-192.168.1.200"}]}
        raise fw.OPNsenseError("nf")

    monkeypatch.setattr(fw, "_api_get", opn_get)
    await fw.sync_dhcp_ranges(db_session, o)

    async def pf_get(_fw, path, timeout=10.0):  # type: ignore[no-untyped-def]
        if path == pf.EP_DHCP_SERVERS:
            return [{"interface": "lan", "range_from": "10.1.1.10", "range_to": "10.1.1.20"}]
        return []

    monkeypatch.setattr(pf, "_api_get", pf_get)
    await pf.sync_dhcp_ranges(db_session, p)

    assert len(await _ranges_of(db_session, "opnsense", o.id)) == 1   # 沒被 pfSense 清掉
    assert len(await _ranges_of(db_session, "pfsense", p.id)) == 1


# ─────────────────────────── Windows DHCP（Beta）───────────────────────────
class _FakeWinClient:
    def __init__(self, scopes, leases=None):
        self._scopes = scopes
        self._leases = leases or {}

    def get_scopes(self):
        return self._scopes

    def get_leases(self, scope_id):
        return self._leases.get(scope_id, [])


async def _mk_win(session, name="win-dhcp"):  # type: ignore[no-untyped-def]
    from app.models.windows_dhcp import WindowsDhcpServer
    w = WindowsDhcpServer(
        name=name, host="dhcp.corp.example.com", username="CORP\\svc",
        password_enc=b"x", password_nonce=b"x",
    )
    session.add(w)
    await session.flush()
    return w


@pytest.mark.anyio
async def test_windows_dhcp_sync_scopes(db_session, admin_user, monkeypatch) -> None:
    """ScopeId + SubnetMask → CIDR；Inactive 的 scope 不算。"""
    from app.services import windows_dhcp as wd
    w = await _mk_win(db_session)
    monkeypatch.setattr(wd, "_client", lambda _inst: _FakeWinClient([
        {"ScopeId": "192.168.50.0", "SubnetMask": "255.255.255.0",
         "StartRange": "192.168.50.100", "EndRange": "192.168.50.200",
         "Name": "Office", "State": "Active"},
        {"ScopeId": "10.5.0.0", "SubnetMask": "255.255.0.0",
         "StartRange": "10.5.1.1", "EndRange": "10.5.1.99",
         "Name": "Lab", "State": "Inactive"},   # 停用 → 略過
    ]))
    n = await wd.sync_scopes(db_session, w)
    assert n == 1
    pools = await _ranges_of(db_session, "windows_dhcp", w.id)
    assert pools == [("192.168.50.0/24", "192.168.50.100", "192.168.50.200")]


@pytest.mark.anyio
async def test_windows_dhcp_scope_handles_ps_object_shape(db_session, admin_user, monkeypatch) -> None:
    """PowerShell 的 IP 物件序列化成 {'IPAddressToString': ...} 也要能解析。"""
    from app.services import windows_dhcp as wd
    w = await _mk_win(db_session, "win2")
    monkeypatch.setattr(wd, "_client", lambda _inst: _FakeWinClient([
        {"ScopeId": {"IPAddressToString": "172.20.0.0"},
         "SubnetMask": {"IPAddressToString": "255.255.255.0"},
         "StartRange": {"IPAddressToString": "172.20.0.50"},
         "EndRange": {"IPAddressToString": "172.20.0.80"}, "State": "Active"},
    ]))
    assert await wd.sync_scopes(db_session, w) == 1
    assert await _ranges_of(db_session, "windows_dhcp", w.id) == [
        ("172.20.0.0/24", "172.20.0.50", "172.20.0.80")]


@pytest.mark.anyio
async def test_windows_dhcp_leases_mark_existing_only(db_session, admin_user, monkeypatch) -> None:
    """租約只標記既有 IP（in_dhcp_lease + MAC/主機名稱），絕不新建 IP。"""
    from app.models.address import IPAddress
    from app.models.section import Section
    from app.models.subnet import Subnet
    from app.services import windows_dhcp as wd

    sect = Section(name="win-sec")
    db_session.add(sect)
    await db_session.flush()
    sub = Subnet(section_id=sect.id, cidr="192.168.60.0/24")
    db_session.add(sub)
    await db_session.flush()
    ipa = IPAddress(subnet_id=sub.id, ip="192.168.60.25")
    db_session.add(ipa)
    await db_session.flush()

    w = await _mk_win(db_session, "win3")
    monkeypatch.setattr(wd, "_client", lambda _inst: _FakeWinClient(
        [{"ScopeId": "192.168.60.0", "SubnetMask": "255.255.255.0",
          "StartRange": "192.168.60.10", "EndRange": "192.168.60.90", "State": "Active"}],
        {"192.168.60.0": [
            {"IPAddress": "192.168.60.25", "ClientId": "AA-BB-CC-11-22-33",
             "HostName": "pc-a.corp.local", "AddressState": "Active"},
            {"IPAddress": "192.168.60.77", "ClientId": "AA-BB-CC-44-55-66",
             "HostName": "ghost", "AddressState": "Active"},   # IPAM 沒有這筆 → 不建
        ]},
    ))
    before = await db_session.scalar(select(func.count()).select_from(IPAddress))
    seen = await wd.sync_leases(db_session, w)
    after = await db_session.scalar(select(func.count()).select_from(IPAddress))

    assert seen == 1            # 只有既有那筆被標到
    assert after == before      # 沒有新建 IP
    await db_session.refresh(ipa)
    assert ipa.in_dhcp_lease is True
    assert ipa.hostname == "pc-a"          # FQDN 取短名
    assert str(ipa.mac).lower() == "aa:bb:cc:11:22:33"
