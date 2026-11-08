"""輸入項底下的說明文字要貼著那一項（使用者 2026-11-07：「說明文字要跟它近一點，而不是跟它的下一項一樣遠」）。

naive 的 n-form-item 底下預設留一塊放驗證訊息的空白；說明放在 </n-form-item> 後面，就隔著那塊空白，
離輸入框跟下一項一樣遠。以前各頁用負的上邊距去抵（-12px、-8px），只抵掉一半。
規則：後面緊接說明（class 含 hint）的 n-form-item 要 `:show-feedback="false"`（或依條件），說明自己留 4px 上邊距。
有掛驗證規則的輸入項不可以這樣做（錯誤訊息會不見）—— 那種請把說明放進 #feedback 或輸入框內。
"""
from __future__ import annotations

import re
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "frontend" / "src"
PAT = re.compile(r"</n-form-item>\s*\n\s*<(?:div|p)\b[^>]*class=\"[^\"]*hint[^\"]*\"")
OPEN = re.compile(r"<n-form-item\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.S)


def test_hint_after_a_form_item_sits_next_to_it() -> None:
    bad: list[str] = []
    for p in sorted(FE.rglob("*.vue")):
        src = p.read_text(encoding="utf-8")
        for m in PAT.finditer(src):
            start = src.rfind("<n-form-item", 0, m.start())
            tag = OPEN.match(src, start)
            attrs = tag.group(1) if tag else ""
            if "show-feedback" not in attrs:
                bad.append(f"{p.relative_to(FE)}:{src[:start].count(chr(10)) + 1}")
            elif re.search(r":(?:rule|rules)=|\bpath=", attrs):
                bad.append(f"{p.relative_to(FE)}:{src[:start].count(chr(10)) + 1}（有驗證規則，不能拿掉訊息空白）")
    assert not bad, "說明文字離輸入框太遠（前一個 n-form-item 加 :show-feedback=\"false\"）：\n" + "\n".join(bad)
