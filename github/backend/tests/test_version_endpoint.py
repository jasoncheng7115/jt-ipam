"""/system/version 回現行版本 + 套件版本；check-latest 容錯（無網路不 500）。"""

from __future__ import annotations

from app.version import __version__


async def test_version_info(client, auth_headers):
    r = await client.get("/api/v1/system/version", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current"] == __version__
    assert "fastapi" in body["packages"]
    assert body["packages"]["fastapi"]   # 已安裝 → 有版本字串
    assert body["python"]


async def test_check_latest_does_not_crash(client, auth_headers, monkeypatch):
    # mock 掉對外請求，確保端點在 GitHub 不可達時回 error 而非 500
    import app.api.v1.endpoints.system_settings as ss

    async def _boom(*a, **k):
        raise ss.httpx.HTTPError("no network")
    monkeypatch.setattr(ss, "safe_request", _boom)

    r = await client.get("/api/v1/system/version/check-latest", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current"] == __version__
    assert body["latest"] is None
    assert body["update_available"] is False
    assert body["error"]


async def test_version_requires_admin(client):
    # 無 auth → 401（system 路由掛 require_admin）
    r = await client.get("/api/v1/system/version")
    assert r.status_code in (401, 403)
