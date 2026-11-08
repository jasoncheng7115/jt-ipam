"""憑證推送的共用型別（見 models/cert_push_target.py 的說明）。

每家設備一個 driver（drivers.py），流程都是：
  讀目前設定（換回去要用）→ 匯入或替換憑證 → 換上 → （引擎）連過去確認對方出示的是新憑證 → 不是就換回去。
driver 只透過 Http 跟設備說話，測試把 Http 換成模擬設備（tests/test_cert_push.py）。
設備回的錯誤訊息是對方系統的文字，只當資料顯示，不拿來做判斷以外的事。
"""
from __future__ import annotations

import base64
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Bundle:
    """要推的那一版。key_pem 是解密後的明文，只在記憶體裡、推完就丟。"""
    cert_name: str
    fingerprint: str
    cert_pem: str
    chain_pem: str
    key_pem: str

    @property
    def fullchain(self) -> str:
        parts = [self.cert_pem.strip()]
        if self.chain_pem and self.chain_pem.strip():
            parts.append(self.chain_pem.strip())
        return "\n".join(parts) + "\n"

    def device_name(self, limit: int = 35) -> str:
        """設備上的憑證名稱：jtipam-<名稱>-<指紋前 8 碼>。同一版重推是同一個名字（可重複執行）。"""
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", self.cert_name).strip("-")[: max(1, limit - 16)] or "cert"
        return f"jtipam-{slug}-{self.fingerprint[:8]}"


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


class PushError(Exception):
    """推送失敗。step＝在哪一步、code＝給畫面翻譯的代碼、detail＝設備回的原文（截短）。"""

    def __init__(self, step: str, code: str, detail: str = "") -> None:
        super().__init__(f"{step}: {code}: {detail}")
        self.step, self.code, self.detail = step, code, (detail or "")[:500]


class Response(Protocol):
    status_code: int
    text: str

    def json(self) -> Any: ...


class Http(Protocol):
    async def __call__(self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None,
                       content: bytes | None = None, headers: dict[str, str] | None = None,
                       timeout: float = 30.0) -> Response: ...


@dataclass
class Steps:
    """做了什麼、在哪一步失敗；存進 last_steps 給畫面看。

    listener：每完成一步、每換一個階段就被叫一次（items, 目前階段），推送中的畫面靠它顯示做到哪裡
    （使用者 2026-11-07）。回報失敗絕不影響推送本身。
    """
    items: list[dict[str, Any]] = field(default_factory=list)
    listener: Callable[[list[dict[str, Any]], str | None], None] | None = None
    current: str | None = None

    def _notify(self) -> None:
        if self.listener is None:
            return
        try:
            self.listener(self.items, self.current)
        except Exception:  # 進度只是顯示用，回報失敗照樣推下去
            return

    def stage(self, name: str) -> None:
        """進入一個階段（推之前先連、推送、確認、換回）：畫面顯示「目前：…」。"""
        self.current = name
        self._notify()

    def ok(self, step: str, detail: str = "") -> None:
        self.items.append({"step": step, "ok": True, "detail": detail[:300]})
        self._notify()

    def fail(self, step: str, code: str, detail: str = "") -> None:
        self.items.append({"step": step, "ok": False, "code": code, "detail": detail[:300]})
        self._notify()

    def warn(self, step: str, code: str, detail: str = "") -> None:
        """沒失敗、但也沒做到（例如沒有確認位址所以沒確認）：畫面不打勾，寫出原因（使用者 2026-11-07）。"""
        self.items.append({"step": step, "ok": True, "warn": True, "code": code, "detail": detail[:300]})
        self._notify()


Rollback = Callable[[], Awaitable[None]]


class Driver(Protocol):
    kind: str

    async def push(self, bundle: Bundle, steps: Steps) -> Rollback | None:
        """推上去並換上；回傳「換回去」的函式（引擎確認失敗時呼叫），沒有東西要換回就回 None。"""
        ...
