"""常用子網路（user_preferences.pinned_subnet_ids）只回傳還能用的（客戶 2026-11-06）。

客戶的拓樸圖篩選欄顯示一串 UUID、圖也畫不出來，按 × 清掉才正常：常用子網路裡存著一個已經刪掉
（或看不到）的子網路。拓樸圖預設帶常用子網路去查，查不到任何東西；選單裡也找不到它的名稱。
讀的時候與存的時候都只留存在、未歸檔、這個人看得到的（跟儀表板「IP 使用率」的自選子網路同一套檢查）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.models.permission import Permission
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.user import User, UserPreference

pytestmark = pytest.mark.anyio
URL = "/api/v1/me/preferences"


async def _subnets(db):  # type: ignore[no-untyped-def]
    sec = Section(name=f"pin-{uuid.uuid4().hex[:6]}")
    db.add(sec)
    await db.flush()
    ok = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    old = Subnet(section_id=sec.id, cidr="203.0.113.0/24", archived_at=datetime.now(UTC))
    db.add_all([ok, old])
    await db.commit()
    return ok, old


async def test_stale_pins_are_dropped_on_read_and_write(client, auth_headers, admin_user, db_session) -> None:
    ok, old = await _subnets(db_session)
    gone = uuid.uuid4()                                    # 已經刪掉的子網路
    db_session.add(UserPreference(user_id=admin_user.id, locale="zh-TW", theme="auto", timezone="Asia/Taipei",
                                  calendar="gregorian", page_size=50,
                                  pinned_subnet_ids=[str(gone), str(ok.id), str(old.id)]))
    await db_session.commit()
    r = await client.get(URL, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["pinned_subnet_ids"] == [str(ok.id)]

    r = await client.patch(URL, headers=auth_headers, json={"pinned_subnet_ids": [str(gone), str(ok.id)]})
    assert r.status_code == 200, r.text
    assert r.json()["pinned_subnet_ids"] == [str(ok.id)]


async def test_pins_outside_the_users_scope_are_not_returned(client, db_session) -> None:
    from app.core.security import hash_password
    from app.services.auth import issue_access_token
    ok, _old = await _subnets(db_session)
    sec2 = Section(name=f"pin2-{uuid.uuid4().hex[:6]}")
    db_session.add(sec2)
    await db_session.flush()
    hidden = Subnet(section_id=sec2.id, cidr="192.0.2.0/24")
    db_session.add(hidden)
    u = User(username=f"u-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local", display_name="U",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True)
    db_session.add(u)
    await db_session.flush()
    db_session.add(Permission(object_type="subnet", object_id=ok.id, principal_type="user",
                              principal_id=u.id, level="read"))
    db_session.add(UserPreference(user_id=u.id, locale="zh-TW", theme="auto", timezone="Asia/Taipei",
                                  calendar="gregorian", page_size=50,
                                  pinned_subnet_ids=[str(hidden.id), str(ok.id)]))
    await db_session.commit()
    r = await client.get(URL, headers={"Authorization": f"Bearer {issue_access_token(u)}"})
    assert r.json()["pinned_subnet_ids"] == [str(ok.id)]
