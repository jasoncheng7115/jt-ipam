"""畫面地圖：AI 回答「某個功能／欄位在哪裡」要對得上真的畫面（使用者 2026-10-15）。

起因：問「電路的申請日與到期日要去哪裡查」，AI 說電路沒有這兩個欄位；編輯畫面明明有「開通日」「合約到期」。
地圖從前端原始碼即時解析（services/ui_map），這裡驗證解析出來的東西跟畫面一致，而且新頁面不會漏掉。
"""
from __future__ import annotations

import re

import pytest

from app.services import ui_map

pytestmark = pytest.mark.anyio


def _where(m: dict) -> tuple:
    return tuple(m["menu"]), tuple(m["tabs"]), m["label"]


def test_circuit_dates_are_found_under_advanced_circuits() -> None:
    out = ui_map.search(["到期"], "zh-TW")
    assert (("進階", "電路"), ("電路",), "合約到期") in {_where(m) for m in out["matches"]}
    out = ui_map.search(["開通日"], "zh-TW")
    first = out["matches"][0]
    assert _where(first) == (("進階", "電路"), ("電路",), "開通日")
    assert first["type"] == "field" and first["admin_only"] is False


def test_a_page_name_returns_every_field_so_synonyms_can_be_mapped() -> None:
    """使用者說「申請日」，畫面叫「開通日」：命中頁面名稱時要給整頁欄位，讓模型自己對。"""
    page = ui_map.search(["電路"], "zh-TW")["pages"][0]
    assert page["menu"] == ["進階", "電路"]
    assert {"電路供應商", "電路", "電路類型"} <= set(page["page_tabs"])
    assert {"開通日", "合約到期", "月費 (分)", "DNS 伺服器"} <= set(page["fields"])


def test_answers_follow_the_users_language() -> None:
    en = ui_map.search(["contract"], "en-US")["matches"][0]
    assert _where(en) == (("Advanced Modules", "Circuits"), ("Circuits",), "Contract end")
    ja = {m["label"] for m in ui_map.search(["契約"], "ja-JP")["matches"]}
    assert "契約終了日" in ja
    # 關鍵字跟介面語言不同也找得到（中文問、英文介面）
    assert ui_map.search(["合約到期"], "en-US")["matches"][0]["label"] == "Contract end"


def test_admin_pages_integrations_and_conditions_are_marked() -> None:
    iso = next(m for m in ui_map.search(["全部子網路"], "zh-TW")["matches"] if m["label"] == "全部子網路")
    assert iso["menu"] == ["管理", "外部系統整合", "ISOinsight"] and iso["admin_only"] is True
    req = ui_map.search(["IP 申請"], "zh-TW")["pages"][0]
    assert req["menu"] == ["IP 申請"] and req["admin_only"] is False, "前一個選單項目的條件不可以套到下一個"
    fw = next(p for p in ui_map.build()[0] if p.route == "pfsense_fw")
    assert fw.condition == "integration:pfsense"


def test_detail_pages_say_open_a_row_and_custom_setting_rows_are_read() -> None:
    m = next(m for m in ui_map.search(["上線狀態跟著所屬設備"], "zh-TW")["matches"] if m.get("open_a_row"))
    assert m["menu"][0] in ("IP 位址", "子網路")
    ss = next(m for m in ui_map.search(["上線判定閾值"], "zh-TW")["matches"])
    assert ss["menu"] == ["管理", "系統設定"], "系統設定頁是自訂排版（<label>），也要讀得到"


def test_every_routed_page_is_placed() -> None:
    """新頁面不在選單、不是詳細頁、也沒說明為何不列 → 失敗（不然 AI 永遠找不到它）。"""
    pages, _ = ui_map.build()
    placed = {p.route for p in pages} | set(ui_map.UNLISTED)
    routes = ui_map._routes()
    missing = sorted(set(routes) - placed)
    assert not missing, f"這些頁面沒有歸類（加進選單、DETAIL_PARENTS 或 UNLISTED 並寫理由）：{missing}"
    stale = sorted((set(ui_map.DETAIL_PARENTS) | set(ui_map.UNLISTED)) - set(routes))
    assert not stale, f"清單裡的路由已經不存在：{stale}"
    assert len(pages) > 80 and sum(len(p.elements) for p in pages) > 2000


def test_menu_paths_match_the_nav_path_guard() -> None:
    """跟選單路徑守門測試用同一份選單：管理底下的項目都要在這裡出現。"""
    from tests.test_nav_paths import _labels
    zh = _labels()["zh-TW"]
    pages, strings = ui_map.build()
    admin_children = {p.menu[1].text(strings["zh-TW"]) for p in pages
                      if len(p.menu) == 2 and p.menu[0].key == "nav.admin_section"}
    groups = {zh["integrations"]}                     # 「外部系統整合」是子選單，不是頁面
    assert {x for x in zh["admin_children"] if not re.match(r"^LLM", x)} - groups <= admin_children


async def test_tool_uses_the_users_locale(db_session, admin_user) -> None:
    from app.mcp.tools import TOOLS, UTILITY_TOOLS
    out = await TOOLS["find_ui_location"]["fn"](db_session, user=admin_user, keywords=["電路", "到期"])
    assert out["locale"] in ui_map.LOCALES
    assert any(m["label"] == "合約到期" for m in out["matches"])
    assert "find_ui_location" in UTILITY_TOOLS, "不碰資料：沒有任何權限的帳號也可以問功能在哪"


# ── 可點的頁面連結（使用者 2026-11-04「直接給點選連結」）───────────────────────
# 連結由地圖決定（不是模型寫的網址）；管理員限定的頁面不給一般帳號連結；詳細頁連到清單；
# 頁面支援 ?tab= 時連到那個頁籤。

def test_pages_carry_an_in_app_link() -> None:
    page = ui_map.search(["電路"], "zh-TW", is_admin=False)["pages"][0]
    assert page["link"] == "/advanced/circuits", page
    sa = ui_map.search(["掃描代理"], "zh-TW", is_admin=True)["pages"][0]
    assert sa["link"] == "/scan-agents"


def test_admin_pages_get_no_link_for_other_users() -> None:
    sa = ui_map.search(["掃描代理"], "zh-TW", is_admin=False)["pages"][0]
    assert sa["admin_only"] is True and "link" not in sa, "看不到的頁面不給連結（點了也只會被擋）"


def test_a_tab_links_to_the_tab_when_the_page_supports_it() -> None:
    m = next(m for m in ui_map.search(["跳板主機"], "zh-TW", is_admin=True)["matches"]
             if m["type"] == "tab" and m["menu"][-1] == "掃描代理")
    assert m["link"] == "/scan-agents?tab=jump"


def test_detail_page_matches_link_to_the_list() -> None:
    m = next(m for m in ui_map.search(["上線狀態跟著所屬設備"], "zh-TW", is_admin=True)["matches"]
             if m.get("open_a_row"))
    assert m["link"] in ("/addresses", "/subnets"), m


def test_ui_links_come_only_from_the_ui_map_tool() -> None:
    import json

    from app.services.ai import ui_links
    out = ui_map.search(["掃描代理", "跳板主機"], "zh-TW", is_admin=True)
    convo = [
        {"role": "user", "content": "掃描代理在哪"},
        {"role": "tool", "name": "list_subnets", "content": json.dumps({"link": "/evil", "label": "x"})},
        {"role": "tool", "name": "find_ui_location", "content": json.dumps(out, ensure_ascii=False)},
        {"role": "tool", "name": "find_ui_location", "content": json.dumps({"error": "boom"})},
    ]
    links = ui_links(convo)
    assert links and len(links) <= 4
    assert links[0] == {"label": "掃描代理", "href": "/scan-agents"}
    assert all(x["href"].startswith("/") and not x["href"].startswith("//") for x in links)
    assert "/evil" not in {x["href"] for x in links}, "別的工具的輸出不可以變成連結"
    assert len({x["href"] for x in links}) == len(links), "同一個連結只出現一次"


async def test_chat_stream_sends_the_links_and_history_keeps_them(client, auth_headers, monkeypatch) -> None:
    import json

    from app.api.v1.endpoints import ai as ai_ep
    from app.services import system_config
    from app.services.system_config import LLMConfig

    async def _cfg(_s):
        return LLMConfig(enabled=True, url="http://llm.example.test:11434", embedding_model="e", chat_model="m",
                         timeout=30.0)
    monkeypatch.setattr(system_config, "get_llm_config", _cfg)
    found = ui_map.search(["掃描代理"], "zh-TW", is_admin=True)

    async def fake_stream(*a, **kw):
        yield {"type": "done", "answer": "在「管理 › 掃描代理」。", "model": "m", "elapsed_ms": 10,
               "trace_messages": [{"role": "tool", "name": "find_ui_location",
                                   "content": json.dumps(found, ensure_ascii=False)}]}
    monkeypatch.setattr(ai_ep.ai_service, "chat_stream", fake_stream)

    r = await client.post("/api/v1/ai/chat/stream", headers=auth_headers,
                          json={"messages": [{"role": "user", "content": "掃描代理在哪"}]})
    done = [json.loads(x[6:]) for x in r.text.splitlines() if x.startswith("data: ")][-1]
    assert done["links"][0] == {"label": "掃描代理", "href": "/scan-agents"}
    conv = (await client.get(f"/api/v1/ai/chat/conversations/{done['conversation_id']}", headers=auth_headers)).json()
    assert conv["messages"][-1]["links"][0]["href"] == "/scan-agents", "重新打開對話也看得到按鈕"
    assert conv["messages"][0]["links"] == [], "使用者的訊息沒有連結"
