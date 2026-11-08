"""AI 對話：工具結果撐爆上下文時，使用者的問題不能被丟掉（2026-11-07 正式站台）。

使用者問「198.51.100.111 誰在用」，AI 卻回答整個網段的使用率。正式站台 num_ctx=16384，工具清單＋系統提示詞
實測就用掉約 12,700 token；查一筆資料多的 IP（結果最多 12,000 字）再加一輪，提示詞就超過上限。Ollama 超過時
從最舊的訊息丟起（系統提示詞與最後一則保留）：被丟掉的正好是使用者的問題，模型只看得到工具結果，就照結果講。

兩道防線：
1. 系統提示詞末尾附上這次的問題（Ollama 不丟系統提示詞）
2. 送出前估算總量，超過就先縮短最大的工具結果（有上一輪實際的 prompt_eval_count 就用它當基準），回答註明有截短
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.services import ai as ai_mod

pytestmark = pytest.mark.anyio
Q = "198.51.100.111 誰在用"


def test_system_prompt_carries_the_latest_question() -> None:
    msgs = [{"role": "user", "content": "先前的問題"}, {"role": "assistant", "content": "先前的回答"},
            {"role": "user", "content": Q}]
    _tools, convo = ai_mod._build_chat_context(msgs, "zh-TW")
    assert convo[0]["role"] == "system"
    assert Q in convo[0]["content"]
    assert "先前的問題" not in convo[0]["content"]


def test_fit_context_shrinks_the_largest_tool_result_and_keeps_the_question() -> None:
    tools: list[Any] = []
    convo = [{"role": "system", "content": "s" * 400}, {"role": "user", "content": Q},
             {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "investigate_ip"}}]},
             {"role": "tool", "name": "small", "content": "x" * 500},
             {"role": "tool", "name": "investigate_ip", "content": "y" * 40_000}]
    n = ai_mod._fit_context(convo, tools, num_ctx=8192, provider="ollama")
    assert n == 1
    assert convo[1]["content"] == Q
    assert convo[3]["content"] == "x" * 500, "小的不動"
    assert len(convo[4]["content"]) < 40_000 and "truncated" in convo[4]["content"]
    total = sum((ai_mod._estimate_data_tokens if m["role"] == "tool" else ai_mod.estimate_tokens)(m.get("content") or "") + 8
                for m in convo)
    assert total <= 8192 - max(ai_mod._ANSWER_RESERVE_MIN, 8192 // 16)


def test_fit_context_leaves_a_prompt_that_fits_alone() -> None:
    convo = [{"role": "system", "content": "s"}, {"role": "user", "content": Q},
             {"role": "tool", "name": "t", "content": "y" * 4000}]
    assert ai_mod._fit_context(convo, [], num_ctx=16384, provider="ollama") == 0
    assert convo[2]["content"] == "y" * 4000


def test_tool_results_are_estimated_as_dense_json() -> None:
    """實測 gemma4：12,000 字的 IP 詳細 JSON 是 4,758 token（約 2.5 字元一個），一般估算只算出 3,004。"""
    blob = '{"ip": "10.20.0.40", "n": 12345, "v": "1.2.3"}, ' * 250
    assert ai_mod._estimate_data_tokens(blob) >= len(blob) / 2.6


def test_fit_context_uses_the_measured_prompt_size_when_known() -> None:
    """估算偏高（實測工具清單估算多約 8%）：上一輪實際用量已知時，只估新加的訊息。"""
    convo = [{"role": "system", "content": "s" * 60_000}, {"role": "user", "content": Q},
             {"role": "tool", "name": "t", "content": "y" * 8000}]
    # 估算會超過 16384，但實際上一輪（系統＋問題）只用了 11,000
    assert ai_mod._fit_context(convo, [], num_ctx=16384, provider="ollama", measured=(11_000, 2)) == 0
    assert ai_mod._fit_context([dict(m) for m in convo], [], num_ctx=16384, provider="ollama") == 1


class _Resp:
    def __init__(self, lines: list[str]):
        self.status_code = 200
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


async def test_second_round_still_has_the_question(monkeypatch, db_session, admin_user) -> None:
    from app.services.system_config import LLMConfig
    cfg = LLMConfig(enabled=True, url="http://llm:11434", embedding_model="e", chat_model="m",
                    timeout=30.0, provider="ollama", num_ctx=16384)

    async def _cfg(_s):
        return cfg
    monkeypatch.setattr("app.services.system_config.get_llm_config", _cfg)
    bodies: list[dict[str, Any]] = []
    rounds = [
        [{"message": {"content": "", "tool_calls": [{"function": {"name": "get_ip_detail", "arguments": {"ip": "198.51.100.111"}}}]},
          "done": True, "done_reason": "stop", "prompt_eval_count": 12_700}],
        [{"message": {"content": "srv-a 在用"}, "done": True, "done_reason": "stop", "prompt_eval_count": 15_000}],
    ]

    @asynccontextmanager
    async def _stream(method, url, *, headers=None, json=None, timeout=None):
        bodies.append(json)
        yield _Resp([__import__("json").dumps(c, ensure_ascii=False) for c in rounds[len(bodies) - 1]])
    monkeypatch.setattr(ai_mod, "safe_stream", _stream)

    async def _tool(session, user, name, args):
        return {"ip": "198.51.100.111", "hostname": "srv-a", "blob": "z" * 30_000}
    monkeypatch.setattr("app.mcp.tools.call_tool", _tool)
    events = [ev async for ev in ai_mod.chat_stream(
        db_session, user=admin_user, messages=[{"role": "user", "content": Q}], locale="zh-TW")]
    done = next(e for e in events if e.get("type") == "done")
    assert len(bodies) == 2
    second = bodies[1]["messages"]
    assert Q in second[0]["content"], "問題在系統提示詞裡，Ollama 截斷時不會丟"
    assert any(m.get("role") == "user" and m.get("content") == Q for m in second)
    tool_msg = next(m for m in second if m.get("role") == "tool")
    assert "srv-a" in tool_msg["content"] and "truncated" in tool_msg["content"]
    est = 12_700 + sum(ai_mod._estimate_data_tokens(m.get("content") or "") for m in second[2:])
    assert est <= 16384 - ai_mod._ANSWER_RESERVE_MIN
    assert "16384" in done["answer"], "回答註明有結果被截短（上下文長度）"
