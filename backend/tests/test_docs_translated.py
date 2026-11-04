"""文件站每段文字都要有三種語言（2026-11-04 使用者：英文版整合導入頁出現兩個中文標題）。

文件站用 <span class="zh|en|ja"> 依語言切換。沒包在語言區塊裡、又含中文或日文的文字，三種語言都會顯示 ——
當時抓到：整合導入頁三張卡片的標題、講座標題、API 手冊英文段落裡的中文角色名，以及排錯頁 10 幾個只寫了
日文的程式碼註解。語言選單本身（「繁體中文」「日本語」）例外。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
CJK = re.compile(r"[぀-ヿ一-鿿]")
VOID = {"br", "img", "input", "meta", "link", "hr", "source", "wbr", "col", "area"}


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, list[str], str, str]] = []
        self.hits: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID:
            return
        a = dict(attrs)
        self.stack.append((tag, (a.get("class") or "").split(), a.get("id") or "", a.get("role") or ""))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if not CJK.search(data):
            return
        if any(t in ("script", "style", "title", "head") for t, _, _, _ in self.stack):
            return
        if any("zh" in c or "ja" in c for _, c, _, _ in self.stack):
            return
        if any(i in ("langMenu", "langCur") or r == "option" for _, _, i, r in self.stack):
            return
        self.hits.append((self.getpos()[0], data.strip()[:60]))


def test_every_docs_text_is_in_a_language_block() -> None:
    bad = []
    for f in sorted(DOCS.glob("*.html")):
        s = _Scan()
        s.feed(f.read_text(encoding="utf-8"))
        bad += [f"{f.name}:{ln} {t}" for ln, t in s.hits]
    assert not bad, "沒有包在 zh / en / ja 區塊裡的中日文（三種語言都會顯示）：\n" + "\n".join(bad)
