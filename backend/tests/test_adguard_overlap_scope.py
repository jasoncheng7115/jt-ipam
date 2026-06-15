"""回歸測試：AdGuard sync 在重疊網段（同 IP 多筆、未設 scope）時不可 MultipleResultsFound。

對應修補：adguard.py 的 sync_clients / sync_rewrites 把 IPAddress.ip 比對改
.limit(1).scalars().first()（地雷 #7：同 IP 多筆會炸掉整批 sync）。
"""

from __future__ import annotations

import uuid

from app.models.address import IPAddress
from app.models.adguard import AdGuardInstance
from app.models.section import Section
from app.models.subnet import Subnet
from app.services import adguard as adguard_svc


async def _make_overlapping_ip(db_session, ip_value: str = "192.168.1.5") -> None:
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    # 兩個重疊子網路（同 CIDR），各掛一筆相同 IP → IPAddress.ip == x 會回多筆
    for _ in range(2):
        sn = Subnet(section_id=sec.id, cidr="192.168.1.0/24")
        db_session.add(sn)
        await db_session.flush()
        db_session.add(IPAddress(subnet_id=sn.id, ip=ip_value, state="active"))
    await db_session.commit()


def _instance() -> AdGuardInstance:
    return AdGuardInstance(
        name=f"ag-{uuid.uuid4().hex[:6]}", api_url="https://adguard.local",
        api_user="admin", api_password_enc=b"x", api_password_nonce=b"x",
        enabled=True, scope_subnet_ids=None,  # 未設 scope → 全域比對，會撞到重疊 IP
    )


async def test_sync_clients_overlap_no_crash(db_session, monkeypatch):
    await _make_overlapping_ip(db_session)

    async def fake_api_get(inst, path, *, timeout=15.0):
        return {"clients": [{"name": "pc1", "ids": ["192.168.1.5", "host.lan"]}]}

    monkeypatch.setattr(adguard_svc, "_api_get", fake_api_get)
    inst = _instance()
    # 修補前這裡會 raise MultipleResultsFound
    res = await adguard_svc.sync_clients(db_session, inst)
    assert res["ips_matched"] >= 1


async def test_sync_rewrites_overlap_no_crash(db_session, monkeypatch):
    await _make_overlapping_ip(db_session)

    async def fake_api_get(inst, path, *, timeout=15.0):
        return [{"domain": "host.lan", "answer": "192.168.1.5"}]

    monkeypatch.setattr(adguard_svc, "_api_get", fake_api_get)
    inst = _instance()
    res = await adguard_svc.sync_rewrites(db_session, inst)
    assert res["rewrites_matched"] >= 1
