"""AI chat 輸入安全檢查（防 prompt-injection / 防 DoS）。

設計取捨：
- 只檢查 role == "user" 的內容（system/assistant 是我們自己或先前回應，不重複檢查）
- 注入偵測採「高訊號片語」白名單式 regex，避免誤殺正常 IPAM 提問
- 結構性防線在別處（工具皆唯讀、LLM 輸出當不可信、輸入長度上限、專屬 rate limit）；
  本模組是 defense-in-depth

OWASP A03（Injection）/ A04（Insecure Design — 資源濫用）。
"""

from __future__ import annotations

import re

# 明顯的 prompt-injection / role-hijack 片語（英中皆涵蓋常見手法）
_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(all\s+|the\s+)?(previous|prior|above|earlier)\s+(instructions|prompts?|messages?)",
        r"disregard\s+(all\s+|the\s+)?(previous|prior|above|system)",
        r"reveal\s+(your\s+|the\s+)?(system\s+)?prompt",
        r"(show|print|repeat)\s+(your\s+|the\s+)?(system\s+)?(prompt|instructions)",
        r"\byou\s+are\s+now\b",
        r"\bdeveloper\s+mode\b",
        r"\bjailbreak\b",
        r"\bDAN\b",
        r"忽略(以上|先前|前面|之前).{0,6}(指令|指示|提示)",
        r"(洩漏|顯示|印出).{0,6}(系統)?(提示詞|prompt|指令)",
    )
]

# 單一字元連續重複的上限（防灌爆 / 拖垮推論）
_MAX_CHAR_RUN = 200
# 允許的控制字元（換行 / tab / Enter）
_ALLOWED_CTRL = {"\n", "\t", "\r"}


class AIInputRejected(Exception):
    """輸入未通過安全檢查；endpoint 應回 400（不送進 LLM）。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _has_excessive_repetition(text: str) -> bool:
    run_char = ""
    run = 0
    for ch in text:
        if ch == run_char:
            run += 1
            if run >= _MAX_CHAR_RUN:
                return True
        else:
            run_char = ch
            run = 1
    return False


def _has_control_chars(text: str) -> bool:
    return any(ord(ch) < 32 and ch not in _ALLOWED_CTRL for ch in text)


def screen_text(text: str) -> None:
    """單段使用者文字的安全檢查；不通過 raise AIInputRejected。"""
    if _has_control_chars(text):
        raise AIInputRejected("control_chars")
    if _has_excessive_repetition(text):
        raise AIInputRejected("excessive_repetition")
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            raise AIInputRejected("prompt_injection")


def screen_user_messages(messages: list[dict[str, object]]) -> None:
    """檢查整串對話中所有 user 訊息；任一不過即 raise。"""
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            screen_text(content)
