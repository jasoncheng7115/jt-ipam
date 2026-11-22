"""同一個 email 可以同時屬於本機帳號與 LDAP 帳號。

真實事故：使用者的本機帳號 alice-local 與 LDAP 帳號 alice 共用同一個公司 email。
`users.email` 是唯一鍵 → LDAP 自動建帳號在 commit 時撞 unique，登入回 500（LDAP
帳密其實已經驗過了），而且該帳號永遠建不起來。

email 只是聯絡資訊，身分識別是 username。migration 0120 拿掉唯一鍵後，這裡釘住
兩件事：重複 email 真的能存在；且以 email 登入不會因為多筆而炸掉。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.user import User
from app.services.auth import InvalidCredentials, authenticate


async def _mk(session, username: str, email: str, provider: str, password: str | None):
    u = User(
        username=username, email=email, auth_provider=provider, is_active=True,
        password_hash=hash_password(password) if password else None,
    )
    session.add(u)
    await session.flush()
    return u


@pytest.mark.anyio
async def test_same_email_allowed_across_realms(db_session) -> None:
    """同一 email 的本機帳號與 LDAP 帳號可以並存（唯一鍵已移除）。"""
    email = f"dup-{uuid.uuid4().hex[:8]}@example.com"
    await _mk(db_session, f"local-{uuid.uuid4().hex[:6]}", email, "local", "Test12345678!")
    await _mk(db_session, f"ldapuser-{uuid.uuid4().hex[:6]}@ldap", email, "ldap", None)
    await db_session.flush()

    rows = (await db_session.execute(
        select(User).where(User.email == email))).scalars().all()
    assert len(rows) == 2, "email 仍是唯一鍵 —— LDAP 自動建帳號會再次 500"


@pytest.mark.anyio
async def test_email_login_not_ambiguous(db_session) -> None:
    """以 email 登入時不可因多筆同 email 炸掉（原本 scalar_one_or_none 會 500）。

    本機領域只看非 LDAP 帳號 → 找得到本機那筆，密碼錯就是乾淨的 InvalidCredentials，
    不是 MultipleResultsFound。
    """
    email = f"dup-{uuid.uuid4().hex[:8]}@example.com"
    await _mk(db_session, f"local-{uuid.uuid4().hex[:6]}", email, "local", "Test12345678!")
    await _mk(db_session, f"ldapuser-{uuid.uuid4().hex[:6]}@ldap", email, "ldap", None)
    await db_session.flush()

    with pytest.raises(InvalidCredentials):
        await authenticate(db_session, username=email, password="wrong-password",
                           realm="local", actor_ip=None, actor_user_agent=None,
                           request_id=None)

    user = await authenticate(db_session, username=email, password="Test12345678!",
                              realm="local", actor_ip=None, actor_user_agent=None,
                              request_id=None)
    assert user.auth_provider == "local", "以 email 登入本機領域卻選到 LDAP 帳號"
