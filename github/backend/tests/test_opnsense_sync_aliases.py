"""OPNsense alias 同步協調邏輯（mock 掉對外的 list_aliases）：
upsert 既有 / 新建 / 移除遠端已不存在者；正確解析 type / enabled / content。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.firewall import OPNsenseFirewall, OPNsenseSyncedAlias
from app.services import opnsense_firewall as ofw


async def _mk_fw(session) -> OPNsenseFirewall:
    fw = OPNsenseFirewall(
        name="fw-test", api_url="https://fw.example.com",
        api_key_enc=b"x", api_key_nonce=b"x",
        api_secret_enc=b"x", api_secret_nonce=b"x",
    )
    session.add(fw)
    await session.flush()
    return fw


def _patch_aliases(monkeypatch, aliases):
    async def _fake(_fw):
        return aliases
    monkeypatch.setattr(ofw, "list_aliases", _fake)


async def _rows(session, fw_id):
    return {
        a.name: a for a in (await session.execute(
            select(OPNsenseSyncedAlias).where(OPNsenseSyncedAlias.firewall_id == fw_id)
        )).scalars().all()
    }


async def test_sync_creates_and_parses(db_session, monkeypatch):
    fw = await _mk_fw(db_session)
    _patch_aliases(monkeypatch, [
        {"uuid": "u1", "name": "web_servers",
         "type": {"host": {"selected": 1}, "network": {"selected": 0}},
         "description": "web", "enabled": "1",
         "content": {"10.0.0.1": {"selected": 1}, "10.0.0.2": {"selected": 1}}},
        {"uuid": "u2", "name": "db", "type": "network", "enabled": "0",
         "content": "10.0.1.0/24"},
    ])
    res = await ofw.sync_aliases(db_session, fw)
    assert res["count"] == 2
    rows = await _rows(db_session, fw.id)

    web = rows["web_servers"]
    assert web.alias_type == "host"
    assert web.enabled is True
    assert web.content == ["10.0.0.1", "10.0.0.2"]
    assert web.member_count == 2
    assert web.opn_uuid == "u1"
    assert web.description == "web"

    db = rows["db"]
    assert db.alias_type == "network"
    assert db.enabled is False
    assert db.content == ["10.0.1.0/24"]
    assert db.member_count == 1


async def test_sync_updates_and_deletes_missing(db_session, monkeypatch):
    fw = await _mk_fw(db_session)
    _patch_aliases(monkeypatch, [
        {"uuid": "u1", "name": "web_servers", "type": "host", "enabled": "1",
         "content": {"10.0.0.1": {"selected": 1}}},
        {"uuid": "u2", "name": "db", "type": "network", "enabled": "1",
         "content": "10.0.1.0/24"},
    ])
    await ofw.sync_aliases(db_session, fw)

    # 第二次：web_servers 內容變多，db 從遠端消失
    _patch_aliases(monkeypatch, [
        {"uuid": "u1", "name": "web_servers", "type": "host", "enabled": "1",
         "content": {"10.0.0.1": {"selected": 1}, "10.0.0.9": {"selected": 1}}},
    ])
    res = await ofw.sync_aliases(db_session, fw)
    assert res["count"] == 1
    await db_session.flush()
    rows = await _rows(db_session, fw.id)
    assert set(rows) == {"web_servers"}          # db 已被移除
    assert rows["web_servers"].member_count == 2  # 已更新
    assert "10.0.0.9" in rows["web_servers"].content
