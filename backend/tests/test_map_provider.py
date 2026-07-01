"""地圖供應商（map_provider）是全域設定：
- GET 任何登入者都可讀（Locations 地圖預覽要用）
- PUT 只有 admin 能改（選擇器移到系統設定 admin 區）
"""

from __future__ import annotations

import uuid

from app.models.user import User


async def _nonadmin_token(db_session) -> tuple[User, str]:
    from app.core.security import hash_password
    from app.services.auth import issue_access_token
    u = User(username=f"na-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local",
             display_name="NA", password_hash=hash_password("TestPassword2026!"),
             auth_provider="local", is_active=True, is_admin=False)
    db_session.add(u)
    await db_session.flush()
    await db_session.commit()
    return u, issue_access_token(u)


async def test_nonadmin_can_read_map_provider(client, db_session):
    _u, token = await _nonadmin_token(db_session)
    r = await client.get("/api/v1/system/map-provider",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["provider"] in ("osm", "google", "builtin")


async def test_admin_can_set_map_provider(client, auth_headers):
    r = await client.put("/api/v1/system/map-provider",
                         headers=auth_headers, json={"provider": "google"})
    assert r.status_code == 200
    assert r.json()["provider"] == "google"
    r2 = await client.get("/api/v1/system/map-provider", headers=auth_headers)
    assert r2.json()["provider"] == "google"
    # 還原預設，避免影響其他測試
    await client.put("/api/v1/system/map-provider",
                     headers=auth_headers, json={"provider": "osm"})


async def test_nonadmin_cannot_set_map_provider(client, db_session):
    _u, token = await _nonadmin_token(db_session)
    r = await client.put("/api/v1/system/map-provider",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"provider": "google"})
    assert r.status_code == 403
