"""回歸測試：AdGuard sync 在重疊網段（同 IP 多筆、未設 scope）時不可 MultipleResultsFound。

對應修補：adguard.py 的 sync_clients / sync_rewrites 把 IPAddress.ip 比對改
.limit(1).scalars().first()（地雷 #7：同 IP 多筆會炸掉整批 sync）。
2026-09-26 起改用 ip_autocreate.match_existing：多筆＝不明確、**不猜**（以前任意取一筆，
名稱會掛到別的單位名下）→ 不炸、也不寫進任何一筆。
"""

from __future__ import annotations

import uuid

from app.models.address import IPAddress
from app.models.adguard import AdGuardInstance
from app.models.section import Section
from app.models.subnet import Subnet
from app.services import adguard as adguard_svc


async def _make_overlapping_ip(db_session, ip_value: str = "192.168.123.5") -> None:
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    # 兩個重疊子網路（同 CIDR），各掛一筆相同 IP → IPAddress.ip == x 會回多筆
    for _ in range(2):
        sn = Subnet(section_id=sec.id, cidr="192.168.123.0/24")
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
        return {"clients": [{"name": "pc1", "ids": ["192.168.123.5", "host.lan"]}]}

    monkeypatch.setattr(adguard_svc, "_api_get", fake_api_get)
    inst = _instance()
    # 修補前這裡會 raise MultipleResultsFound
    res = await adguard_svc.sync_clients(db_session, inst)
    assert res["ips_matched"] == 0, "兩個單位都有這個 IP、又沒設範圍 → 不猜是誰的"


async def test_sync_rewrites_overlap_no_crash(db_session, monkeypatch):
    await _make_overlapping_ip(db_session)

    async def fake_api_get(inst, path, *, timeout=15.0):
        return [{"domain": "host.lan", "answer": "192.168.123.5"}]

    monkeypatch.setattr(adguard_svc, "_api_get", fake_api_get)
    inst = _instance()
    res = await adguard_svc.sync_rewrites(db_session, inst)
    assert res["rewrites_matched"] == 0, "兩個單位都有這個 IP、又沒設範圍 → 不猜是誰的"


async def test_clients_and_rewrites_match_in_one_query(db_session, monkeypatch):
    """用戶端與改寫都對到既有 IP：主機名稱、MAC、DNS 最後看到的時間；查詢次數不隨筆數成長
    （2026-09-30 大量資料測試：以前每個位址各查一次）。"""
    from sqlalchemy import event, select

    db_session.autoflush = False
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sn = Subnet(section_id=sec.id, cidr="10.96.0.0/16")
    db_session.add(sn)
    await db_session.flush()
    n = 300
    await db_session.execute(IPAddress.__table__.insert(), [
        {"subnet_id": sn.id, "ip": f"10.96.{i // 256}.{i % 256}"} for i in range(n)])
    inst = _instance()
    db_session.add(inst)
    await db_session.commit()
    clients = [{"name": f"pc-{i}", "ids": [f"10.96.{i // 256}.{i % 256}", f"00:00:5e:00:{i // 256:02x}:{i % 256:02x}"]}
               for i in range(n)]

    async def fake_api_get(_inst, path, *, timeout=15.0):
        return {"clients": clients} if path == "/control/clients" else [
            {"domain": f"svc-{i}.lan", "answer": f"10.96.{i // 256}.{i % 256}"} for i in range(n)]
    monkeypatch.setattr(adguard_svc, "_api_get", fake_api_get)

    q = {"n": 0}
    conn = await db_session.connection()

    def _c(*_a, **_k):
        q["n"] += 1
    await adguard_svc.sync_clients(db_session, inst)          # 第一輪：補 MAC、名稱
    await db_session.commit()
    event.listen(conn.sync_connection, "before_cursor_execute", _c)
    try:
        res = await adguard_svc.sync_clients(db_session, inst)
        res2 = await adguard_svc.sync_rewrites(db_session, inst)
        await db_session.commit()
    finally:
        event.remove(conn.sync_connection, "before_cursor_execute", _c)
    assert res["ips_matched"] == n
    assert res2["rewrites_matched"] == n
    assert q["n"] < 50, f"兩段同步查了 {q['n']} 次（各 {n} 筆）"
    ip = (await db_session.execute(select(IPAddress).where(IPAddress.ip == "10.96.0.7"))).scalar_one()
    assert str(ip.mac) == "00:00:5e:00:00:07"
    assert ip.last_seen_dns is not None
