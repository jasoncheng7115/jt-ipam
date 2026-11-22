"""重疊網段下的搜尋結果要分得出是哪一筆。

實機情況：`192.168.123.36` 同時存在於 `192.168.123.0/24` 與 `192.168.123.32/28`（兩個子網路
互相重疊），兩筆主機名稱又剛好一樣，搜尋結果就變成兩列一模一樣的東西 —— 使用者看不出
差別，也不知道自己點進去的是哪一筆（而那兩筆的存活狀態並不相同）。

重疊網段本身是刻意支援的（不同單位可能都用 192.168.123.0/24），所以正解不是把重複的藏
起來，而是**把區別講出來**：標上所屬子網路。
"""
from __future__ import annotations

import uuid

import pytest
from app.models.address import IPAddress
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.search import search


@pytest.fixture
async def overlapping(db_session):
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    wide = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    narrow = Subnet(section_id=sec.id, cidr="198.51.100.32/28")
    db_session.add_all([wide, narrow])
    await db_session.flush()
    for sn in (wide, narrow):
        db_session.add(IPAddress(subnet_id=sn.id, ip="198.51.100.36", hostname="dup-host"))
    await db_session.commit()
    return wide, narrow


@pytest.mark.anyio
async def test_both_records_are_returned(db_session, admin_user, overlapping):
    """兩筆都要回 —— 重疊網段是刻意支援的，不能自作主張只回一筆。"""
    res = await search(db_session, user=admin_user, q="198.51.100.36")
    ips = [h for h in res["results"] if h["type"] == "ip_address"]
    assert len(ips) == 2


@pytest.mark.anyio
async def test_results_say_which_subnet(db_session, admin_user, overlapping):
    """兩列必須看得出差別，否則使用者是在猜自己點的是哪一筆。"""
    res = await search(db_session, user=admin_user, q="198.51.100.36")
    subs = [str(h.get("sublabel") or "")
            for h in res["results"] if h["type"] == "ip_address"]
    assert len(set(subs)) == 2, f"兩列的說明文字必須不同：{subs}"
    assert any("198.51.100.0/24" in s for s in subs)
    assert any("198.51.100.32/28" in s for s in subs)
    assert all("dup-host" in s for s in subs), "主機名稱仍要看得到"
