"""不支援 Ollama 原生 tool_calls 的模型（如 gemma）會把工具呼叫寫成文字。

後援解析器要能：把那段文字還原成結構化 tool_calls（名稱對得上已知工具、參數正確），
一般文句不可誤判成呼叫，並能辨識「無法還原的外洩」以便改顯示友善訊息（而非原始亂碼）。
"""

from __future__ import annotations

from app.services.ai import (
    _inline_tool_calls,
    _looks_like_tool_leak,
    _parse_inline_args,
    _tool_leak_message,
)


def test_gemma_call_syntax_recovered_to_oui_lookup():
    s = "call:ouilookup(mac:</~/>3c:ec:ef:7e:76:43</~/>)<toolcall>"
    assert _looks_like_tool_leak(s) is True
    calls = _inline_tool_calls(s, None)
    assert calls == [{"function": {"name": "oui_lookup", "arguments": {"mac": "3c:ec:ef:7e:76:43"}}}]


def test_json_tool_call_recovered():
    s = '{"name": "get_subnet_detail", "arguments": {"subnet_cidr": "192.168.1.0/24"}}'
    calls = _inline_tool_calls(s, None)
    assert calls == [{"function": {"name": "get_subnet_detail",
                                   "arguments": {"subnet_cidr": "192.168.1.0/24"}}}]


def test_normal_prose_is_not_a_tool_call():
    n = "You can call: support and we will help (really)."
    assert _looks_like_tool_leak(n) is False
    assert _inline_tool_calls(n, None) == []


def test_unknown_tool_name_not_accepted():
    # 名稱對不上任何已知工具 → 不還原（避免亂執行）
    s = "call:definitely_not_a_real_tool(x:1)<toolcall>"
    assert _inline_tool_calls(s, None) == []


def test_allowed_filter_blocks_disallowed_tool():
    s = "call:oui_lookup(mac:</~/>3c:ec:ef:7e:76:43</~/>)<toolcall>"
    assert _inline_tool_calls(s, allowed=set()) == []  # 該使用者無可用工具


def test_parse_inline_args_numbers_and_mac():
    args = _parse_inline_args("count:3, consecutive:true, cidr:</~/>10.0.0.0/24</~/>")
    assert args == {"count": 3, "consecutive": True, "cidr": "10.0.0.0/24"}


def test_tool_leak_message_localized_and_neutral():
    zh = _tool_leak_message("zh-TW")
    en = _tool_leak_message("en-US")
    assert "工具呼叫" in zh and "tool call" in en
    # 不可誤導成「模型不支援」或叫使用者換模型（模型其實支援，只是偶發外洩）
    for msg in (zh, en):
        assert "不支援" not in msg
        assert "switch" not in msg.lower()
