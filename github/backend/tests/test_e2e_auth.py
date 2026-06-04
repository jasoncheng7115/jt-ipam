"""E2E：認證 + RBAC 行為。"""

from __future__ import annotations


async def test_login_returns_token(client, admin_user):  # type: ignore[no-untyped-def]
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": admin_user.username, "password": "TestPassword2026!"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mfa_required"] is False
    assert data["access_token"] and len(data["access_token"]) > 50
    assert data["expires_in"] >= 60


async def test_login_invalid_credentials(client, admin_user):  # type: ignore[no-untyped-def]
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": admin_user.username, "password": "wrong-password"},
    )
    assert resp.status_code == 401


async def test_unknown_user_returns_401_too(client):  # type: ignore[no-untyped-def]
    """A07：user enumeration 防護 — 找不到 user 也回 401（不是 404）。"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "ghost-user-zzz", "password": "anything-12345"},
    )
    assert resp.status_code == 401


async def test_me_requires_auth(client):  # type: ignore[no-untyped-def]
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_me_with_token(client, auth_headers, admin_user):  # type: ignore[no-untyped-def]
    resp = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["username"] == admin_user.username
    assert data["is_admin"] is True
    # password_hash / totp 不應外洩
    assert "password_hash" not in data
    assert "totp_secret_enc" not in data
