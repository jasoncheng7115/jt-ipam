"""語意搜尋與對話：預設走自架 Ollama，本地推論不外送（規格 §11.1 / §11.3）。

設計：
- 透過 LLM HTTP API 取得 embedding（Ollama `/api/embeddings`、OpenAI 相容 `/v1/embeddings`）
- 寫入時 / 排程時對 Subnet / IPAddress / Device 的 description 計算向量
- /api/v1/search/semantic?q=... 走 cosine 相似度（pgvector ivfflat）

供應商：`ollama`（預設）或 `openai`（OpenAI 相容端點）。**預設不變是刻意的** ——
接外部服務等於把網段、主機名稱、拓樸送出去，那要使用者明確選擇。路徑、回應結構、
可送的參數三處都不同，各自在 `chat_url` / `extract_reply` / `chat_body` 處理。

OWASP A04 / A06：LLM URL 走 safe_request（私網允許）；任何回到該端點之外的呼叫都會被擋住。
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.safe_http import UnsafeOutboundURL, safe_request, safe_stream, transport_detail
from app.services.prompt_safety import DATA_RULE_EN

log = logging.getLogger(__name__)


class AINotConfigured(RuntimeError):
    pass


class AIError(RuntimeError):
    """模型那一端的錯誤。訊息是給日誌與管理員看的原文；給一般帳號看的是 `code`（ai_error_event）。"""

    code = "ai_failed"


class AIBlocked(AIError):
    """LLM 位址被出站防護擋下。"""

    code = "ai_ssrf_blocked"


class AIUnreachable(AIError):
    """連不上 LLM 伺服器（名稱解析、拒絕連線、TLS…）。"""

    code = "ai_unreachable"


class AITimeout(AIError):
    """LLM 伺服器沒有在時限內回覆完。"""

    code = "ai_timeout"


class AIUpstream(AIError):
    """LLM 伺服器回了錯誤（HTTP 狀態、串流裡的 error）。"""

    code = "ai_upstream_error"


class EmbeddingDimMismatch(AIError):
    """嵌入模型的輸出維度跟資料庫欄位不合（使用者 2026-10-13）。

    以前丟的是 `Embedding dim mismatch ... (adjust EMBEDDING_DIM or migration vector(N))`：叫使用者去改
    設定檔與 migration，網頁上根本做不到；「檢查維度」也因此走到「取不到向量」、拿不到維度。
    """

    code = "ai_embedding_dim_mismatch"

    def __init__(self, got: int, expected: int) -> None:
        self.got, self.expected = got, expected
        super().__init__(f"嵌入模型輸出 {got} 維，目前需要 {expected} 維：請改用 {expected} 維的嵌入模型")

    @property
    def params(self) -> dict[str, int]:
        return {"got": self.got, "expected": self.expected}


#: 錯誤代碼的退路文字（前端有翻譯 errors.<code> 就不會用到）
_AI_PUBLIC: dict[str, str] = {
    "ai_failed": "AI 判讀失敗",
    "ai_disabled": "LLM 未啟用",
    "ai_ssrf_blocked": "LLM 伺服器的位址被出站防護擋下，請管理員檢查 LLM 設定",
    "ai_unreachable": "連不上 LLM 伺服器",
    "ai_timeout": "LLM 伺服器沒有在時限內回覆",
    "ai_upstream_error": "LLM 伺服器回報錯誤",
}


def ai_error_code(exc: BaseException) -> str:
    import asyncio
    if isinstance(exc, AIError):
        return exc.code
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "ai_timeout"
    return "ai_failed"


def ai_error_event(exc: BaseException | None, *, admin: bool, code: str | None = None,
                   reason: str | None = None) -> dict[str, Any]:
    """串流與判讀的錯誤：`{"code", "params", "detail"}`。

    CodeQL 判讀（2026-10-01，#11／#15／#16）：以前把例外原文直接給任何登入帳號看 —— 內部 LLM 主機名稱、
    出站防護規則、上游 401 回應的前 200 字（有的閘道會回遮罩過的金鑰）。現在一般帳號只拿到代碼（前端照
    語系翻譯）；**管理員**另外附上原因（`params.reason`），因為診斷要靠它。完整原因一律寫進日誌。
    """
    code = code or (ai_error_code(exc) if exc is not None else "ai_failed")
    if exc is not None:
        log.warning("AI call failed (%s)", code, exc_info=exc)
    params: dict[str, Any] = {}
    if admin:
        raw = reason if reason is not None else (str(exc) if exc is not None else "")
        if raw:
            params["reason"] = raw[:300]
    return {"code": code, "params": params, "detail": _AI_PUBLIC.get(code, _AI_PUBLIC["ai_failed"])}


# 單一工具結果回填上下文的字元上限（避免超大清單拖慢/逾時模型）
_TOOL_RESULT_CAP = 12000


def _chat_options(cfg: Any) -> dict[str, Any]:
    """Ollama 對話請求的 options：低溫度減少亂插字；num_ctx 有設才帶（None＝用模型/Ollama 預設）。"""
    opts: dict[str, Any] = {"temperature": 0.2}
    n = getattr(cfg, "num_ctx", None)
    if n:
        opts["num_ctx"] = int(n)
    return opts

# ── 供應商抽象（Ollama 原生 vs OpenAI 相容）────────────────────────────
#
# 預設 ollama。改成可選 OpenAI 相容之後，同一份設定能接 ChatGPT、vLLM、LM Studio、
# OpenRouter 等 —— Ollama 自己也提供 /v1 相容層。
#
# **這是資料出境的決定**：本專案的賣點是「自架 LLM、資料不外流」，接雲端等於把網段、
# 主機名稱、拓樸送給外部服務。所以預設不變，要使用者明確選擇。

def _openai_base(url: str) -> str:
    """OpenAI 相容的 base。使用者填 `.../v1` 或不填都要正確 —— 直接接字串會變成
    `/v1/v1/chat/completions`，那種錯誤只會在實際呼叫時才炸。"""
    u = url.rstrip("/")
    return u if u.endswith("/v1") else f"{u}/v1"


def chat_url(url: str, provider: str) -> str:
    if provider == "openai":
        return f"{_openai_base(url)}/chat/completions"
    return f"{url.rstrip('/')}/api/chat"


def embedding_url(url: str, provider: str) -> str:
    if provider == "openai":
        return f"{_openai_base(url)}/embeddings"
    return f"{url.rstrip('/')}/api/embeddings"


def models_url(url: str, provider: str) -> str:
    if provider == "openai":
        return f"{_openai_base(url)}/models"
    return f"{url.rstrip('/')}/api/tags"


def parse_models(data: dict[str, Any], provider: str) -> list[dict[str, Any]]:
    """把模型清單正規化成設定頁下拉要的形狀。

    Ollama 回 `{"models":[{"name":..., "details":{...}}]}`，
    OpenAI 相容回 `{"data":[{"id":...}]}`（沒有大小／參數量這些資訊）。
    結構不如預期時回空清單，不要讓設定頁整頁掛掉。
    """
    if provider == "openai":
        rows = data.get("data")
        if not isinstance(rows, list):
            return []
        return [
            {"name": m.get("id"), "size": None, "modified_at": None,
             "family": m.get("owned_by"), "parameter_size": None}
            for m in rows if isinstance(m, dict) and m.get("id")
        ]
    rows = data.get("models")
    if not isinstance(rows, list):
        return []
    return [
        {"name": m.get("name"), "size": m.get("size"), "modified_at": m.get("modified_at"),
         "family": (m.get("details") or {}).get("family"),
         "parameter_size": (m.get("details") or {}).get("parameter_size")}
        for m in rows if isinstance(m, dict)
    ]


def auth_headers(provider: str, api_key: str | None) -> dict[str, str]:
    """OpenAI 相容用 Bearer；Ollama 不需要。

    沒填金鑰就不要送空的 Bearer —— 本地 vLLM／LM Studio 多半不需要金鑰，
    送一個空的反而會被判成認證失敗。
    """
    h = {"Content-Type": "application/json"}
    if provider == "openai" and api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def extract_reply(data: dict[str, Any], provider: str) -> dict[str, Any]:
    """把回應正規化成 `{"role":..., "content":..., "tool_calls":[...]}`。

    兩家結構不同：Ollama 是 `message`，OpenAI 是 `choices[0].message`。
    結構不如預期時回空 dict，不要讓 IndexError 變成 500。
    """
    if provider == "openai":
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return {}
        return choices[0].get("message") or {}
    return data.get("message") or {}


def chat_body(cfg: Any, provider: str, *, messages: list[Any], tools: list[Any]) -> dict[str, Any]:
    """組請求主體。`options`（num_ctx 等）是 Ollama 專屬 —— 送給 OpenAI 端點會被拒絕。"""
    body: dict[str, Any] = {
        "model": cfg.chat_model,
        "messages": messages,
        "stream": False,
    }
    if tools:
        body["tools"] = tools
    if provider == "ollama":
        body["options"] = _chat_options(cfg)
    body.update(chat_thinking_off(cfg, provider, cfg.chat_model))
    return body


def chat_thinking_off(cfg: Any, provider: str, model: str) -> dict[str, Any]:
    """AI 對話設成不允許思考時要送的參數（允許時回空）。

    Ollama 用它自己的 `think: false`；OpenAI 相容服務與閘道（LiteLLM、vLLM、llama.cpp…）用
    reasoning_effort 等三個（見 _openai_reasoning_off）。官方 OpenAI 對不認得的欄位回 400，不送。
    這台伺服器先前拒絕過的不再送（_strip_rejected_controls 記下的）。
    """
    if getattr(cfg, "chat_thinking", True):
        return {}
    skip = _rejected_controls(chat_url(getattr(cfg, "url", ""), provider), model)
    if provider == "ollama":
        return {} if "think" in skip else {"think": False}
    if _is_official_openai(getattr(cfg, "url", "")):
        return {}
    return {k: v for k, v in _openai_reasoning_off().items() if k not in skip}


def _drop_rejected_thinking(body: dict[str, Any], status: int, text: str, url: str) -> bool:
    """伺服器拒絕了某個關閉思考的參數 → 拿掉並記住（回 True＝該重送）。

    舊版 Ollama 不認 `think`；OpenAI 相容服務與閘道的另外三個交給 _strip_rejected_controls。
    """
    if status in (400, 422) and "think" in body and "think" in (text or "").lower():
        body.pop("think", None)
        key = (url, str(body.get("model") or ""))
        _REJECTED_CONTROLS[key] = (time.monotonic(), _rejected_controls(*key) | {"think"})
        return True
    return _strip_rejected_controls(body, status, text, url)


class _ReadResponse:
    """已經讀完主體的失敗回應（_chat_stream 看完錯誤、判斷不必重送時交回給呼叫端）。"""

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self._text = text

    async def aread(self) -> bytes:
        return self._text.encode("utf-8")

    async def aiter_lines(self) -> AsyncIterator[str]:
        return
        yield  # pragma: no cover


@asynccontextmanager
async def _chat_stream(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float,
                       ) -> AsyncIterator[Any]:
    """開對話的串流；伺服器拒絕關閉思考的參數時拿掉被點名的再開（還沒吐出任何東西，可以重來）。"""
    for _ in range(len(_REASONING_OFF_KEYS) + 2):
        async with safe_stream("POST", url, headers=headers, json=body, timeout=timeout) as resp:
            if resp.status_code in (400, 422):
                text = (await resp.aread()).decode("utf-8", "replace")
                if _drop_rejected_thinking(body, resp.status_code, text, url):
                    continue
                yield _ReadResponse(resp.status_code, text)
                return
            yield resp
            return
    raise AIUpstream("LLM server kept rejecting the request")


async def _post_chat(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
    """非串流的對話請求；同樣會拿掉被拒絕的關閉思考參數再送。"""
    resp = await safe_request("POST", url, headers=headers, json=body, timeout=timeout)
    for _ in range(len(_REASONING_OFF_KEYS) + 1):
        if resp.status_code not in (400, 422) or not _drop_rejected_thinking(
                body, resp.status_code, getattr(resp, "text", ""), url):
            break
        resp = await safe_request("POST", url, headers=headers, json=body, timeout=timeout)
    return resp


def provider_label(provider: str) -> str:
    """錯誤訊息裡的供應商名稱。

    回「Ollama chat 401」但實際打的是 OpenAI 端點，只會把人送去查錯的地方。
    """
    return "OpenAI-compatible" if provider == "openai" else "Ollama"


# ─────────────────── 串流：兩種格式 ───────────────────
#
# Ollama 串流是逐行 JSON；OpenAI 相容是 SSE —— 每一行是 `data: {...}`，最後一行
# `data: [DONE]`，中間可能夾 `: keep-alive` 註解或 `event:` 行。以前三處串流都直接把
# 整行丟給 json.loads，SSE 那種每行都失敗、被靜靜跳過：巡檢拿到「空回應」（issue #36），
# 對話則解析不到工具呼叫，一路走到「強制作答」的後援 —— 有回答，但沒有查任何資料。

def _stream_payload(line: str) -> dict[str, Any] | None:
    """把串流的一行解成 dict；不是資料的行（空行、註解、SSE 欄位、[DONE]）回 None／哨兵。"""
    s = line.strip()
    if not s or s.startswith(":"):
        return None
    if s.startswith("data:"):
        s = s[5:].strip()
        if s == "[DONE]":
            return {"done": True}
    elif s.split(":", 1)[0] in ("event", "id", "retry"):
        return None
    try:
        data = json.loads(s)
    except ValueError:
        return None           # Ollama 偶爾夾非 JSON 行；跳過即可
    return data if isinstance(data, dict) else None


@dataclass
class _StreamDelta:
    content: str = ""
    thinking: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    done_reason: str | None = None
    error: str = ""
    #: 提示詞用掉幾個 token（Ollama 最後一片的 prompt_eval_count；OpenAI 相容服務有回 usage 時的 prompt_tokens）
    prompt_tokens: int | None = None


def _prompt_tokens(chunk: dict[str, Any]) -> int | None:
    v = chunk.get("prompt_eval_count")
    if v is None and isinstance(chunk.get("usage"), dict):
        v = chunk["usage"].get("prompt_tokens")
    return v if isinstance(v, int) else None


def _stream_delta(chunk: dict[str, Any]) -> _StreamDelta:
    """把一個串流片段正規化（Ollama: message；OpenAI: choices[0].delta）。"""
    err = chunk.get("error")
    error = ""
    if err:
        error = str(err.get("message") or err) if isinstance(err, dict) else str(err)
    choices = chunk.get("choices")
    if isinstance(choices, list):
        ch = choices[0] if choices and isinstance(choices[0], dict) else {}
        d = ch.get("delta") or ch.get("message") or {}
        finish = ch.get("finish_reason")
        return _StreamDelta(
            content=str(d.get("content") or ""),
            # 推論模型的思考內容：vLLM／DeepSeek 用 reasoning_content，部分服務用 reasoning
            thinking=str(d.get("reasoning_content") or d.get("reasoning") or d.get("thinking") or ""),
            tool_calls=list(d.get("tool_calls") or []),
            done=finish is not None, done_reason=finish, error=error, prompt_tokens=_prompt_tokens(chunk))
    m = chunk.get("message") or {}
    return _StreamDelta(
        content=str(m.get("content") or ""), thinking=str(m.get("thinking") or ""),
        tool_calls=list(m.get("tool_calls") or []),
        done=bool(chunk.get("done")), done_reason=chunk.get("done_reason"), error=error,
        prompt_tokens=_prompt_tokens(chunk))


def _with_call_ids(calls: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    """從文字還原的工具呼叫沒有 id。OpenAI 相容要 id（工具結果靠它對回去）與字串 arguments；
    Ollama 維持原樣（它要的 arguments 是物件）。"""
    if provider != "openai":
        return calls
    out = []
    for i, c in enumerate(calls):
        fn = dict(c.get("function") or {})
        args = fn.get("arguments")
        if not isinstance(args, str):
            fn["arguments"] = json.dumps(args or {}, ensure_ascii=False)
        out.append({"id": c.get("id") or f"call_inline_{i}", "type": "function", "function": fn})
    return out


class _ToolCallBuffer:
    """收集串流裡的工具呼叫。

    Ollama 一次給完整的呼叫；OpenAI 把一個呼叫拆成好幾段送，靠 `index` 對回同一個，
    arguments 是要接起來的字串片段。接好之後維持 OpenAI 的形狀（id／type／字串
    arguments）送回對話 —— 工具結果要用那個 id 對回去，不然嚴格的服務會拒絕。
    """

    def __init__(self) -> None:
        self._whole: list[dict[str, Any]] = []
        self._by_index: dict[int, dict[str, Any]] = {}

    def add(self, calls: list[dict[str, Any]]) -> None:
        for c in calls or []:
            if not isinstance(c, dict):
                continue
            idx = c.get("index")
            if idx is None:
                self._whole.append(c)
                continue
            cur = self._by_index.setdefault(int(idx), {"type": "function",
                                                       "function": {"name": "", "arguments": ""}})
            if c.get("id"):
                cur["id"] = c["id"]
            if c.get("type"):
                cur["type"] = c["type"]
            fn = c.get("function") or {}
            if fn.get("name"):
                cur["function"]["name"] = fn["name"]
            args = fn.get("arguments")
            if isinstance(args, str):
                cur["function"]["arguments"] += args
            elif isinstance(args, dict):
                cur["function"]["arguments"] = json.dumps(args, ensure_ascii=False)

    def calls(self) -> list[dict[str, Any]]:
        return self._whole + [self._by_index[i] for i in sorted(self._by_index)]


def json_chat_body(
    cfg: Any,
    provider: str,
    *,
    prompt: str,
    stream: bool,
    force_json: bool,
    max_output_tokens: int | None,
    num_ctx: int | None,
    no_thinking: bool,
    model: str | None = None,
) -> dict[str, Any]:
    """JSON 模式（AI 巡檢用）的請求主體。

    四個控制欄位在兩家的名字完全不同，而且送錯會被打回 400：
    `options` / `format` / `think` / `num_predict` 是 Ollama 專屬，OpenAI 相容端點
    對應的是 `response_format` 與 `max_tokens`（`think`、上下文長度則沒有對應，
    只能省略）。巡檢是背景批次，失敗只會留在紀錄裡沒有人在看畫面 —— 更不能靠運氣。
    """
    body: dict[str, Any] = {
        "model": model or cfg.chat_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
    }
    if provider == "openai":
        if force_json:
            body["response_format"] = {"type": "json_object"}
        if max_output_tokens:
            body["max_tokens"] = int(max_output_tokens)
        if no_thinking and not _is_official_openai(getattr(cfg, "url", "")):
            # 以前這條路完全忽略 no_thinking（issue #36）：推論模型把整個產出額度寫成思考，
            # 答案 0 字。官方 OpenAI 不送 —— 它對不認得的欄位直接回 400。
            # 這台伺服器先前拒絕過的欄位不再送（見 _strip_rejected_controls）
            skip = _rejected_controls(chat_url(getattr(cfg, "url", ""), provider), body["model"])
            body.update({k: v for k, v in _openai_reasoning_off().items() if k not in skip})
        return body
    options = _chat_options(cfg)
    if max_output_tokens:
        options = {**options, "num_predict": int(max_output_tokens)}
    if num_ctx:
        options = {**options, "num_ctx": int(num_ctx)}
    body["options"] = options
    if force_json:
        body["format"] = "json"
    if no_thinking:
        body["think"] = False
    return body




#: 自架 OpenAI 相容服務的「不要思考」。llama.cpp（b9882 原始碼確認）每個請求都認這兩個：
#: `chat_template_kwargs.enable_thinking` 交給聊天樣板（樣板有沒有理它看模型），
#: `thinking_budget_tokens` 是取樣層強制結束思考 —— 任何有思考標記的模型都有效。
#: vLLM 也認 chat_template_kwargs。被拒絕時會拿掉重送一次（見 _strip_rejected_controls）。
#: `reasoning_effort: "none"` 是 OpenAI 標準欄位，llama.cpp 自 b10434 起才真的照做（修正前會忽略，
#: 見 ggml-org/llama.cpp#27023）—— 回報者升級後單送它就解決了。三個一起送：新舊版本都蓋到。
_REASONING_OFF_KEYS = ("chat_template_kwargs", "thinking_budget_tokens", "reasoning_effort")


def _openai_reasoning_off() -> dict[str, Any]:
    return {"chat_template_kwargs": {"enable_thinking": False}, "thinking_budget_tokens": 0,
            "reasoning_effort": "none"}


def _is_official_openai(url: str) -> bool:
    """官方 OpenAI／Azure OpenAI：對不認得的請求欄位回 400，不能送自架服務用的欄位。"""
    from urllib.parse import urlparse
    host = (urlparse(url or "").hostname or "").lower()
    return host == "api.openai.com" or host.endswith(".openai.com") or host.endswith(
        ".openai.azure.com")


#: 這台伺服器、這個模型不收哪些「不要思考」的欄位：{(請求網址, 模型): (記下的時間, 欄位)}。
#: 記住才不必每個請求都先失敗一次（延遲加倍）；一小時後重試一次（伺服器可能升級了）。
_REJECTED_CONTROLS: dict[tuple[str, str], tuple[float, frozenset[str]]] = {}
_REJECT_TTL = 3600.0


def _rejected_controls(url: str, model: str) -> frozenset[str]:
    hit = _REJECTED_CONTROLS.get((url, model))
    return hit[1] if hit and time.monotonic() - hit[0] < _REJECT_TTL else frozenset()


def _strip_rejected_controls(body: dict[str, Any], status: int, text: str, url: str = "") -> bool:
    """伺服器因為不認得「不要思考」的欄位而拒絕時，把**被點名的那幾個**拿掉（回 True＝該重送）。

    只認 400／422 而且錯誤訊息點名了這些欄位 —— 其他 400（例如超出上下文）照常報錯，重送也不會好。
    以前一個被拒就三個一起拿掉：LiteLLM 這類閘道預設拒絕它不認得的欄位（例如 thinking_budget_tokens），
    連它認得、會轉成 Ollama `think:false` 的 reasoning_effort 也一起拿掉，重送的請求完全沒關思考
    （2026-09-30，jt-doc-tools 同一天踩到：翻譯從幾分鐘變成 400 分鐘）。
    拿掉的欄位依（網址, 模型）記住，之後的請求一開始就不送。
    """
    if status not in (400, 422):
        return False
    low = (text or "").lower()
    present = [k for k in _REASONING_OFF_KEYS if k in body]
    named = [k for k in present if k in low]
    if "enable_thinking" in low and "chat_template_kwargs" in present and "chat_template_kwargs" not in named:
        named.append("chat_template_kwargs")
    if not present or not (named or any(k in low for k in _REASONING_OFF_KEYS)):
        return False
    drop = named or present
    for k in drop:
        body.pop(k, None)
    if url:
        key = (url, str(body.get("model") or ""))
        _REJECTED_CONTROLS[key] = (time.monotonic(), _rejected_controls(*key) | set(drop))
    return True


def _cut_off_while_thinking(thinking_chars: int, max_tokens: Any) -> AIError:
    """模型把產出額度全用在思考上（finish_reason=length、答案 0 字）—— 講清楚是怎麼回事。

    以前報成「（空回應）」，看起來像模型壞了或我們沒收到東西；其實是額度被思考吃光。
    """
    limit = f"（{int(max_tokens)} tokens）" if max_tokens else ""
    return AIError(
        f"模型把產出額度{limit}全用在思考上：思考 {thinking_chars} 字、答案 0 字"
        "（finish_reason=length）。請在推論伺服器關閉思考模式（例如 llama.cpp 啟動時加 "
        "--reasoning-budget 0），或改用不帶思考的模型。"
    )


#: 自動輸出上限（output_budget）的上下限與保留。
#: 防呆上限：上下文再大也不讓單次回覆超過這個數 —— 模型偶爾卡在重複輸出的迴圈（實測寫到 54,000 字還沒停），
#: 沒有上限會把整個逾時燒完
OUTPUT_LOOP_GUARD = 32768
#: 至少留這麼多：提示詞已經把上下文塞滿時照樣送（那是另一個問題，有別的訊息說明）
OUTPUT_FLOOR = 1024
#: 估計誤差與聊天樣板的保留
_OUTPUT_MARGIN = 256
#: Ollama 沒設 num_ctx 時用的預設上下文
_OLLAMA_DEFAULT_CTX = 4096
#: OpenAI 相容端點、管理員也沒填上下文長度：不知道模型能放多少，給一個多數模型都接受的值
#: （送超過模型上限的 max_tokens 有些端點會直接回 400）
_UNKNOWN_CTX_OUTPUT = 8192


def estimate_tokens(text: str) -> int:
    """粗估 token 數，寧可高估：英數約 4 字元 1 token，中日韓大致 1 字 1 token。
    （低估的代價是提示詞被截掉、模型完全不照指示做；AI 巡檢、輸出上限共用這一份。）"""
    cjk = sum(1 for ch in text if ord(ch) > 0x2E7F)
    return cjk + (len(text) - cjk) // 4 + 1


def output_budget(prompt: str, num_ctx: int | None, provider: str) -> int:
    """這次呼叫可以產出多少 token：上下文長度 － 提示詞 － 保留，夾在 [OUTPUT_FLOOR, OUTPUT_LOOP_GUARD]。

    2026-10-21 使用者：輸出上限要跟著 LLM 設定自動變，不要寫死（各家的主機與模型都不同）。
    以前各功能各寫一個數字（800、900、1400、2000、6000），換了大上下文的模型也用不到。"""
    if not num_ctx:
        if provider == "openai":
            return _UNKNOWN_CTX_OUTPUT
        num_ctx = _OLLAMA_DEFAULT_CTX
    room = int(num_ctx) - estimate_tokens(prompt) - _OUTPUT_MARGIN
    return max(OUTPUT_FLOOR, min(OUTPUT_LOOP_GUARD, room))


class ChatText(str):
    """模型的回覆文字，另外帶「是不是被輸出上限切斷」（finish_reason／done_reason＝length）。

    2026-10-21：IP 變更評估的逐項解釋寫到一半被我們自己設的輸出上限切斷，JSON 不完整，
    只被當成「格式不對」；文字類的判讀被切斷時畫面上也只是話講到一半。標出來，呼叫端才能
    要求模型精簡重寫，或在文字後面註明被截斷。"""

    truncated: bool

    def __new__(cls, value: str = "", truncated: bool = False) -> ChatText:
        obj = super().__new__(cls, value)
        obj.truncated = bool(truncated)
        return obj


_TRUNCATED_NOTE = {
    "zh": "（回答達到輸出長度上限，後面被截斷）",
    "en": "(The answer reached the output length limit and was cut off here.)",
    "ja": "（回答が出力の長さの上限に達したため、ここで途切れています）",
}


async def finish_text(session: AsyncSession, user: Any, text: str) -> str:
    """文字類判讀的收尾：去掉前後空白；被輸出上限切斷就在最後註明（依這個人的介面語言）。"""
    if not getattr(text, "truncated", False):
        return str(text or "").strip()
    return with_truncation_note(text, await user_locale(session, user)).strip()


def with_truncation_note(text: str, locale: str | None) -> str:
    """被輸出上限切斷的文字類回覆，在最後註明（不讓半句話看起來像寫完了）。"""
    if not getattr(text, "truncated", False):
        return str(text)
    key = "en" if (locale or "").startswith("en") else "ja" if (locale or "").startswith("ja") else "zh"
    return f"{str(text).rstrip()}\n\n{_TRUNCATED_NOTE[key]}"


def _answer_or_explain(data: dict[str, Any], *, max_tokens: Any) -> ChatText:
    """非串流回覆取出答案；答案是空的而且是被額度切在思考裡，就丟出說明清楚的錯誤。"""
    msg = data.get("message") or {}
    finish = data.get("done_reason")
    if not msg:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        finish = choice.get("finish_reason")
    content = str(msg.get("content") or "")
    if not content.strip() and finish == "length":
        thinking = str(msg.get("reasoning_content") or msg.get("reasoning")
                       or msg.get("thinking") or "")
        raise _cut_off_while_thinking(len(thinking), max_tokens)
    return ChatText(content, truncated=finish == "length")


def embedding_body(cfg: Any, provider: str, text_in: str) -> dict[str, Any]:
    """Ollama 收 `prompt`，OpenAI 相容收 `input`。"""
    if provider == "openai":
        return {"model": cfg.embedding_model, "input": text_in}
    return {"model": cfg.embedding_model, "prompt": text_in}


def extract_embedding(data: dict[str, Any], provider: str) -> list[float]:
    """Ollama 回 `embedding`，OpenAI 相容回 `data[0].embedding`。

    取不到就回空清單，讓呼叫端統一報「沒有拿到向量」，而不是 KeyError／IndexError。
    """
    vec: Any
    if provider == "openai":
        rows = data.get("data")
        vec = rows[0].get("embedding") if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    else:
        vec = data.get("embedding")
    return vec if isinstance(vec, list) else []



async def embed(session: AsyncSession, text_in: str) -> list[float]:
    """呼叫 Ollama 的 embedding endpoint。設定取自 system_settings (DB)，fallback 到 env。"""
    from app.services.system_config import get_llm_config
    cfg = await get_llm_config(session)
    if not cfg.enabled:
        raise AINotConfigured("LLM is disabled")
    # 嵌入模型可以在另一台（issue #33）；留空就沿用對話模型那個位址
    url = embedding_url(getattr(cfg, "embedding_base_url", None) or cfg.url, cfg.provider)
    body = embedding_body(cfg, cfg.provider, text_in)
    try:
        resp = await safe_request(
            "POST", url,
            headers=auth_headers(cfg.provider, cfg.api_key),
            json=body, timeout=cfg.timeout,
        )
    except UnsafeOutboundURL as exc:
        raise AIBlocked(f"SSRF guard: {exc}") from exc
    except httpx.HTTPError as exc:
        raise AIUnreachable(f"transport: {transport_detail(exc)}") from exc
    if resp.status_code != 200:
        raise AIUpstream(f"{provider_label(cfg.provider)} {resp.status_code}: {resp.text[:200]}")
    vec = extract_embedding(resp.json(), cfg.provider)
    if not vec:
        raise AIUpstream(f"{provider_label(cfg.provider)} returned no embedding")
    expected_dim = get_settings().embedding_dim
    if len(vec) != expected_dim:
        raise EmbeddingDimMismatch(len(vec), expected_dim)
    return [float(x) for x in vec]


def _vector_literal(vec: list[float]) -> str:
    """pgvector 的字串字面值：'[0.1,0.2,...]'。"""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


# ─────────────────── 寫入：對單一物件描述產生向量 ───────────────────


async def index_subnet(session: AsyncSession, subnet_id: str, description: str | None,
                       *, raise_on_error: bool = False) -> bool:
    """為一個 subnet 產 embedding 並寫入 vector 欄位；description 為空則清空。"""
    if not description:
        await session.execute(
            text("UPDATE subnets SET description_embedding = NULL WHERE id = :id"),
            {"id": subnet_id},
        )
        return True
    try:
        vec = await embed(session, description)
    except (AIError, AINotConfigured):
        # 單筆寫入路徑（存檔時順手索引）不因為 LLM 不通就讓存檔失敗；
        # 批次 reindex 則要知道原因，否則「全失敗」會被當成「沒事可做」。
        # 舊向量清掉：它是上一段說明的向量，留著會讓搜尋配錯、索引狀態也看起來是好的（2026-10-13）
        await session.execute(text("UPDATE subnets SET description_embedding = NULL WHERE id = :id"), {"id": subnet_id})
        if raise_on_error:
            raise
        return False
    await session.execute(
        text("UPDATE subnets SET description_embedding = (:v)::vector WHERE id = :id"),
        {"v": _vector_literal(vec), "id": subnet_id},
    )
    return True


async def index_ip(session: AsyncSession, ip_id: str, description: str | None,
                   *, raise_on_error: bool = False) -> bool:
    if not description:
        await session.execute(
            text("UPDATE ip_addresses SET description_embedding = NULL WHERE id = :id"),
            {"id": ip_id},
        )
        return True
    try:
        vec = await embed(session, description)
    except (AIError, AINotConfigured):
        # 單筆寫入路徑（存檔時順手索引）不因為 LLM 不通就讓存檔失敗；
        # 批次 reindex 則要知道原因，否則「全失敗」會被當成「沒事可做」。
        # 舊向量清掉：它是上一段說明的向量，留著會讓搜尋配錯、索引狀態也看起來是好的（2026-10-13）
        await session.execute(text("UPDATE ip_addresses SET description_embedding = NULL WHERE id = :id"), {"id": ip_id})
        if raise_on_error:
            raise
        return False
    await session.execute(
        text("UPDATE ip_addresses SET description_embedding = (:v)::vector WHERE id = :id"),
        {"v": _vector_literal(vec), "id": ip_id},
    )
    return True


async def index_device(session: AsyncSession, device_id: str, description: str | None,
                       *, raise_on_error: bool = False) -> bool:
    if not description:
        await session.execute(
            text("UPDATE devices SET description_embedding = NULL WHERE id = :id"),
            {"id": device_id},
        )
        return True
    try:
        vec = await embed(session, description)
    except (AIError, AINotConfigured):
        # 單筆寫入路徑（存檔時順手索引）不因為 LLM 不通就讓存檔失敗；
        # 批次 reindex 則要知道原因，否則「全失敗」會被當成「沒事可做」。
        # 舊向量清掉：它是上一段說明的向量，留著會讓搜尋配錯、索引狀態也看起來是好的（2026-10-13）
        await session.execute(text("UPDATE devices SET description_embedding = NULL WHERE id = :id"), {"id": device_id})
        if raise_on_error:
            raise
        return False
    await session.execute(
        text("UPDATE devices SET description_embedding = (:v)::vector WHERE id = :id"),
        {"v": _vector_literal(vec), "id": device_id},
    )
    return True


# ─────────────────── 查詢：跨表 cosine 最相近 ───────────────────


async def probe_embedding(session: AsyncSession) -> dict[str, Any]:
    """實際取一次向量，回報維度是否與資料庫欄位相符。

    設定頁上，嵌入模型選錯的唯一症狀是「語意搜尋永遠沒有結果」—— 沒有任何訊息會說
    是維度不合。這支就是把那件事講出來：模型回幾維、資料庫要幾維、通不通。
    """
    expected = get_settings().embedding_dim
    try:
        vec = await embed(session, "jt-ipam embedding dimension probe")
    except EmbeddingDimMismatch as exc:
        # 有拿到向量、只是維度不對：講出模型回幾維（畫面那句「模型回 N 維，資料庫要 M 維」）
        return {"ok": False, "dim": exc.got, "expected": expected, "error": None}
    except (AIError, AINotConfigured) as exc:
        return {"ok": False, "dim": None, "expected": expected, "error": str(exc)[:300]}
    return {"ok": len(vec) == expected, "dim": len(vec), "expected": expected, "error": None}


async def semantic_search(
    session: AsyncSession,
    *,
    query: str,
    limit: int = 20,
    user: Any,
) -> dict[str, Any]:
    """跨 subnets / ip_addresses / devices 的語意搜尋（cosine 距離，越小越像）。

    可見範圍推進 SQL（先過濾再 LIMIT，否則看得到的會被看不到的擠掉）；歸檔的子網路與其中的 IP 不列。
    以前完全沒帶使用者：任何登入帳號都拿得到全站資料（2026-10-07 RBAC 稽核）。
    """
    from app.services.permission import visible_ids
    vis = {t: await visible_ids(session, user=user, object_type=t) for t in ("subnet", "ip", "device")}  # type: ignore[arg-type]
    vec = await embed(session, query)
    vlit = _vector_literal(vec)

    async def rows(t: str, sql: str) -> list[Any]:
        ids = vis[t]
        if ids is not None and not ids:
            return []
        params: dict[str, Any] = {"v": vlit, "limit": limit}
        scope = ""
        if ids is not None:
            scope = " AND x.id = ANY(CAST(:ids AS uuid[]))"
            params["ids"] = [str(i) for i in ids]
        return list((await session.execute(text(sql.replace("{scope}", scope)), params)).all())

    sub_rows = await rows("subnet", """
        SELECT x.id::text AS id, x.cidr::text AS label, x.description,
               (x.description_embedding <=> (:v)::vector) AS distance
          FROM subnets x
         WHERE x.description_embedding IS NOT NULL AND x.archived_at IS NULL{scope}
         ORDER BY x.description_embedding <=> (:v)::vector
         LIMIT :limit
    """)
    ip_rows = await rows("ip", """
        SELECT x.id::text AS id, host(x.ip)::text AS label, x.hostname, x.description,
               (x.description_embedding <=> (:v)::vector) AS distance
          FROM ip_addresses x JOIN subnets s ON s.id = x.subnet_id
         WHERE x.description_embedding IS NOT NULL AND s.archived_at IS NULL{scope}
         ORDER BY x.description_embedding <=> (:v)::vector
         LIMIT :limit
    """)
    dev_rows = await rows("device", """
        SELECT x.id::text AS id, x.name AS label, x.description,
               (x.description_embedding <=> (:v)::vector) AS distance
          FROM devices x
         WHERE x.description_embedding IS NOT NULL{scope}
         ORDER BY x.description_embedding <=> (:v)::vector
         LIMIT :limit
    """)

    return {
        "query": query,
        "subnets": [
            {"id": r.id, "label": r.label, "description": r.description,
             "score": round(1 - float(r.distance), 4)}
            for r in sub_rows
        ],
        "ip_addresses": [
            {"id": r.id, "label": r.label, "hostname": r.hostname,
             "description": r.description, "score": round(1 - float(r.distance), 4)}
            for r in ip_rows
        ],
        "devices": [
            {"id": r.id, "label": r.label, "description": r.description,
             "score": round(1 - float(r.distance), 4)}
            for r in dev_rows
        ],
    }


# ─────────────────── Chat：自然語言 + tool use（Phase 4）───────────────────


_LANG_MAP = {
    "zh-TW": "Traditional Chinese (繁體中文，使用台灣用語)",
    "zh-CN": "Simplified Chinese (简体中文)",
    "en-US": "English",
    "en": "English",
    "ja-JP": "Japanese（日本語。です・ます調）",
    "ja": "Japanese (日本語)",
}


def _lang_instruction(locale: str | None) -> str:
    """根據使用者 UI locale 給出要求 LLM 用何語言回應的指令。"""
    name = _LANG_MAP.get(locale or "", None)
    if not name:
        # 未知 locale → 不強制；讓 LLM 跟著使用者輸入的語言
        return "Respond in the same language as the user's most recent message."
    return f"Always respond to the user in {name}, regardless of the language of tool outputs."


async def user_locale(session: AsyncSession, user: Any) -> str | None:
    """那個人的介面語言 —— 所有會把文字丟給 LLM 的功能都該用這一支。

    為什麼集中在這裡：語言判斷曾經散在各個進入點，做法還各自不同 —— 調查視窗是
    `lang.startswith("zh")` 的二分法（日文使用者會拿到英文），鑑識卡與規則異動解讀
    則是提示詞寫死中文（**英文使用者也拿到中文**）。加日文時這三處全都沒有跟上，
    而且不會報錯，只是答案語言不對。一個來源就不會各走各的。
    """
    from app.models.user import UserPreference

    uid = getattr(user, "id", None)
    if uid is None:
        return None
    return (await session.execute(
        select(UserPreference.locale).where(UserPreference.user_id == uid)
    )).scalar_one_or_none()


async def answer_language(session: AsyncSession, user: Any) -> str:
    """給 raw_chat 用的提示詞片段：要模型用這個人的介面語言回答。

    提示詞本體維持中文（那是我們寫給模型看的指示），**回答的語言**由這一行決定。
    模型對「用 X 語言回答」的遵從度比把整段提示詞翻成 X 高得多，也不必維護五份提示詞。
    """
    name = _LANG_MAP.get(await user_locale(session, user) or "", None) or _LANG_MAP["zh-TW"]
    return (
        f"\n\n**回答一律使用 {name}**，不論上面的證據與欄位是什麼語言；"
        "專有名詞（主機名稱、欄位名、指令）保持原文。\n"
    )


# 模型偶爾會把工具呼叫當成「文字」吐出來（而非結構化 tool_calls）——即使是支援工具呼叫的
# 模型也會偶發如此。常見痕跡：<tool_call>…</tool_call> 標記 / call:name(args) / JSON {"name":…,"arguments":…}
_TOOL_LEAK_RE = re.compile(
    r"</?tool_?call>"
    r"|\bcall\s*:\s*[A-Za-z_]\w*\s*\("
    r'|\{[^{}]*"name"\s*:\s*"[^"]+"[^{}]*"(?:arguments|parameters)"',
    re.I | re.S,
)


def _norm_tool(n: str) -> str:
    return re.sub(r"[^a-z0-9]", "", n.lower())


def _looks_like_tool_leak(content: str) -> bool:
    """內容看起來是「被當成文字吐出來的工具呼叫」而非正常答案。"""
    return bool(content and _TOOL_LEAK_RE.search(content))


def _parse_inline_args(raw: str) -> dict[str, Any]:
    """解析 gemma 式參數字串 `arg:</~/>value</~/>, n:3`（值內可能含冒號，如 MAC）。"""
    raw = raw.replace("</~/>", '"').replace("<~>", '"').strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            pass
    args: dict[str, Any] = {}
    for part in re.split(r",(?![^{\[]*[}\]])", raw):
        part = part.strip()
        if not part:
            continue
        seps = [i for i in (part.find(":"), part.find("=")) if i >= 0]
        if not seps:
            continue
        cut = min(seps)
        key = part[:cut].strip().strip("\"'")
        val = part[cut + 1:].strip().strip("\"'")
        if not key:
            continue
        if re.fullmatch(r"-?\d+", val):
            args[key] = int(val)
        elif val.lower() in ("true", "false"):
            args[key] = val.lower() == "true"
        else:
            args[key] = val
    return args


def _inline_tool_calls(content: str, allowed: set[str] | None) -> list[dict[str, Any]]:
    """後援：模型偶發把工具呼叫寫成文字時，盡量還原成 Ollama 結構化 tool_calls。
    僅在偵測到 tool-call 痕跡、且名稱對得上已知（且該使用者可用）的工具時才接受，
    避免把一般文句誤判成呼叫。"""
    from app.mcp.tools import TOOLS
    if not _looks_like_tool_leak(content):
        return []
    known = {_norm_tool(n): n for n in TOOLS if allowed is None or n in allowed}
    found: list[tuple[str, dict[str, Any]]] = []
    # (a) JSON 物件 {"name": "...", "arguments"/"parameters": {...}}
    for m in re.finditer(
        r'\{[^{}]*?"name"\s*:\s*"([^"]+)"'
        r'(?:[^{}]*?"(?:arguments|parameters)"\s*:\s*(\{[^{}]*\}))?[^{}]*\}',
        content, re.S,
    ):
        real = known.get(_norm_tool(m.group(1)))
        if not real:
            continue
        args: dict[str, Any] = {}
        if m.group(2):
            try:
                args = json.loads(m.group(2))
            except (ValueError, TypeError):
                args = {}
        found.append((real, args))
    # (b) 函式式 `[call:]name(args)`（含 gemma 的 <toolcall> 標記）
    if not found:
        for m in re.finditer(r"(?:call\s*:\s*)?([A-Za-z_]\w*)\s*\(([^()]*)\)", content):
            real = known.get(_norm_tool(m.group(1)))
            if not real:
                continue
            found.append((real, _parse_inline_args(m.group(2))))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, args in found:
        key = name + "|" + json.dumps(args, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append({"function": {"name": name, "arguments": args}})
        if len(out) >= 4:
            break
    return out


def _tool_leak_message(locale: str | None) -> str:
    """偵測到工具呼叫外洩、但無法還原執行時，給使用者的友善說明（取代原始亂碼）。

    注意：模型本身支援工具呼叫，只是這次偶發地把呼叫寫成了無法解析的文字 → 請使用者重試即可，
    不要誤導成「模型不支援」或叫他換模型。
    """
    if (locale or "").lower().startswith("en"):
        return (
            "This reply contained a tool call I couldn't parse, so no data was fetched. "
            "Please try again or rephrase your question."
        )
    return (
        "這次的回應裡有一段工具呼叫無法解析，因此沒有取得資料。請再試一次，或換個說法重新詢問。"
    )


async def chat(
    session: AsyncSession,
    *,
    user: Any,
    messages: list[dict[str, Any]],
    locale: str | None = None,
    max_iterations: int = 4,
    page_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """以 Ollama chat 模型 + jt-ipam tools 處理自然語言。

    流程：
      1. 把 IPAM tools 註冊表轉成 Ollama tools schema
      2. 用 system prompt 框定身份（jt-ipam 助手）
      3. 呼叫 Ollama；若回 tool_calls，執行對應 jt-ipam 工具
      4. 把 tool 結果 append 回 messages，再呼叫一次（最多 max_iterations 輪）

    OWASP A04 / A06：
      - chat 對外 URL 走 safe_request
      - tool 執行時的 user 與 session 都從本端拿，不從 LLM 輸入信任
    """
    from app.services.system_config import get_llm_config
    cfg = await get_llm_config(session)
    if not cfg.enabled:
        raise AINotConfigured("LLM is disabled")

    from app.mcp.tools import allowed_tool_names
    _allowed = await allowed_tool_names(session, user)
    ollama_tools, convo = _build_chat_context(messages, locale, page_context, _allowed)
    url = chat_url(cfg.url, cfg.provider)
    started = time.monotonic()

    def _meta() -> dict[str, Any]:
        return {"model": cfg.chat_model, "elapsed_ms": int((time.monotonic() - started) * 1000)}

    measured: tuple[int, int] | None = None
    trimmed = 0
    for _ in range(max_iterations):
        trimmed += _fit_context(convo, ollama_tools, num_ctx=cfg.num_ctx, provider=cfg.provider, measured=measured)
        sent_len = len(convo)
        body = {
            "model": cfg.chat_model,
            "messages": convo,
            "tools": ollama_tools,
            "stream": False,
            # 低溫度：減少模型亂插字（如把 192.168 寫成「19 kiếm 168」之類的跨語言錯字）
            "options": _chat_options(cfg),
            **chat_thinking_off(cfg, cfg.provider, cfg.chat_model),
        }
        try:
            resp = await _post_chat(url, body, auth_headers(cfg.provider, cfg.api_key), cfg.timeout)
        except UnsafeOutboundURL as exc:
            raise AIBlocked(f"SSRF guard: {exc}") from exc
        except httpx.HTTPError as exc:
            raise AIUnreachable(f"transport: {transport_detail(exc)}") from exc
        if resp.status_code != 200:
            raise AIUpstream(f"{provider_label(cfg.provider)} chat {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if _prompt_tokens(data):
            measured = (int(_prompt_tokens(data) or 0), sent_len)

        msg = extract_reply(data, cfg.provider)
        convo.append(msg)

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            # 後援：模型偶發把工具呼叫寫成文字（而非結構化 tool_calls）→ 還原執行
            inline = _with_call_ids(_inline_tool_calls(msg.get("content") or "", _allowed), cfg.provider)
            if inline:
                msg["tool_calls"] = inline
                msg["content"] = ""
                tool_calls = inline
            elif _looks_like_tool_leak(msg.get("content") or ""):
                return {"answer": _tool_leak_message(locale), "messages": convo, **_meta()}
            else:
                content = msg.get("content") or ""
                if not content.strip():
                    content = (await _force_final_answer(cfg, convo)
                               or _empty_answer_message(locale, data.get("done_reason"),
                                                        _prompt_tokens(data), cfg.num_ctx))
                return {"answer": _with_trimmed_note(_annotate_unverified(content, convo, locale), trimmed,
                                                     cfg.num_ctx, cfg.provider, locale),
                        "messages": convo, **_meta()}

        # 異動類工具不直接執行 → 回傳待確認動作，等使用者按「確認」
        pending = _pending_mutations(tool_calls)
        if pending:
            return {"answer": msg.get("content") or _pending_prompt_text(pending),
                    "messages": convo, "pending_actions": pending, **_meta()}

        convo.extend(await _run_tool_calls(session, user, tool_calls))

    # 用完 max_iterations 還在叫工具 → 最後不給工具再叫一次，逼它用現有資訊作答
    answer = await _force_final_answer(cfg, convo) or _steps_exceeded_message(locale)
    return {"answer": _annotate_unverified(answer, convo, locale),
            "messages": convo, **_meta()}


# 看起來像位址的點分四段（含抄錯的 999）。抄錯的那種不是合法 IPv4，但照樣會被當事實讀。
_DOTTED_QUAD = re.compile(r"\b\d{1,4}(?:\.\d{1,4}){3}\b")


def _is_netmask(text: str) -> bool:
    """255.255.255.0 這種是常識不是查來的資料；0.0.0.0 同理。標它們只會製造雜訊。"""
    try:
        parts = [int(x) for x in text.split(".")]
    except ValueError:
        return False
    if any(p > 255 for p in parts):
        return False
    bits = "".join(f"{p:08b}" for p in parts)
    # 連續的 1 之後全是 0（含全 0 與全 1）
    return bits == "0" * 32 or bits.rstrip("0").count("0") == 0


def unverified_ips(answer: str, sources: Iterable[str]) -> list[str]:
    """答案裡出現、但任何來源（工具結果／使用者的話）都沒有的位址。

    模型只負責敘述，事實必須來自查詢。提示詞已經寫了「逐字照抄」，量化過的小模型還是會
    把位址抄錯、甚至整個編出來（GitHub issue #34），而那種答案看起來跟正確答案一模一樣。
    """
    seen: set[str] = set()
    for src in sources:
        seen.update(_DOTTED_QUAD.findall(src or ""))
    out: list[str] = []
    for token in _DOTTED_QUAD.findall(answer or ""):
        if token in seen or token in out or _is_netmask(token):
            continue
        out.append(token)
    return out


_UNVERIFIED_WARN = {
    "zh": "⚠️ 下列位址不在這次的查詢結果裡，可能是模型自行產生或抄錯的，請不要直接採信：{ips}",
    "ja": "⚠️ 次のアドレスは今回の照会結果に含まれていません。モデルが生成または書き間違えた可能性があります：{ips}",
    "en": ("⚠️ These addresses do not appear in any tool result from this turn and may have "
           "been invented or mistyped by the model — do not rely on them: {ips}"),
}


def _annotate_unverified(answer: str, convo: list[dict[str, Any]], locale: str | None) -> str:
    """在答案後面加一行警告，**不改寫模型的字**。

    改寫會讓人以為模型答對了；標出來才看得出哪幾個不能用。
    """
    if not answer:
        return answer
    sources = [str(m.get("content") or "") for m in convo
               if m.get("role") in ("tool", "user")]
    bad = unverified_ips(answer, sources)
    if not bad:
        return answer
    loc = (locale or "zh-TW").lower()
    key = "en" if loc.startswith("en") else ("ja" if loc.startswith("ja") else "zh")
    return answer + "\n\n" + _UNVERIFIED_WARN[key].format(ips="、".join(bad)
                                                           if key != "en" else ", ".join(bad))


#: 提示詞用掉 num_ctx 的這個比例以上就當成「塞滿了」：剩下的空間連一句回答都放不下
_CONTEXT_FULL_RATIO = 0.95


def _empty_answer_message(locale: str | None, done_reason: str | None, prompt_tokens: int | None = None,
                          num_ctx: int | None = None) -> str:
    """模型什麼都沒產出時，說清楚可能的原因與下一步 —— 空白訊息無法行動。

    提示詞把 num_ctx 塞滿是最常見、也最不會報錯的原因（Ollama 默默截掉前面的內容）：2026-10-15
    工具說明變長後，正式站台的提示詞吃掉 16,231／16,384 token，gemma 開始空白回答。量得到就講出數字。"""
    locale = locale or "zh-TW"
    if prompt_tokens and num_ctx and prompt_tokens >= num_ctx * _CONTEXT_FULL_RATIO:
        if locale.startswith("en"):
            return (f"No answer was produced: the prompt (tool list and conversation) already uses "
                    f"{prompt_tokens} of {num_ctx} tokens (num_ctx), leaving the model no room to answer. "
                    "Raise num_ctx under Admin → LLM / AI, or start a new conversation.")
        if locale.startswith("ja"):
            return (f"回答が得られませんでした：プロンプト（ツール一覧と会話）だけで {prompt_tokens} / {num_ctx} "
                    "トークン（num_ctx）を使っており、モデルが回答する余地がありません。"
                    "「管理 → LLM / AI」で num_ctx を上げるか、新しい会話で試してください。")
        return (f"這次沒有得到回答：提示詞（工具清單與對話）已經用掉 {prompt_tokens} / {num_ctx} 個 token"
                "（num_ctx），模型沒有空間回答。請到「管理 → LLM / AI」調高 num_ctx，或開新對話再試。")
    why_zh = {
        "length": "回答被輸出長度上限截斷",
        "load": "模型正在載入",
    }.get(done_reason or "", "模型沒有產生任何文字（可能只輸出了思考內容，或超出上下文長度）")
    why_en = {
        "length": "the answer hit the output length limit",
        "load": "the model was still loading",
    }.get(done_reason or "", "the model produced no text (it may have emitted only "
                             "thinking, or exceeded the context length)")
    if locale.startswith("en"):
        return (f"No answer was produced — {why_en}. Try a narrower question, or raise "
                "num_ctx / the output limit under Admin → LLM / AI.")
    if locale.startswith("ja"):
        why_ja = {"length": "回答が出力上限で切れました", "load": "モデルを読み込み中でした"}.get(
            done_reason or "", "モデルが文字を出力しませんでした（思考だけを出力したか、コンテキスト長を超えた可能性）")
        return (f"回答が得られませんでした：{why_ja}。質問を小さくするか、"
                "「管理 → LLM / AI」で num_ctx と出力上限を上げてください。")
    return (f"這次沒有得到回答 —— {why_zh}。可以把問題問得更小一點，"
            "或到「管理 → LLM / AI」調高 num_ctx 與輸出上限。")


def _steps_exceeded_message(locale: str | None) -> str:
    """工具呼叫輪數用完、最後強制作答也沒有內容。只有真的叫過工具才用這段話。"""
    locale = locale or "zh-TW"
    if locale.startswith("en"):
        return "(Too many lookup steps without an answer: please ask a smaller question.)"
    if locale.startswith("ja"):
        return "（照会の手順が多すぎて回答できませんでした。質問を小さくして再度お試しください）"
    return "（查詢步驟過多仍未完成，請把問題拆小一點再試一次）"


async def _force_final_answer(cfg: Any, convo: list[dict[str, Any]]) -> str:
    """max_iterations 用完時的收尾：不帶 tools 再呼叫一次，要 LLM 直接作答。"""
    url = chat_url(cfg.url, cfg.provider)
    # 明確指示：根據已取得的工具結果立刻作答，別再呼叫工具（否則模型常回空字串 → 落到 fallback）
    nudge = {
        "role": "user",
        "content": (
            "Based on the tool results already gathered above, give your best final "
            "answer to my question now, in my language. Do NOT call any more tools. "
            "If the data is incomplete, answer with what you have and say what is missing."
        ),
    }
    body = {"model": cfg.chat_model, "messages": [*convo, nudge],
            "stream": False, "options": _chat_options(cfg),
            **chat_thinking_off(cfg, cfg.provider, cfg.chat_model)}
    try:
        resp = await _post_chat(url, body, auth_headers(cfg.provider, cfg.api_key), cfg.timeout)
        if resp.status_code == 200:
            msg = extract_reply(resp.json(), cfg.provider)
            content = msg.get("content")
            if content:
                convo.append({"role": "assistant", "content": content})
                return content  # type: ignore[no-any-return]
    except (UnsafeOutboundURL, httpx.HTTPError):
        pass
    # 空字串：由呼叫端決定要怎麼說明（以前這裡回「查詢步驟過多」，空白回答也被說成步驟過多）
    return ""


def _page_context_line(context: dict[str, Any] | None) -> str:
    """把前端帶來的「目前所在頁面」資訊轉成 system prompt 提示。"""
    if not context:
        return ""
    parts: list[str] = []
    cidr = context.get("subnet_cidr")
    sid = context.get("subnet_id")
    if sid or cidr:
        ref = cidr or sid
        parts.append(
            f" The user is currently viewing subnet {ref}"
            + (f" (subnet_id={sid})" if sid else "")
            + ". If they ask for free IPs, usage, or allocation without naming a "
            "subnet, default to THIS subnet."
        )
    if context.get("device_id"):
        parts.append(f" They are viewing device_id={context['device_id']}.")
    if context.get("section_id"):
        parts.append(f" They are viewing section_id={context['section_id']}.")
    return "".join(parts)


# ── 工具結果撐爆上下文時，使用者的問題不能被丟掉（2026-11-07 正式站台）──
# num_ctx=16384 時工具清單＋系統提示詞就用掉約 12,700 token；查一筆資料多的 IP 再加一輪就超過上限。
# Ollama 超過時從最舊的訊息丟起（系統提示詞與最後一則保留），被丟掉的正好是使用者的問題：
# 使用者問「這個 IP 誰在用」，模型只看得到上一個工具的結果，就回答整個網段的使用率。
#: 回答至少留這麼多 token（另外不少於上下文的 1/16）
_ANSWER_RESERVE_MIN = 1024
#: 縮短工具結果時至少留這麼多字（開頭通常是最重要的欄位：位址、名稱、狀態）
_TRIM_FLOOR = 800
_TRIM_NOTE = " …[truncated to fit the model's context length]"
#: 工具結果是 JSON（鍵名、數字、引號）：實測 gemma4 約 2.5 字元一個 token，一般文字的估算（4 字元）會少算六成
_DATA_CHARS_PER_TOKEN = 2.4


def _estimate_data_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if ord(ch) > 0x2E7F)
    return cjk + int((len(text) - cjk) / _DATA_CHARS_PER_TOKEN) + 1


def _context_limit(num_ctx: int | None, provider: str) -> int | None:
    if num_ctx:
        return int(num_ctx)
    return _OLLAMA_DEFAULT_CTX if provider == "ollama" else None


def _fit_context(convo: list[dict[str, Any]], tools: list[Any], *, num_ctx: int | None, provider: str,
                 measured: tuple[int, int] | None = None) -> int:
    """送出前估算提示詞，超過上下文（扣掉回答的空間）就縮短最大的工具結果；回傳縮短了幾則。

    `measured`＝(上一輪實際的提示詞 token 數, 當時 convo 的長度)：有的話前段用實測、只估新加的訊息，
    比整段估算準（估算刻意偏高，實測工具清單約多 8%）。
    """
    ctx = _context_limit(num_ctx, provider)
    if not ctx:
        return 0
    budget = ctx - max(_ANSWER_RESERVE_MIN, ctx // 16)

    def est(m: dict[str, Any]) -> int:
        text = m.get("content") or ""
        return (_estimate_data_tokens(text) if m.get("role") == "tool" else estimate_tokens(text)) + 8

    if measured and 0 < measured[1] <= len(convo):
        total = measured[0] + sum(est(m) for m in convo[measured[1]:])
    else:
        total = estimate_tokens(json.dumps(tools, ensure_ascii=False)) + sum(est(m) for m in convo)
    shrunk = 0
    while total > budget:
        cands = [m for m in convo if m.get("role") == "tool" and len(m.get("content") or "") > _TRIM_FLOOR + 200]
        if not cands:
            break
        m = max(cands, key=lambda x: len(x["content"]))
        text = m["content"]
        per_char = _estimate_data_tokens(text) / max(1, len(text))
        keep = max(_TRIM_FLOOR, len(text) - int((total - budget) / max(per_char, 0.01)) - 100)
        before = est(m)
        m["content"] = text[:keep] + _TRIM_NOTE
        total -= before - est(m)
        shrunk += 1
    return shrunk


_TRIMMED_NOTE = {
    "zh-TW": "（部分查詢結果太長，已截短才放得進模型的上下文長度 {ctx}；管理員可以在 AI 設定調大。）",
    "en-US": "(Some tool results were too long and were shortened to fit the model's context length of {ctx}; "
             "an admin can raise it in the AI settings.)",
    "ja-JP": "（一部の検索結果が長すぎるため、モデルのコンテキスト長 {ctx} に収まるよう短くしました。"
             "管理者は AI 設定で増やせます。）",
}


def _with_trimmed_note(answer: str, trimmed: int, num_ctx: int | None, provider: str, locale: str | None) -> str:
    ctx = _context_limit(num_ctx, provider)
    if not trimmed or not ctx:
        return answer
    note = _TRIMMED_NOTE.get(locale or "", _TRIMMED_NOTE["en-US"]).format(ctx=ctx)
    return f"{answer}\n\n{note}"


def _latest_question(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        text = m.get("content")
        if m.get("role") == "user" and isinstance(text, str) and text.strip():
            return text.strip()[:600]
    return ""


def _build_chat_context(
    messages: list[dict[str, Any]], locale: str | None,
    page_context: dict[str, Any] | None = None,
    allowed_tools: set[str] | None = None,
) -> Any:
    """共用：把 IPAM tools 轉 Ollama schema + 組 system prompt + 接上對話。

    回傳 (ollama_tools, convo)。chat / chat_stream 共用，避免兩份 prompt 不一致。
    allowed_tools 給定時依 RBAC 過濾掉使用者不可呼叫的工具（避免 LLM 浪費回合）。
    """
    from app.mcp.tools import TOOLS

    ollama_tools = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": meta["description"],
                "parameters": meta["parameters"],
            },
        }
        for name, meta in TOOLS.items()
        if allowed_tools is None or name in allowed_tools
    ]
    convo: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are jt-ipam's network operations assistant. Use the provided "
                "tools to answer questions about subnets, IPs (get_ip_detail / "
                "get_subnet_detail for full records), devices, DNS (servers/zones/"
                "consistency), VPN tunnels (WireGuard / IPsec / OpenVPN, incl. site-to-"
                "site), NAT, VLANs, VRFs, racks, sections, firewalls and their rules/"
                "aliases, network topology (get_topology), ARP/FDB, IP-allocation "
                "requests, scan agents, virtual machines, wireless links, customers, "
                "Wazuh security-coverage gaps (wazuh_missing_agents), TLS certificates and "
                "their distribution to hosts (list_certificates / list_cert_distribution — "
                "metadata only, private keys are never exposed), DHCP pool ranges "
                "(list_dhcp_ranges), anomaly detection results (list_anomalies — IP "
                "conflicts, MAC drifts, ghost IPs, unauthorised IPs, rogue DHCP servers; "
                "these are measured facts and can be stated plainly), and AI review "
                "findings (list_ai_findings — these are model inferences, not verified "
                "facts; say so when you repeat them). Both are admin-only. "
                "list_firewalls covers OPNsense, pfSense and FortiGate together; FortiGate "
                "policies and address objects have their own tools. Before saying you "
                "cannot determine something, check whether a relevant tool exists and "
                "call it (e.g. list_vpn_tunnels for site-to-site VPN, get_topology for "
                "how things connect, list_firewall_rules for firewall policy). "
                "When the user asks how many IPs are used / free / available / usable in a "
                "subnet or CIDR (e.g. '192.168.1.0/24 有多少可用 IP'), you MUST call "
                "get_subnet_detail (subnet_cidr=...) or get_subnet_usage to report jt-ipam's "
                "ACTUAL allocated/used/free counts and usage %. Do NOT answer with generic "
                "CIDR arithmetic (total addresses, usable-hosts = 2^n − 2, network/broadcast) "
                "— the user wants this IPAM's real data, not subnet math. Only say the subnet "
                "is not in IPAM if the tool reports no such subnet. "
                "Tools whose description says 'ADMIN ONLY' only work for admin users; "
                "do not attempt writes unless the user clearly asks to change data. "
                "Always cite the IPs / CIDRs / device names returned by tools, and copy "
                "every IP, CIDR, MAC, hostname and identifier VERBATIM, character for "
                "character — never translate, localise, reformat or alter a single digit. "
                "If a tool errors, explain it briefly. "
                "When the user asks WHERE something is in jt-ipam (a feature, page, setting or field; where to "
                "record, set or look something up), call find_ui_location and answer with the menu path, tab and "
                "label it returns, written with › as the tool does (plain text, never LaTeX such as $\\rightarrow$); "
                "never invent menu paths or labels. A data tool only returns the fields it "
                "returns: if the user asks about a field that is missing from a tool result, do NOT conclude that "
                "jt-ipam has no such field — call find_ui_location (with the object name and synonyms) first. "
                "Stay strictly on-topic: only answer questions about THIS jt-ipam system, "
                "its data (network / IPAM / devices / firewalls / DNS …) or how to use it. "
                "For unrelated questions (general coding, world knowledge, chit-chat), "
                "politely decline and suggest a dedicated general-purpose LLM platform "
                "such as Open WebUI or opencode instead. "
                "NEVER invent, guess, or extend data — only report exactly what "
                "tools return. If a tool returns no results (empty lists, zero counts, "
                "found=false), say plainly that none were found; NEVER fabricate example "
                "IPs, MACs, hostnames or subnets to illustrate an answer. "
                "When the user wants several or consecutive free IPs, "
                "call find_free_ips with the right count/consecutive ONCE; report only "
                "the IPs it returns, and if it returns fewer than requested, say so "
                "instead of making up more. "
                "Some list tools return has_more/next_offset; if a result is truncated "
                "or the user asks for more, tell them there are more and, when they ask "
                "for the next batch, call the SAME tool again with offset=next_offset. "
                "List replies carry total and truncated: report the total, and say so when truncated. "
                "When the question is about one subnet/CIDR and the tool takes subnet_cidr, you MUST "
                "pass it; when a reply carries 'scope', state it. "
                + DATA_RULE_EN + " "
                + _lang_instruction(locale)
                + _page_context_line(page_context)
                # 放在系統提示詞：上下文滿了 Ollama 會丟最舊的訊息，系統提示詞不會被丟
                + (f"\n\nThe user's current question (answer exactly this, using the tools as needed): "
                   f"{json.dumps(question, ensure_ascii=False)}" if (question := _latest_question(messages)) else "")
            ),
        }
    ]
    convo.extend(messages)
    return ollama_tools, convo


def _pending_mutations(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """挑出 tool_calls 中會異動資料的，回傳 {tool,args,title} 清單給前端確認。"""
    from app.mcp.tools import MUTATING_TOOLS, summarize_action

    pending: list[dict[str, Any]] = []
    for call in tool_calls:
        fn = call.get("function") or {}
        name = fn.get("name")
        if name not in MUTATING_TOOLS:
            continue
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        pending.append({"tool": name, "args": args, "title": summarize_action(name, args)})
    return pending


def _pending_prompt_text(pending: list[dict[str, Any]]) -> str:
    """模型回傳待確認動作卻沒給文字時，用動作標題組一句提示，避免空白回應。"""
    titles = [str(p.get("title") or p.get("tool") or "") for p in pending if p]
    titles = [t for t in titles if t]
    if not titles:
        return ""
    return "我準備執行以下動作，請確認後再進行：\n" + "\n".join(f"• {t}" for t in titles)


_UI_LINK_RE = re.compile(r"/[\w\-/]*(\?tab=[\w-]+)?")


def ui_links(convo: list[dict[str, Any]], limit: int = 4) -> list[dict[str, str]]:
    """這一輪 find_ui_location 給的站內連結 → 對話視窗回答下方的「前往」按鈕（使用者 2026-11-04）。

    連結只取自畫面地圖工具（網址由前端路由決定，不是模型寫的），其他工具的輸出一律不算。
    `links` 放在工具結果最前面，結果太長被截斷時也還完整。"""
    out: list[dict[str, str]] = []
    dec = json.JSONDecoder()
    for m in convo:
        if m.get("role") != "tool" or m.get("name") != "find_ui_location":
            continue
        blob = m.get("content")
        if not isinstance(blob, str):
            continue
        at = blob.find('"links": [')
        if at < 0:
            continue
        try:
            arr, _end = dec.raw_decode(blob, at + len('"links": '))
        except ValueError:
            continue
        for x in arr if isinstance(arr, list) else []:
            href, label = (x.get("href"), x.get("label")) if isinstance(x, dict) else (None, None)
            if not (isinstance(href, str) and isinstance(label, str) and _UI_LINK_RE.fullmatch(href)) \
                    or href.startswith("//") or any(y["href"] == href for y in out):
                continue
            out.append({"label": label[:80], "href": href})
            if len(out) >= limit:
                return out
    return out


async def _run_tool_calls(session: AsyncSession, user: Any, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """執行 LLM 要求的 tool_calls，回傳要 append 進 convo 的 tool 訊息（含 name）。"""
    from app.mcp.tools import TOOLS, IPAMToolError, authorize_tool, call_tool

    out: list[dict[str, Any]] = []
    for call in tool_calls:
        fn = call.get("function") or {}
        name = fn.get("name")
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        denied = await authorize_tool(session, user, name) if name in TOOLS else None
        if name not in TOOLS:
            tool_result: Any = {"error": f"unknown tool {name!r}"}
        elif denied is not None:
            tool_result = {"error": denied}
        else:
            try:
                # 共用入口：先檢查參數格式、在 SAVEPOINT 裡跑 —— 一個工具出錯不會讓後面的工具與存對話紀錄跟著失敗
                tool_result = await call_tool(session, user, name, args)
            except IPAMToolError as exc:
                tool_result = {"error": str(exc)}
            except Exception as exc:
                # 工具結果會隨 trace_messages 回到畫面。只給類別名稱＝把「為什麼壞了」丟掉（#34 時刻意
                # 保留原文）；但原文整段給任何登入帳號看，資料庫錯誤會帶出 SQL 與參數（CodeQL #11，
                # 2026-10-01）。折衷：管理員保留第一行（SQLAlchemy 的 [SQL: …]／[parameters: …] 在後面
                # 幾行），一般帳號只給類別名稱；完整原文一律寫進日誌。
                log.warning("AI tool %s failed", name, exc_info=exc)
                first = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else ""
                show = first if bool(getattr(user, "is_admin", False)) else ""
                tool_result = {"error": f"tool failed: {exc.__class__.__name__}" + (f": {show}" if show else "")}
        blob = json.dumps(tool_result, ensure_ascii=False, default=str)
        # 防止單一工具回傳過大撐爆上下文 → 模型變慢甚至 ReadTimeout
        if len(blob) > _TOOL_RESULT_CAP:
            blob = blob[:_TOOL_RESULT_CAP] + " …[truncated; 結果過多，請縮小範圍或加篩選條件]"
        msg: dict[str, Any] = {"role": "tool", "name": name, "content": blob}
        # OpenAI 相容：工具結果要用 tool_call_id 對回那一個呼叫，缺了嚴格的服務會拒絕整個請求。
        # Ollama 的呼叫沒有 id，就不要憑空生一個。
        if call.get("id"):
            msg["tool_call_id"] = call["id"]
        out.append(msg)
    return out


async def chat_stream(
    session: AsyncSession,
    *,
    user: Any,
    messages: list[dict[str, Any]],
    locale: str | None = None,
    max_iterations: int = 4,
    page_context: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """chat 的 streaming 版：逐 token 把最終回答吐出來（規格 §11.1，本地推論不外送）。

    yield 的事件（給 SSE endpoint 包成 data: ...）：
      {"type": "token",      "text": ...}    最終回答的增量片段
      {"type": "tool",       "name": ...}    正在執行某個工具
      {"type": "tool_round"}                 該輪是 tool round，前端應清掉已收到的暫存 token
      {"type": "done",       "answer": ..., "trace_messages": convo}
      {"type": "error",      "detail": ...}

    串流時無法在中途改 HTTP status，故所有錯誤都以 error 事件回報；config 未開的
    503 由 endpoint 在開串流前先擋掉。
    """
    from app.services.system_config import get_llm_config
    cfg = await get_llm_config(session)
    admin = bool(getattr(user, "is_admin", False))
    if not cfg.enabled:
        yield {"type": "error", **ai_error_event(None, admin=admin, code="ai_disabled")}
        return

    from app.mcp.tools import allowed_tool_names
    _allowed = await allowed_tool_names(session, user)
    ollama_tools, convo = _build_chat_context(messages, locale, page_context, _allowed)
    url = chat_url(cfg.url, cfg.provider)
    started = time.monotonic()

    def _meta() -> dict[str, Any]:
        return {"model": cfg.chat_model, "elapsed_ms": int((time.monotonic() - started) * 1000)}

    measured: tuple[int, int] | None = None
    trimmed = 0

    def _final(answer: str) -> str:
        return _with_trimmed_note(_annotate_unverified(answer, convo, locale), trimmed, cfg.num_ctx,
                                  cfg.provider, locale)

    for _ in range(max_iterations):
        trimmed += _fit_context(convo, ollama_tools, num_ctx=cfg.num_ctx, provider=cfg.provider, measured=measured)
        sent_len = len(convo)
        # chat_body 依服務類型組主體（options 是 Ollama 專屬，送給 OpenAI 端點會被拒絕）
        body = {**chat_body(cfg, cfg.provider, messages=convo, tools=ollama_tools), "stream": True}
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_buf = _ToolCallBuffer()
        done_reason: str | None = None
        prompt_tokens: int | None = None
        # 思考內容不是答案，但**使用者需要知道它在動**：只吐 thinking 的那段時間，
        # 畫面上原本什麼都沒有，看起來就像當機（實測 gemma4 可以想十幾秒）。
        thinking_chars = 0
        thinking_reported = -1
        try:
            async with _chat_stream(url, body, auth_headers(cfg.provider, cfg.api_key), cfg.timeout) as resp:
                if resp.status_code != 200:
                    detail = (await resp.aread()).decode("utf-8", "replace")[:200]
                    yield {"type": "error", **ai_error_event(
                        None, admin=admin, code="ai_upstream_error",
                        reason=f"{provider_label(cfg.provider)} chat {resp.status_code}: {detail}")}
                    return
                async for line in resp.aiter_lines():
                    chunk = _stream_payload(line)
                    if chunk is None:
                        continue
                    d = _stream_delta(chunk)
                    if d.error:
                        yield {"type": "error", **ai_error_event(
                            None, admin=admin, code="ai_upstream_error", reason=f"LLM: {d.error[:200]}")}
                        return
                    if d.thinking:
                        thinking_chars += len(d.thinking)
                        # 節流：每累積約 200 字回報一次，不要用事件洪水灌前端
                        if thinking_chars - thinking_reported >= 200:
                            thinking_reported = thinking_chars
                            yield {"type": "thinking", "chars": thinking_chars}
                    if d.content:
                        content_parts.append(d.content)
                        yield {"type": "token", "text": d.content}
                    tool_buf.add(d.tool_calls)
                    if d.prompt_tokens is not None:
                        prompt_tokens = d.prompt_tokens
                    if d.done:
                        done_reason = d.done_reason
                        break
            tool_calls = tool_buf.calls()
            if prompt_tokens:
                measured = (prompt_tokens, sent_len)
        except UnsafeOutboundURL as exc:
            yield {"type": "error", **ai_error_event(exc, admin=admin, code="ai_ssrf_blocked",
                                                     reason=f"SSRF guard: {exc}")}
            return
        except httpx.HTTPError as exc:
            yield {"type": "error", **ai_error_event(exc, admin=admin, code="ai_unreachable",
                                                     reason=f"transport: {transport_detail(exc)}")}
            return

        full_content = "".join(content_parts)
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": full_content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        convo.append(assistant_msg)

        if not tool_calls:
            # 後援：模型偶發把工具呼叫寫成文字（而非結構化 tool_calls）→ 還原；
            # 後面的 tool_round 事件會叫前端清掉剛串流出去的呼叫文字。
            inline = _with_call_ids(_inline_tool_calls(full_content, _allowed), cfg.provider)
            if inline:
                assistant_msg["tool_calls"] = inline
                assistant_msg["content"] = ""
                tool_calls = inline
            elif _looks_like_tool_leak(full_content):
                yield {"type": "tool_round"}
                yield {"type": "done", "answer": _tool_leak_message(locale),
                       "trace_messages": convo, **_meta()}
                return
            elif not full_content.strip():
                # 這輪沒有工具呼叫、content 也是空的。常見成因：模型只吐了 thinking
                # （Ollama 會放在另一個欄位）、或撞到輸出／上下文長度上限。
                # 直接把空字串當答案回去，前端只會顯示「(沒有回應)」，使用者無從判斷。
                # → 再逼問一次；仍然空的話講清楚原因。
                yield {"type": "tool_round"}
                retry = await _force_final_answer(cfg, convo)
                yield {"type": "done",
                       "answer": retry or _empty_answer_message(locale, done_reason, prompt_tokens, cfg.num_ctx),
                       "trace_messages": convo, **_meta()}
                return
            else:
                yield {"type": "done", "answer": _final(full_content), "trace_messages": convo, **_meta()}
                return

        # 異動類工具：不直接執行，回傳待確認動作給前端，等使用者按「確認」
        pending = _pending_mutations(tool_calls)
        if pending:
            yield {"type": "pending_action", "actions": pending}
            return

        # 這輪是 tool round：剛吐的 token（若有，多半是 thinking）不是最終答案，叫前端清掉
        yield {"type": "tool_round"}
        for call in tool_calls:
            yield {"type": "tool", "name": (call.get("function") or {}).get("name")}
        convo.extend(await _run_tool_calls(session, user, tool_calls))

    # max_iterations 用完 → 不給工具，串流最後一次強制作答
    yield {"type": "tool_round"}
    trimmed += _fit_context(convo, [], num_ctx=cfg.num_ctx, provider=cfg.provider)
    final_parts: list[str] = []
    body = {**chat_body(cfg, cfg.provider, messages=convo, tools=[]), "stream": True}
    try:
        async with _chat_stream(url, body, auth_headers(cfg.provider, cfg.api_key), cfg.timeout) as resp:
            if resp.status_code == 200:
                async for line in resp.aiter_lines():
                    chunk = _stream_payload(line)
                    if chunk is None:
                        continue
                    d = _stream_delta(chunk)
                    if d.content:
                        final_parts.append(d.content)
                        yield {"type": "token", "text": d.content}
                    if d.done:
                        break
    except (UnsafeOutboundURL, httpx.HTTPError):
        pass
    answer = "".join(final_parts) or _steps_exceeded_message(locale)
    if final_parts:
        convo.append({"role": "assistant", "content": answer})
    yield {"type": "done", "answer": _final(answer), "trace_messages": convo, **_meta()}


# ─────────────────── 全表 reindex（admin 一次性） ───────────────────


async def reindex_all(session: AsyncSession) -> dict[str, Any]:
    """重新計算所有有 description 的物件的 embedding。慢；只在初始化或換 model 時跑。

    **失敗筆數與原因要一起回報。** 只回成功數的話，「全部失敗」看起來會跟「沒有東西
    要索引」一模一樣 —— 實機上就是這樣：嵌入模型回 4096 維、欄位是 vector(768)，
    每一筆都丟例外被吞掉，reindex 回 0/0/0，語意搜尋從頭到尾沒有真的運作過。
    """
    from app.models.address import IPAddress
    from app.models.device import Device
    from app.models.subnet import Subnet

    stats: dict[str, Any] = {"subnets": 0, "ip_addresses": 0, "devices": 0,
                             "failed": 0, "error": None, "error_code": None, "error_params": None}

    def _note(exc: Exception) -> None:
        stats["failed"] = int(stats["failed"]) + 1
        if not stats["error"]:
            stats["error"] = str(exc)[:300]
            # 代碼化：前端翻成三語（維度不合時帶模型回幾維、需要幾維）
            stats["error_code"] = getattr(exc, "code", None)
            stats["error_params"] = getattr(exc, "params", None)

    # 分批 commit：整表放在同一個交易裡，會跟同時在跑的整合同步（jt-ipam-sync 每 ~5 分鐘
    # 更新同一批 ip_addresses）互鎖 —— 實機第一次真的跑得動 reindex 就撞到 deadlock，
    # 整批因此中止。每 N 筆放掉一次鎖，衝突視窗就只剩下這 N 筆。
    _BATCH = 25

    sub_rows = (
        await session.execute(
            select(Subnet.id, Subnet.description).where(Subnet.description.isnot(None))
        )
    ).all()
    for sid, desc in sub_rows:
        try:
            if await index_subnet(session, str(sid), desc, raise_on_error=True):
                stats["subnets"] += 1
        except (AIError, AINotConfigured) as exc:
            _note(exc)
        if (stats["subnets"] + stats["failed"]) % _BATCH == 0:
            await session.commit()
    await session.commit()

    ip_rows = (
        await session.execute(
            select(IPAddress.id, IPAddress.description).where(
                IPAddress.description.isnot(None)
            )
        )
    ).all()
    for iid, desc in ip_rows:
        try:
            if await index_ip(session, str(iid), desc, raise_on_error=True):
                stats["ip_addresses"] += 1
        except (AIError, AINotConfigured) as exc:
            _note(exc)
        if (stats["ip_addresses"] + stats["failed"]) % _BATCH == 0:
            await session.commit()
    await session.commit()

    dev_rows = (
        await session.execute(
            select(Device.id, Device.description).where(Device.description.isnot(None))
        )
    ).all()
    for did, desc in dev_rows:
        try:
            if await index_device(session, str(did), desc, raise_on_error=True):
                stats["devices"] += 1
        except (AIError, AINotConfigured) as exc:
            _note(exc)
        if (stats["devices"] + stats["failed"]) % _BATCH == 0:
            await session.commit()
    await session.commit()

    # 記下這次重建用的是哪個模型：之後換了模型，索引狀態才知道既有的向量是舊模型的（需要重建）
    await _save_index_record(session, stats)
    await session.commit()
    return stats


INDEX_RECORD_KEY = "semantic_index"


async def _save_index_record(session: AsyncSession, stats: dict[str, Any]) -> None:
    from app.models.system_setting import SystemSetting
    from app.services.system_config import get_llm_config
    cfg = await get_llm_config(session)
    rec = {"model": cfg.embedding_model, "dim": get_settings().embedding_dim,
           "at": datetime.now(UTC).isoformat(),
           "indexed": int(stats["subnets"]) + int(stats["ip_addresses"]) + int(stats["devices"]),
           "failed": int(stats["failed"])}
    row = await session.get(SystemSetting, INDEX_RECORD_KEY)
    if row is None:
        session.add(SystemSetting(key=INDEX_RECORD_KEY, value=rec))
    else:
        row.value = rec


async def index_status(session: AsyncSession) -> dict[str, Any]:
    """語意搜尋的索引需不需要重建（使用者 2026-10-13：「看不出來要不要按」）。

    - never：還沒有重建紀錄（升級前建的、或從沒重建過）
    - model_changed：上次重建用的模型跟現在設定的不同 → 既有向量是舊模型的，跟新的查詢配不起來
    - missing：有說明卻沒有向量（換模型或維度不合期間新增、修改的；或單筆索引失敗）
    """
    from app.models.system_setting import SystemSetting
    from app.services.system_config import get_llm_config
    cfg = await get_llm_config(session)
    counts: dict[str, dict[str, int]] = {}
    for table in ("subnets", "ip_addresses", "devices"):
        row = (await session.execute(text(
            f"SELECT count(*) FILTER (WHERE coalesce(btrim(description), '') <> '') AS described, "  # noqa: S608 - 固定表名
            f"count(*) FILTER (WHERE coalesce(btrim(description), '') <> '' "
            f"AND description_embedding IS NOT NULL) AS indexed FROM {table}"))).one()
        counts[table] = {"described": int(row.described), "indexed": int(row.indexed)}
    described = sum(c["described"] for c in counts.values())
    missing = sum(c["described"] - c["indexed"] for c in counts.values())
    rec_row = await session.get(SystemSetting, INDEX_RECORD_KEY)
    last = rec_row.value if rec_row is not None and isinstance(rec_row.value, dict) else None
    reason: str | None = None
    if described:
        if last is None:
            reason = "never"
        elif last.get("model") != cfg.embedding_model:
            reason = "model_changed"
        elif missing:
            reason = "missing"
    return {"model": cfg.embedding_model, "last": last, "counts": counts, "described": described,
            "missing": missing, "needs_reindex": reason is not None, "reason": reason}


async def raw_chat(session: AsyncSession, prompt: str, timeout: float | None = None,
                   model: str | None = None, force_json: bool = False,
                   max_output_tokens: int | str | None = None, no_thinking: bool = False,
                   num_ctx: int | None = None,
                   on_chunk: Callable[[str, str], Awaitable[None]] | None = None,
                   role: str = "chat") -> str:
    """單次、不帶工具的對話 —— 給 AI 巡檢這類「送一段提示詞、要一段結構化輸出」的用途。

    刻意**不掛 IPAM 工具**：巡檢的資料已經由呼叫端依可見範圍取好並放進提示詞裡，
    再讓模型去呼叫工具只會多一條繞過權限的路。

    `timeout` 留空＝用 LLM 設定裡的值（那個是為互動對話調的）。背景批次要自己給一個
    夠長的值 —— 幾百筆資料的提示詞用互動逾時去跑，幾乎每次都會逾時。
    `model` 留空＝用設定的對話模型。
    `force_json=True` 會要求 Ollama 只輸出 JSON —— 光靠提示詞說「只回 JSON」不夠，
    模型（尤其提示詞很長時）常常改寫一段散文回來。
    `max_output_tokens` 限制產出長度。沒有上限的話，模型有機會卡在重複輸出的迴圈裡，
    把整個逾時燒完才失敗（實測看過一批產出 34,000 字還沒停）。傳 `"auto"`＝依實際使用的
    上下文長度自動計算（output_budget；功能程式一律用這個，有守門測試）。
    `no_thinking=True` 關掉思考模式。**思考過程也算在產出額度裡** —— 實測 gemma4 一批
    寫了 10,401 字的思考，結果真正的答案被額度切斷。舊版 Ollama 不認這個欄位，
    被拒絕時會自動退回不帶它重送。
    `role` 是用途（chat／interpret／audit）：判讀、巡檢可以接自己的 LLM Server 與模型，沒設就繼承對話設定
    （system_config.role_config）。`model`／`num_ctx` 有給的話再蓋過去。
    `on_chunk(片段, 種類)` 有給就改走串流：一批要跑好幾分鐘，沒有中途訊號的話畫面上
    完全看不出模型是在算還是已經卡死。種類是 "thinking"（思考過程）或 "content"
    （最終輸出）—— 會思考的模型前幾分鐘只吐 thinking，兩者要分開才講得清楚現況。
    """
    from app.services.system_config import get_llm_config, role_config
    cfg = role_config(await get_llm_config(session), role)
    if not cfg.enabled:
        raise AINotConfigured("LLM is disabled")
    url = chat_url(cfg.url, cfg.provider)
    limit = (output_budget(prompt, num_ctx or getattr(cfg, "num_ctx", None), cfg.provider)
             if max_output_tokens == "auto" else (int(max_output_tokens) if max_output_tokens else None))
    body = json_chat_body(
        cfg, cfg.provider, prompt=prompt, stream=on_chunk is not None,
        force_json=force_json, max_output_tokens=limit, num_ctx=num_ctx,
        no_thinking=no_thinking, model=model,
    )
    wait = timeout or cfg.timeout
    try:
        if on_chunk is not None:
            return await _raw_chat_streamed(url, body, wait, on_chunk,
                                            auth_headers(cfg.provider, cfg.api_key),
                                            cfg.provider)
        resp = await _post_dropping_rejected(url, body, auth_headers(cfg.provider, cfg.api_key), wait)
    except UnsafeOutboundURL as exc:
        raise AIBlocked(f"SSRF guard: {exc}") from exc
    except httpx.ReadTimeout as exc:
        raise AITimeout(
            f"LLM 伺服器在 {int(wait)} 秒內沒有回覆完（模型太慢或資料量太大）"
        ) from exc
    except httpx.HTTPError as exc:
        raise AIUnreachable(f"transport: {transport_detail(exc)}") from exc
    if resp.status_code != 200:
        raise AIUpstream(f"{provider_label(cfg.provider)} chat {resp.status_code}: {resp.text[:200]}")
    # 兩家結構不同：Ollama 是 message、OpenAI 是 choices[0].message
    return _answer_or_explain(resp.json(), max_tokens=body.get("max_tokens")
                              or (body.get("options") or {}).get("num_predict"))


async def _post_dropping_rejected(url: str, body: dict[str, Any], headers: dict[str, str], wait: float) -> Any:
    """非串流送出；被拒絕的「不要思考」欄位一個一個拿掉重送（每次只拿掉被點名的；最多試到欄位用完）。"""
    resp = await safe_request("POST", url, headers=headers, json=body, timeout=wait)
    for _ in range(len(_REASONING_OFF_KEYS) + 1):
        if resp.status_code == 200 or not (
                ("think" in body and _rejected_think(resp))
                or _strip_rejected_controls(body, resp.status_code, getattr(resp, "text", ""), url)):
            break
        if "think" in body and _rejected_think(resp):
            body.pop("think", None)
        resp = await safe_request("POST", url, headers=headers, json=body, timeout=wait)
    return resp


#: 思考檢查的問題與產出上限：思考沒關的話，思考內容也算在額度裡，很快就會停 ——
#: 不會為了一次檢查讓模型想好幾分鐘。
_THINK_PROBE_PROMPT = "Reply with exactly one word: OK"
_THINK_PROBE_TOKENS = 64
_THINK_TAG = re.compile(r"<think>.*?(</think>|$)", re.S | re.I)


async def probe_thinking(session: AsyncSession, model: str | None = None, role: str = "chat") -> dict[str, Any]:
    """用 AI 巡檢／判讀同一套「關閉思考」參數問模型一句極短的話，看它照不照做。

    伺服器或閘道沒照做時（LiteLLM 沒轉送、舊版 llama.cpp 忽略 reasoning_effort……），症狀只是
    「很慢」或「答案被思考吃掉額度」，不會報錯 —— 設定頁要能當場問一次。
    **回答是空的也算在思考**：有些閘道不把思考內容轉出來，額度被思考用光、正文是空的；只看
    思考欄位的話會誤報成「不會先思考」（jt-doc-tools 實測 LiteLLM 的 ollama/）。
    """
    from app.services.system_config import get_llm_config, role_config
    cfg = role_config(await get_llm_config(session), role)
    model = model or cfg.chat_model
    out: dict[str, Any] = {"model": model, "ok": False, "thinking": None, "reasoning_chars": 0,
                           "think_tag": False, "empty_answer": False, "answer": "", "seconds": 0.0,
                           "rejected_params": [], "error": None}
    if not cfg.enabled:
        out["error"] = "LLM is disabled"
        return out
    url = chat_url(cfg.url, cfg.provider)
    body = json_chat_body(cfg, cfg.provider, prompt=_THINK_PROBE_PROMPT, stream=False, force_json=False,
                          max_output_tokens=_THINK_PROBE_TOKENS, num_ctx=None, no_thinking=True, model=model)
    sent_think = "think" in body
    t0 = time.monotonic()
    try:
        resp = await _post_dropping_rejected(url, body, auth_headers(cfg.provider, cfg.api_key),
                                             min(float(cfg.timeout or 60), 120.0))
    except UnsafeOutboundURL as exc:
        out["error"] = f"SSRF guard: {exc}"
        return out
    except httpx.HTTPError as exc:
        out["error"] = f"transport: {transport_detail(exc)}"
        return out
    out["seconds"] = round(time.monotonic() - t0, 1)
    rejected = sorted(_rejected_controls(url, model))
    if sent_think and "think" not in body:
        rejected.append("think")
    out["rejected_params"] = rejected
    if resp.status_code != 200:
        out["error"] = f"{provider_label(cfg.provider)} chat {resp.status_code}: {(resp.text or '')[:200]}"
        return out
    data = resp.json() or {}
    msg = data.get("message") or ((data.get("choices") or [{}])[0] or {}).get("message") or {}
    content = str(msg.get("content") or "")
    reasoning = str(msg.get("reasoning_content") or msg.get("reasoning") or msg.get("thinking") or "")
    tag = bool(_THINK_TAG.search(content))
    answer = _THINK_TAG.sub("", content).strip()
    out.update(ok=True, reasoning_chars=len(reasoning), think_tag=tag, empty_answer=not answer,
               answer=answer[:40], thinking=bool(reasoning or tag or not answer))
    return out


def interpret_model(cfg: Any) -> str:
    """AI 判讀實際用的模型：有設判讀專用模型就用它，否則沿用對話模型。"""
    from app.services.system_config import role_config
    return role_config(cfg, "interpret").chat_model


async def interpret_chat(session: AsyncSession, prompt: str, **kw: Any) -> tuple[str, str]:
    """AI 判讀（未授權 IP 判讀／IP 調查／防火牆規則異動解讀）共用的單次對話。

    三個功能共用一個「判讀模型」設定，所以模型與上下文長度統一在這裡決定 —— 各自呼叫
    raw_chat 的話，下一個新加的判讀功能很容易又忘了帶、安靜地用回對話模型。
    回傳 (內容, 模型名)：判讀出自哪個模型是品質的一部分，畫面要顯示真的那個。
    """
    from app.services.system_config import get_llm_config
    cfg = await get_llm_config(session)
    text = await raw_chat(session, prompt, role="interpret", **kw)
    return text, interpret_model(cfg)


def _rejected_think(resp: Any) -> bool:
    """舊版 Ollama 不認 `think` 欄位 —— 認出這種錯誤，好退回不帶它重送。"""
    if resp.status_code == 200 or "think" not in (resp.text or "").lower():
        return False
    return True


async def _raw_chat_streamed(
    url: str, body: dict[str, Any], wait: float,
    on_chunk: Callable[[str, str], Awaitable[None]],
    headers: dict[str, str] | None = None,
    provider: str = "ollama",
) -> str:
    """串流版：邊收邊回報，最後把整段內容拼回來給呼叫端解析。"""
    try:
        for _ in range(len(_REASONING_OFF_KEYS)):
            try:
                return await _stream_once(url, body, wait, on_chunk, headers, provider)
            except _Rejected as exc:
                # 自架服務或閘道不認某個「不要思考」的欄位：只拿掉被點名的再來，而不是整批失敗
                if not _strip_rejected_controls(body, exc.status, exc.text, url):
                    raise
        return await _stream_once(url, body, wait, on_chunk, headers, provider)
    except AIError as exc:
        # 舊版 Ollama 不認 `think`：拿掉重來一次，而不是整批失敗
        if "think" not in str(exc).lower() or "think" not in body:
            raise
        body.pop("think", None)
        return await _stream_once(url, body, wait, on_chunk, headers, provider)


class _Rejected(AIError):
    """串流請求被伺服器以非 200 拒絕（保留狀態碼與原文，好判斷要不要換個請求再送）。"""

    def __init__(self, message: str, status: int, text: str) -> None:
        super().__init__(message)
        self.status = status
        self.text = text


async def _stream_once(
    url: str, body: dict[str, Any], wait: float,
    on_chunk: Callable[[str, str], Awaitable[None]],
    headers: dict[str, str] | None = None,
    provider: str = "ollama",
) -> str:
    parts: list[str] = []
    async with safe_stream("POST", url,
                           headers=headers or {"Content-Type": "application/json"},
                           json=body, timeout=wait) as resp:
        if resp.status_code != 200:
            full = (await resp.aread()).decode("utf-8", "replace")
            raise _Rejected(f"{provider_label(provider)} chat {resp.status_code}: {full[:200]}",
                            resp.status_code, full)
        thinking_chars = 0
        done_reason: str | None = None
        async for line in resp.aiter_lines():
            data = _stream_payload(line)
            if data is None:
                continue
            # 兩種格式（Ollama 逐行 JSON／OpenAI SSE）都由 _stream_payload／_stream_delta 處理，
            # 這支輔助函式拿不到 cfg，也不需要。
            d = _stream_delta(data)
            if d.error:
                raise AIUpstream(f"LLM: {d.error[:200]}")
            # 會思考的模型（gemma4 等）先吐一大段 thinking，content 要等到最後才出現。
            # 只看 content 的話，畫面會停住好幾分鐘完全沒有動靜 —— 實際上模型正在想。
            if d.thinking:
                thinking_chars += len(d.thinking)
                await on_chunk(d.thinking, "thinking")
            if d.content:
                parts.append(d.content)
                await on_chunk(d.content, "content")
            if d.done:
                done_reason = d.done_reason
                break
    out = "".join(parts)
    if not out.strip() and done_reason == "length":
        raise _cut_off_while_thinking(
            thinking_chars, body.get("max_tokens") or (body.get("options") or {}).get("num_predict"))
    return ChatText(out, truncated=done_reason == "length")
