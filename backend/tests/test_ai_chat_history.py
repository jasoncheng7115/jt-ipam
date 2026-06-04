"""AI chat 歷程持久化：每個 user 存自己的對話，admin 可看全部，可設保留天數清除。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.user import User
from app.services import ai_chat_store as store


async def _mk_user(db_session, *, admin=False) -> User:
    from app.core.security import hash_password
    u = User(
        username=f"u-{uuid.uuid4().hex[:8]}",
        email=f"u-{uuid.uuid4().hex[:8]}@test.local",
        display_name="U",
        password_hash=hash_password("TestPassword2026!"),
        auth_provider="local", is_active=True, is_admin=admin,
    )
    db_session.add(u)
    await db_session.flush()
    return u


async def test_save_turn_creates_conversation_with_title(db_session, admin_user):
    conv = await store.save_turn(
        db_session, user_id=admin_user.id, conversation_id=None,
        user_text="192.168.1.1 是什麼裝置？", assistant_text="那是一台 OPNsense 防火牆。",
        model="gpt-oss", elapsed_ms=1234,
    )
    assert conv.user_id == admin_user.id
    assert conv.title and "192.168.1.1" in conv.title
    msgs = await store.get_messages(db_session, conversation_id=conv.id)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].model == "gpt-oss"
    assert msgs[1].elapsed_ms == 1234


async def test_second_turn_appends_to_same_conversation(db_session, admin_user):
    conv = await store.save_turn(
        db_session, user_id=admin_user.id, conversation_id=None,
        user_text="Q1", assistant_text="A1",
    )
    conv2 = await store.save_turn(
        db_session, user_id=admin_user.id, conversation_id=conv.id,
        user_text="Q2", assistant_text="A2",
    )
    assert conv2.id == conv.id
    msgs = await store.get_messages(db_session, conversation_id=conv.id)
    assert [m.content for m in msgs] == ["Q1", "A1", "Q2", "A2"]


async def test_list_conversations_is_per_user(db_session, admin_user):
    other = await _mk_user(db_session)
    await store.save_turn(db_session, user_id=admin_user.id, conversation_id=None,
                          user_text="mine", assistant_text="ok")
    await store.save_turn(db_session, user_id=other.id, conversation_id=None,
                          user_text="theirs", assistant_text="ok")
    mine = await store.list_conversations(db_session, user_id=admin_user.id)
    assert len(mine) == 1
    theirs = await store.list_conversations(db_session, user_id=other.id)
    assert len(theirs) == 1
    assert mine[0].id != theirs[0].id


async def test_delete_conversation_cascades(db_session, admin_user):
    conv = await store.save_turn(db_session, user_id=admin_user.id, conversation_id=None,
                                 user_text="Q", assistant_text="A")
    await store.delete_conversation(db_session, conversation_id=conv.id)
    assert await store.get_conversation(db_session, conversation_id=conv.id) is None
    assert await store.get_messages(db_session, conversation_id=conv.id) == []


async def test_admin_can_list_all(db_session, admin_user):
    other = await _mk_user(db_session)
    await store.save_turn(db_session, user_id=admin_user.id, conversation_id=None,
                          user_text="a", assistant_text="x")
    await store.save_turn(db_session, user_id=other.id, conversation_id=None,
                          user_text="b", assistant_text="y")
    all_convs = await store.list_all_conversations(db_session)
    assert len(all_convs) >= 2
    user_ids = {c.user_id for c in all_convs}
    assert admin_user.id in user_ids and other.id in user_ids


async def test_purge_old_removes_by_retention(db_session, admin_user):
    old = await store.save_turn(db_session, user_id=admin_user.id, conversation_id=None,
                                user_text="old", assistant_text="x")
    fresh = await store.save_turn(db_session, user_id=admin_user.id, conversation_id=None,
                                  user_text="fresh", assistant_text="y")
    # 把 old 的時間推到 100 天前
    old.created_at = datetime.now(UTC) - timedelta(days=100)
    old.updated_at = datetime.now(UTC) - timedelta(days=100)
    await db_session.flush()

    removed = await store.purge_old(db_session, retention_days=90)
    assert removed == 1
    remaining = await store.list_conversations(db_session, user_id=admin_user.id)
    assert [c.id for c in remaining] == [fresh.id]


async def test_purge_disabled_when_retention_zero(db_session, admin_user):
    await store.save_turn(db_session, user_id=admin_user.id, conversation_id=None,
                          user_text="keep", assistant_text="x")
    removed = await store.purge_old(db_session, retention_days=0)  # 0 = 永久保留
    assert removed == 0
