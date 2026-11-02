"""日期選擇器面板右下的「清除 / 現在 / 確定」也要有圖示（使用者 2026-11-02：按鈕都要有圖示）。

naive-ui 預設的這幾顆按鈕只有文字；components/JtDatePicker.vue 用插槽補上圖示。
全站的日期欄位都要用它，直接寫 <n-date-picker> 或 h(NDatePicker) 就又是沒有圖示的按鈕。
"""
from __future__ import annotations

import re
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "frontend" / "src"
WRAPPER = FE / "components" / "JtDatePicker.vue"


def test_date_pickers_go_through_the_wrapper_with_icons() -> None:
    raw = []
    for p in FE.rglob("*.vue"):
        if p == WRAPPER:
            continue
        text = p.read_text(encoding="utf-8")
        if re.search(r"<n-date-picker\b|<NDatePicker\b|h\(NDatePicker\b", text):
            raw.append(str(p.relative_to(FE)))
    assert not raw, f"請改用 <jt-date-picker>（右下按鈕才有圖示）：{raw}"
    w = WRAPPER.read_text(encoding="utf-8")
    for slot in ("#clear", "#now", "#confirm"):
        block = w.split(slot, 1)[1].split("</template>", 2)
        assert "#icon" in block[0] + block[1], f"{slot} 按鈕少了圖示"
