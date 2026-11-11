"""帳號的 Email 改成選用（2026-10-22 使用者：「本機帳號 email 一定要必填嗎」，例：給電視用的唯讀帳號）。

email 不是身分（username 才是），只拿來寄通知信；寄信本來就略過空的收件人。
- 本機帳號可以不填；填了要像個 email
- 空的 email 不可以讓「以 email 登入」比中（空字串不是一個 email）
- LDAP 目錄沒有 email 時不再自動塞假的「帳號@ldap.local」（通知信會寄到不存在的地址被退信）；
  舊資料裡的 @ldap.local 不寄
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.anyio


def _body(**kw):  # type: ignore[no-untyped-def]
    return {"username": f"tv-{uuid.uuid4().hex[:6]}", "password": "TestPassword2026!", **kw}


async def test_local_account_without_email(client, auth_headers) -> None:
    r = await client.post("/api/v1/users", json=_body(), headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["email"] == ""
    r = await client.post("/api/v1/users", json=_body(email="  "), headers=auth_headers)
    assert r.status_code == 201 and r.json()["email"] == ""


async def test_email_if_given_must_look_like_one(client, auth_headers) -> None:
    r = await client.post("/api/v1/users", json=_body(email="not-an-email"), headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "user_email_invalid"
    ok = await client.post("/api/v1/users", json=_body(email=" TV@Example.com "), headers=auth_headers)
    assert ok.status_code == 201 and ok.json()["email"] == "TV@Example.com"


async def test_email_can_be_cleared(client, auth_headers) -> None:
    uid = (await client.post("/api/v1/users", json=_body(email="a@example.com"), headers=auth_headers)).json()["id"]
    r = await client.patch(f"/api/v1/users/{uid}", json={"email": ""}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["email"] == ""
    bad = await client.patch(f"/api/v1/users/{uid}", json={"email": "nope"}, headers=auth_headers)
    assert bad.status_code == 422


async def test_empty_email_never_matches_a_login(client, auth_headers) -> None:
    await client.post("/api/v1/users", json=_body(), headers=auth_headers)
    r = await client.post("/api/v1/auth/login", json={"username": "", "password": "TestPassword2026!"})
    assert r.status_code in (401, 422)


def test_placeholder_ldap_addresses_are_not_mailed() -> None:
    from app.services.notification import deliverable_email
    assert deliverable_email("jason@example.com")
    assert not deliverable_email("jason@ldap.local")
    assert not deliverable_email("")
    assert not deliverable_email(None)


async def test_ldap_user_without_email_gets_none_invented(db_session, admin_user, monkeypatch) -> None:
    from app.models.user import User
    from app.services import auth as auth_mod
    from app.services import ldap_auth
    from app.services.system_config import set_ldap_config

    await set_ldap_config(db_session, data={"enabled": True, "server_url": "ldap://192.0.2.10",
                                            "base_dn": "dc=example,dc=net", "user_filter": "(uid={username})"},
                          updated_by_user_id=admin_user.id)
    await db_session.commit()

    async def fake_auth(cfg, account, password):  # type: ignore[no-untyped-def]
        return ldap_auth.LDAPUserInfo(dn=f"uid={account},dc=example,dc=net", username=account, email=None,
                                      display_name=account, is_admin=False, raw_attrs={})
    monkeypatch.setattr(ldap_auth, "authenticate", fake_auth)
    user = await auth_mod.authenticate(db_session, username="noemail", password="pw-irrelevant-2026", realm="ldap",
                                       actor_ip=None, actor_user_agent=None, request_id=None)
    row = await db_session.get(User, user.id)
    assert row.email == ""


async def test_account_without_email_can_log_in(client, auth_headers) -> None:
    """2026-11-11 客戶：清空 email 之後登入 500 —— /me 的格式還要求 email 至少 3 個字元。
    以前只驗了「建得起來」，沒驗「登得進去」。"""
    body = _body()
    assert (await client.post("/api/v1/users", json=body, headers=auth_headers)).status_code == 201
    r = await client.post("/api/v1/auth/login", json={"username": body["username"], "password": body["password"]})
    assert r.status_code == 200, r.text
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.status_code == 200, me.text
    assert me.json()["email"] == ""


@pytest.mark.parametrize("addr", ["admin@localhost", "admin@local", "ops@lan"])
async def test_internal_single_label_domains_are_accepted(client, auth_headers, addr) -> None:
    """安裝程式建的 admin 預設是 admin@localhost：網域沒有「.」也要過，否則改密碼時連帶被 email 擋下
    （客戶只好把 email 清空，結果就登不進去）。內部常見 .local / .lan 這類單段網域。"""
    uid = (await client.post("/api/v1/users", json=_body(), headers=auth_headers)).json()["id"]
    r = await client.patch(f"/api/v1/users/{uid}", json={"email": addr, "password": "AnotherPassword2026!"},
                           headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["email"] == addr
