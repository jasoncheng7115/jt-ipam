"""有文字的按鈕都要有圖示（使用者 2026-11-06：「有些視窗按鈕少 icon，電力、佈線等你都檢查一下」）。

一查全站 134 個：多半是對話框底下的「取消／儲存／確認」，還有重新連線、清除選取、複製、產生金鑰…。
規則見 feedback_buttons_icons_colors。這支測試掃每個 .vue 的 <n-button>，有文字就要有圖示
（`#icon` 插槽、`<n-icon>`、render-icon）。不算的：
- 按鈕本身就是符號（A− A+、↑ ↓、×、＋ －）
- 長得像連結的文字按鈕（`text` 屬性）
- 一組切換鈕或選項晶片（下面 EXEMPT 逐項寫理由）
"""
from __future__ import annotations

import re
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "frontend" / "src"
TAG = re.compile(r"<n-button\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.S)
ICON = re.compile(r"#icon|<n-icon|render-icon|<[A-Z][A-Za-z]*Icon\b")
TEXT_ATTR = re.compile(r"(?:^|\s)text(?:\s|$|=)")

#: (檔名, 按鈕文字樣式) → 理由
EXEMPT: dict[tuple[str, str], str] = {
    ("RackDiagram.vue", r"face_front|face_rear"): "正面 / 背面切換鈕組",
    ("RackRoomToolbar.vue", r"face_front|face_rear"): "正面 / 背面切換鈕組",
    ("Racks.vue", r"pre\.label"): "層架尺寸預設值的選項晶片",
    ("Racks.vue", r"pinnedRoom"): "★／☆ 本身就是圖示",
    ("SubnetDetail.vue", r"stale\.n_days|stale\.never"): "失聯天數的選項晶片",
    ("RackFloorPlan.vue", r"r\.name"): "平面圖上的機櫃本身",
    ("OpenStackPortsCard.vue", r"p\.vm_name"): "虛擬機名稱連結",
    ("InvestigateModal.vue", r"\.\{\{ f \}\}"): "副檔名篩選晶片",
    ("BmcScreen.vue", r"A[−+]"): "字級 A− / A+",
    ("NoVncScreen.vue", r"A[−+]"): "字級 A− / A+",
    ("SshTerminal.vue", r"A[−+]"): "字級 A− / A+",
}


def _problems() -> list[str]:
    out: list[str] = []
    for p in sorted(FE.rglob("*.vue")):
        src = p.read_text(encoding="utf-8")
        m = re.search(r"^<template>\n(.*)^</template>", src, re.S | re.M)
        if not m:
            continue
        body = m.group(1)
        for t in TAG.finditer(body):
            attrs = t.group(1)
            if attrs.rstrip().endswith("/") or TEXT_ATTR.search(attrs):
                continue
            end = body.find("</n-button>", t.end())
            inner = body[t.end():end]
            if ICON.search(inner + attrs):
                continue
            text = re.sub(r"<[^>]+>", "", re.sub(r"<!--.*?-->", "", inner, flags=re.S)).strip()
            if not text or not re.search(r"[A-Za-z一-鿿]", text):
                continue                                  # 沒有文字，或純符號
            if any(f == p.name and re.search(rx, text) for (f, rx) in EXEMPT):
                continue
            line = src[:m.start(1) + t.start()].count("\n") + 1
            out.append(f"{p.relative_to(FE)}:{line} {' '.join(text.split())[:60]}")
    return out


def test_buttons_with_text_have_an_icon() -> None:
    bad = _problems()
    assert not bad, "按鈕缺圖示（加 <template #icon><n-icon><XxxIcon /></n-icon></template>）：\n" + "\n".join(bad)
