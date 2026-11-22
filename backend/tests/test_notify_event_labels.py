"""通知矩陣的每個事件都要有三種語言的名稱（2026-11-22 發版 e2e 抓到）。

畫面用 `notify_ch.ev.<事件代碼把 . 換成 _>` 組出翻譯鍵；鍵是動態組的，i18n 檢查（掃原始碼裡寫死的鍵）看不到。
Kubernetes 位址衝突加進事件清單時漏了翻譯，通知設定頁直接顯示 `notify_ch.ev.anomaly_k8s_conflicts`。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.services.system_config import NOTIFY_EVENTS

I18N = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"


def test_every_notify_event_has_a_label_in_every_language() -> None:
    missing = []
    for loc in ("zh-TW", "en-US", "ja-JP"):
        ev = json.loads((I18N / f"{loc}.json").read_text(encoding="utf-8"))["notify_ch"]["ev"]
        missing += [f"{loc}:{e}" for e, *_ in NOTIFY_EVENTS if not ev.get(e.replace(".", "_"))]
    assert not missing, f"通知事件沒有翻譯：{missing}"
