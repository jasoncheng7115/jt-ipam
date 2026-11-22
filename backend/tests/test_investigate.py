"""AI 調查模式：把一個位址散落在各處的線索收成一份檔案。

為什麼要有：查一台機器現在要在六個頁面之間跳。這幾天追 `192.168.123.187`（macOS 掛到
Linux VM）和 `.36`（ping 得到卻顯示離線），都是這樣一頁一頁翻出來的 —— IP 詳細資料、
Wazuh、DNS、NAT、ARP、異動記錄。人做得到，但慢，而且很容易漏掉關鍵的那一項。

權限分層照專案既有的三類走：
- 位址本身是逐物件資料 → 看不到那個子網路就整份都不給
- NAT／防火牆／DNS 是全域基礎設施 → **只有具全域讀取權限的人才附上這幾段**，
  其餘人拿到的是同一份檔案少了那幾段，而不是被擋掉整個功能
"""
from __future__ import annotations

import uuid

import pytest
from app.models.address import IPAddress
from app.models.nat import NATTranslation
from app.models.permission import Permission
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.user import User
from app.services.investigate import collect_dossier


@pytest.fixture
async def target(db_session):
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sn = Subnet(section_id=sec.id, cidr="198.51.100.0/24", description="服務網段")
    db_session.add(sn)
    await db_session.flush()
    ipa = IPAddress(subnet_id=sn.id, ip="198.51.100.77", hostname="target-host",
                    effective_status="online", mac="aa:bb:cc:dd:ee:77")
    db_session.add(ipa)
    await db_session.flush()
    db_session.add(NATTranslation(name="pf-web", type="port_forward",
                                  dst_ip_id=ipa.id, dst_port=443, protocol="tcp"))
    await db_session.commit()
    return sn, ipa


async def _dept_user(db_session, subnet_id):
    """只被指派一個子網路的部門帳號（沒有全域讀取權限）。"""
    from app.core.security import hash_password
    u = User(username=f"d-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local",
             display_name="D", password_hash=hash_password("TestPassword2026!"),
             auth_provider="local", is_active=True, is_admin=False)
    db_session.add(u)
    await db_session.flush()
    db_session.add(Permission(object_type="subnet", object_id=subnet_id,
                              principal_type="user", principal_id=u.id, level="read"))
    await db_session.commit()
    return u


@pytest.mark.anyio
async def test_dossier_gathers_the_basics(db_session, admin_user, target):
    _sn, ipa = target
    d = await collect_dossier(db_session, user=admin_user, ip="198.51.100.77")
    assert d["found"] is True
    assert d["address"]["hostname"] == "target-host"
    assert d["address"]["subnet"] == "198.51.100.0/24"
    assert d["address"]["ip_address_id"] == str(ipa.id)


@pytest.mark.anyio
async def test_admin_sees_global_infrastructure(db_session, admin_user, target):
    """管理員要看得到 NAT —— 這正是判斷「這台有沒有對外開放」的關鍵一段。"""
    d = await collect_dossier(db_session, user=admin_user, ip="198.51.100.77")
    assert any(n["name"] == "pf-web" for n in d["nat"])
    assert d["global_read"] is True


@pytest.mark.anyio
async def test_department_user_gets_the_record_without_global_sections(db_session, target):
    """部門帳號看得到自己子網路裡的位址，但拿不到 NAT／防火牆／DNS 那幾段。

    重點是**降級而不是全部擋掉**：他對自己的機器仍該查得到基本狀況。
    """
    sn, _ipa = target
    u = await _dept_user(db_session, sn.id)
    d = await collect_dossier(db_session, user=u, ip="198.51.100.77")
    assert d["found"] is True
    assert d["address"]["hostname"] == "target-host"
    assert d["global_read"] is False
    assert d["nat"] == []
    assert d["firewall_rules"] == []
    assert d["dns"] == []


@pytest.mark.anyio
async def test_invisible_address_is_not_disclosed(db_session, target):
    """看不到那個子網路的人，連「這個位址存在」都不該知道。"""
    sec = Section(name=f"other-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    other = Subnet(section_id=sec.id, cidr="203.0.113.0/24")
    db_session.add(other)
    await db_session.flush()
    u = await _dept_user(db_session, other.id)

    d = await collect_dossier(db_session, user=u, ip="198.51.100.77")
    assert d["found"] is False
    assert "address" not in d or not d.get("address")


@pytest.mark.anyio
async def test_overlapping_addresses_are_all_returned(db_session, admin_user, target):
    """重疊網段下同一個位址有多筆 → 要把每一筆都列出來，不能只挑一筆。

    「挑一筆」正是這幾天連續修掉的那類 bug；調查用的檔案更不該重蹈覆轍。
    """
    sn, _ipa = target
    sec2 = Section(name=f"s2-{uuid.uuid4().hex[:6]}")
    db_session.add(sec2)
    await db_session.flush()
    narrow = Subnet(section_id=sec2.id, cidr="198.51.100.64/28")
    db_session.add(narrow)
    await db_session.flush()
    db_session.add(IPAddress(subnet_id=narrow.id, ip="198.51.100.77", hostname="dup"))
    await db_session.commit()

    d = await collect_dossier(db_session, user=admin_user, ip="198.51.100.77")
    assert len(d["other_records"]) == 1, "另一筆同位址的紀錄要被指出來"
    assert d["other_records"][0]["subnet"] == "198.51.100.64/28"
