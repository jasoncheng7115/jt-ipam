"""AI 判讀、AI 巡檢可以接不同的 LLM Server（使用者 2026-11-05）。

以前三種用途只能共用一台 LLM Server、各自換模型：
1. 對話＋嵌入（基本設定）
2. AI 判讀（未授權 IP 判讀、IP 調查、防火牆規則異動解讀、IP 變更評估、攻擊面）
3. AI 巡檢

現在 2 和 3 可以各自指定供應商、位址、金鑰與模型；**沒設就繼承 1**，既有安裝升上來行為不變。

守的是：
- 繼承：判讀／巡檢沒設位址 → 用對話的供應商、位址、金鑰（模型、上下文長度照舊各自可換）
- 指定位址 → 供應商、金鑰也用它自己的，請求真的送到那一台
- 位址清掉＝回到繼承，供應商與金鑰一起清掉（不留用不到的金鑰）
- 金鑰加密儲存、各用各的 AAD；設定頁只回「有沒有設」；稽核紀錄不會出現明文
- 模型清單、思考檢查照角色連到對的那一台
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.models.address import IPAddress
from app.models.section import Section
from app.models.subnet import Subnet
from app.services import ai as ai_mod
from app.services.system_config import get_llm_config, role_config, set_llm_config

pytestmark = pytest.mark.anyio


class _Resp:
    status_code = 200
    text = ""

    @staticmethod
    def json() -> dict[str, Any]:
        return {"message": {"content": '{"items": []}'},
                "choices": [{"message": {"content": '{"items": []}'}}]}


@pytest.fixture
def sent(monkeypatch) -> list[dict[str, Any]]:
    """攔下送給 LLM 的請求：位址、標頭、主體。"""
    calls: list[dict[str, Any]] = []

    async def _fake_request(method, url, **kw):
        calls.append({"url": url, "headers": kw.get("headers") or {}, "body": kw.get("json")})
        return _Resp()

    async def _fake_stream(url, body, wait, on_chunk, headers=None, provider="ollama"):
        calls.append({"url": url, "headers": headers or {}, "body": body})
        await on_chunk('{"items": []}', "content")
        return '{"items": []}'

    monkeypatch.setattr(ai_mod, "safe_request", _fake_request)
    monkeypatch.setattr(ai_mod, "_raw_chat_streamed", _fake_stream)
    return calls


async def _llm(db, **kw) -> None:
    await set_llm_config(db, enabled=True, provider="ollama", url="http://chat.invalid:11434",
                         chat_model="chat-model:8b", num_ctx=8192, **kw)
    await db.commit()


async def _address(db) -> None:
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db.add(sec)
    await db.flush()
    sn = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db.add(sn)
    await db.flush()
    db.add(IPAddress(subnet_id=sn.id, ip="198.51.100.88", hostname="judge-me", state="used"))
    await db.commit()


# ── 設定與繼承 ────────────────────────────────────────────────────────

async def test_roles_inherit_the_chat_server_by_default(db_session) -> None:
    await _llm(db_session, api_key="sk-chat", ai_interpret_model="judge:32b")
    cfg = await get_llm_config(db_session)
    for role in ("interpret", "audit"):
        rc = role_config(cfg, role)
        assert (rc.provider, rc.url, rc.api_key) == ("ollama", "http://chat.invalid:11434", "sk-chat"), role
    assert role_config(cfg, "interpret").chat_model == "judge:32b"
    assert role_config(cfg, "audit").chat_model == "chat-model:8b", "模型沒設也繼承"
    assert role_config(cfg, "audit").num_ctx == 8192
    assert role_config(cfg, "chat") is cfg


async def test_a_role_can_use_its_own_server(db_session) -> None:
    await _llm(db_session, api_key="sk-chat",
               ai_audit_provider="openai", ai_audit_url=" https://audit.invalid/v1/ ", ai_audit_api_key="sk-audit",
               ai_audit_model="big-audit", ai_audit_num_ctx=65536)
    cfg = await get_llm_config(db_session)
    rc = role_config(cfg, "audit")
    assert (rc.provider, rc.url, rc.api_key, rc.chat_model, rc.num_ctx) == (
        "openai", "https://audit.invalid/v1", "sk-audit", "big-audit", 65536)
    ri = role_config(cfg, "interpret")
    assert (ri.provider, ri.url, ri.api_key) == ("ollama", "http://chat.invalid:11434", "sk-chat"), "互不影響"


async def test_clearing_the_url_goes_back_to_inheriting_and_drops_the_key(db_session) -> None:
    from app.models.system_setting import SystemSetting
    from app.services.system_config import LLM_KEY
    await _llm(db_session, ai_interpret_provider="openai", ai_interpret_url="https://judge.invalid/v1",
               ai_interpret_api_key="sk-judge")
    row = await db_session.get(SystemSetting, LLM_KEY)
    assert "sk-judge" not in str(row.value), "金鑰加密儲存"
    await set_llm_config(db_session, ai_interpret_url="")
    await db_session.commit()
    cfg = await get_llm_config(db_session)
    assert (cfg.ai_interpret_url, cfg.ai_interpret_provider, cfg.ai_interpret_api_key) == (None, None, None)
    assert role_config(cfg, "interpret").url == "http://chat.invalid:11434"
    await db_session.refresh(row)
    assert not row.value.get("ai_interpret_api_key_enc"), "用不到的金鑰不要留著"


async def test_role_keys_use_their_own_aad(db_session) -> None:
    """密文不能搬到別的欄位使用（例如把判讀的金鑰貼到對話的欄位）。"""
    from app.models.system_setting import SystemSetting
    from app.services.system_config import LLM_KEY
    await _llm(db_session, ai_interpret_provider="openai", ai_interpret_url="https://judge.invalid/v1",
               ai_interpret_api_key="sk-judge")
    row = await db_session.get(SystemSetting, LLM_KEY)
    value = dict(row.value)
    value["api_key_enc"] = value["ai_interpret_api_key_enc"]
    row.value = value
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await db_session.commit()
    from app.services import system_config
    system_config._bust()
    cfg = await get_llm_config(db_session)
    assert cfg.api_key is None and cfg.ai_interpret_api_key == "sk-judge"


async def test_api_exposes_patches_and_never_logs_the_key(client, auth_headers, db_session) -> None:
    await _llm(db_session)
    r = await client.patch("/api/v1/system/llm", headers=auth_headers, json={
        "ai_interpret_provider": "openai", "ai_interpret_url": "https://judge.invalid/v1",
        "ai_interpret_api_key": "sk-judge-secret"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["ai_interpret_provider"], d["ai_interpret_url"], d["ai_interpret_api_key_set"]) == (
        "openai", "https://judge.invalid/v1", True)
    assert "sk-judge-secret" not in r.text
    assert (d["ai_audit_url"], d["ai_audit_api_key_set"]) == ("", False)
    logs = (await client.get("/api/v1/audit", headers=auth_headers, params={"page_size": 5})).text
    assert "sk-judge-secret" not in logs, "稽核紀錄不可以有金鑰明文"
    bad = await client.patch("/api/v1/system/llm", headers=auth_headers, json={"ai_audit_provider": "gemini"})
    assert bad.status_code == 422


# ── 請求真的送到那一台 ────────────────────────────────────────────────

async def test_interpret_goes_to_the_interpret_server(db_session, admin_user, sent) -> None:
    from app.services.ip_triage import triage_ip
    await _address(db_session)
    await _llm(db_session, ai_interpret_provider="openai", ai_interpret_url="https://judge.invalid/v1",
               ai_interpret_api_key="sk-judge", ai_interpret_model="judge:32b")
    out = await triage_ip(db_session, admin_user, "198.51.100.88")
    assert sent[-1]["url"].startswith("https://judge.invalid/v1/chat/completions")
    assert sent[-1]["headers"].get("Authorization") == "Bearer sk-judge"
    assert sent[-1]["body"]["model"] == "judge:32b"
    assert out["model"] == "judge:32b"


async def test_interpret_inherits_the_chat_server(db_session, admin_user, sent) -> None:
    from app.services.ip_triage import triage_ip
    await _address(db_session)
    await _llm(db_session)
    await triage_ip(db_session, admin_user, "198.51.100.88")
    assert sent[-1]["url"].startswith("http://chat.invalid:11434/api/chat")
    assert sent[-1]["body"]["model"] == "chat-model:8b"


async def test_audit_goes_to_the_audit_server(db_session, sent) -> None:
    await _llm(db_session, ai_audit_url="http://audit.invalid:11434", ai_audit_model="audit:70b")
    await ai_mod.raw_chat(db_session, "hi", role="audit", force_json=True)
    assert sent[-1]["url"].startswith("http://audit.invalid:11434/api/chat")
    assert sent[-1]["body"]["model"] == "audit:70b"
    await ai_mod.raw_chat(db_session, "hi")
    assert sent[-1]["url"].startswith("http://chat.invalid:11434/api/chat"), "沒指定角色＝對話設定"


async def test_ai_audit_run_uses_the_audit_role(db_session) -> None:
    import inspect

    from app.services import ai_audit
    src = inspect.getsource(ai_audit)
    assert 'role="audit"' in src, "巡檢呼叫 raw_chat 要帶 role=\"audit\""


async def test_model_list_and_thinking_check_follow_the_role(client, auth_headers, db_session, monkeypatch) -> None:
    from app.api.v1.endpoints import system_settings as ss
    urls: list[str] = []

    class _Tags:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict[str, Any]:
            return {"models": [{"name": "audit:70b"}]}

    async def _fake(method, url, **kw):
        urls.append(url)
        return _Tags()
    monkeypatch.setattr(ss, "safe_request", _fake)
    await _llm(db_session, ai_audit_url="http://audit.invalid:11434")
    r = await client.get("/api/v1/system/llm/models", headers=auth_headers, params={"role": "audit"})
    assert r.status_code == 200 and urls[-1] == "http://audit.invalid:11434/api/tags"
    await client.get("/api/v1/system/llm/models", headers=auth_headers)
    assert urls[-1] == "http://chat.invalid:11434/api/tags"
    assert (await client.get("/api/v1/system/llm/models", headers=auth_headers,
                             params={"role": "bogus"})).status_code == 422

    probed: list[tuple[str, str]] = []

    async def _probe(session, model=None, role="chat"):
        rc = role_config(await get_llm_config(session), role)
        probed.append((rc.url, model or rc.chat_model))
        return {"ok": True, "thinking": False, "model": model or rc.chat_model, "rejected_params": []}
    monkeypatch.setattr(ai_mod, "probe_thinking", _probe)
    r = await client.get("/api/v1/ai/thinking-check", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert ("http://audit.invalid:11434", "chat-model:8b") in probed, "同模型名但不同 server 也要問"
    assert {x["role"] for x in r.json()["results"]} == {"chat", "audit"}, "判讀跟對話完全一樣，只問一次"


async def test_system_transfer_carries_every_llm_key(db_session) -> None:
    """搬機（匯出／匯入）要把設定裡每一把加密的金鑰解開帶走、到新主機再加密；沒列進清單的到了別台就解不開。"""
    from app.models.system_setting import SystemSetting
    from app.services.system_config import LLM_KEY, _dec, _role_key_aad
    from app.services.system_transfer.secrets import _SETTINGS_V1, transform_settings_in, transform_settings_out
    await _llm(db_session, api_key="sk-chat",
               ai_interpret_provider="openai", ai_interpret_url="https://judge.invalid/v1", ai_interpret_api_key="sk-judge",
               ai_audit_provider="openai", ai_audit_url="https://audit.invalid/v1", ai_audit_api_key="sk-audit")
    from app.services.system_config import rotate_mcp_api_key
    await rotate_mcp_api_key(db_session, principal_user_id=uuid.uuid4(), expires_in_days=30)
    row = await db_session.get(SystemSetting, LLM_KEY)
    enc_fields = {k for k, v in row.value.items() if k.endswith("_enc") and v}
    assert enc_fields <= set(_SETTINGS_V1["llm"]), f"搬機清單漏了：{enc_fields - set(_SETTINGS_V1['llm'])}"
    out = transform_settings_out("llm", row.value)
    assert out["ai_interpret_api_key_enc"] == {"__plain__": "sk-judge"}
    back = transform_settings_in("llm", out)
    assert _dec(back["ai_audit_api_key_enc"], _role_key_aad("audit")) == "sk-audit"
