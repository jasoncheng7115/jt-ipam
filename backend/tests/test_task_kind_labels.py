"""守門：每一種背景作業類型在三個語系都有顯示名稱（使用者 2026-10-09 看到作業頁直接顯示
`checkpoint_gaia.sync` 與 `checkpoint.sync`，問兩個差在哪）。

作業頁把類型翻成看得懂的名字（frontend/src/utils/taskKind.ts），沒翻譯的就照內部名稱顯示 ——
不會壞，但又回到看不懂。新增作業類型時要在 i18n 的 `tasks.kinds` 補上，鍵是點號換成底線。
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
_KIND = re.compile(r'''(?:\bkind=|\bKIND = |record_refresh\(session, )"([a-z0-9_]+\.[a-z0-9_]+)"''')


def _kinds() -> set[str]:
    out: set[str] = set()
    for p in [*(ROOT / "backend" / "app").rglob("*.py"), *(ROOT / "scripts").glob("*.py")]:
        out |= set(_KIND.findall(p.read_text(encoding="utf-8")))
    return out


def test_every_task_kind_has_a_label_in_every_locale() -> None:
    kinds = _kinds()
    assert len(kinds) >= 25, f"只找到 {len(kinds)} 種作業類型：搜尋方式失效，守門形同虛設"
    for loc in ("zh-TW", "en-US", "ja-JP"):
        labels = json.loads((ROOT / "frontend" / "src" / "i18n" / f"{loc}.json").read_text(encoding="utf-8"))
        have = labels["tasks"].get("kinds", {})
        missing = sorted(k for k in kinds if not have.get(k.replace(".", "_")))
        assert not missing, f"{loc} 的 tasks.kinds 少了這些作業類型的名稱：{missing}"


def test_no_stale_labels() -> None:
    """名稱清單裡有、程式裡已經沒有的類型要拿掉（過期的項目會讓人以為還有這種作業）。"""
    kinds = {k.replace(".", "_") for k in _kinds()}
    labels = json.loads((ROOT / "frontend" / "src" / "i18n" / "zh-TW.json").read_text(encoding="utf-8"))
    stale = sorted(set(labels["tasks"].get("kinds", {})) - kinds)
    assert not stale, f"tasks.kinds 有程式裡已經沒有的類型：{stale}"
