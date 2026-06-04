"""DNS 拉取（mock adapter）：同一 IP 有多筆 A 記錄時，套用的主機名稱必須是
穩定的（字母序最小），不隨每次 sync 的記錄順序跳動。"""

from __future__ import annotations

from app.models.address import IPAddress
from app.models.dns import DNSServer
from app.models.section import Section
from app.models.subnet import Subnet
from app.services import dns_sync
from app.services.dns.base import DNSRecordOp, DNSZoneInfo


class _FakeAdapter:
    def __init__(self, records):
        self._records = records

    async def list_zones(self):
        return [DNSZoneInfo(name="example.com", kind="forward")]

    async def list_records(self, _zone_name):
        return list(self._records)

    async def close(self):
        return None


def _patch(monkeypatch, records):
    async def _fake_get_adapter(_session, _server):
        return _FakeAdapter(records)
    monkeypatch.setattr(dns_sync, "get_adapter", _fake_get_adapter)


async def _mk_ip(session, ip="10.10.0.5"):
    sec = Section(name="dns-sec")
    session.add(sec)
    await session.flush()
    sub = Subnet(section_id=sec.id, cidr="10.10.0.0/24")
    session.add(sub)
    await session.flush()
    obj = IPAddress(subnet_id=sub.id, ip=ip)
    session.add(obj)
    await session.flush()
    return obj


async def test_multiple_a_records_pick_stable_name(db_session, monkeypatch):
    ipa = await _mk_ip(db_session)
    server = DNSServer(name="pdns-1", type="powerdns")
    db_session.add(server)
    await db_session.flush()

    # 同一 IP 兩筆 A：meet3 / meet3-turn → 套用字母序最小 = meet3
    _patch(monkeypatch, [
        DNSRecordOp(name="meet3-turn", type="A", value="10.10.0.5"),
        DNSRecordOp(name="meet3", type="A", value="10.10.0.5"),
    ])
    await dns_sync.pull_server(db_session, server)
    await db_session.refresh(ipa)
    assert ipa.hostname == "meet3"


async def test_name_does_not_flap_on_record_order(db_session, monkeypatch):
    ipa = await _mk_ip(db_session)
    server = DNSServer(name="pdns-2", type="powerdns")
    db_session.add(server)
    await db_session.flush()

    _patch(monkeypatch, [
        DNSRecordOp(name="aaa", type="A", value="10.10.0.5"),
        DNSRecordOp(name="zzz", type="A", value="10.10.0.5"),
    ])
    await dns_sync.pull_server(db_session, server)
    await db_session.refresh(ipa)
    assert ipa.hostname == "aaa"

    # 第二次反序回傳 → 仍應是 aaa（不跳動）
    _patch(monkeypatch, [
        DNSRecordOp(name="zzz", type="A", value="10.10.0.5"),
        DNSRecordOp(name="aaa", type="A", value="10.10.0.5"),
    ])
    await dns_sync.pull_server(db_session, server)
    await db_session.refresh(ipa)
    assert ipa.hostname == "aaa"
