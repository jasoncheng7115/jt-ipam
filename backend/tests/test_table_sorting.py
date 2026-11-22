"""表格要能點標題排序（使用者 2026-11-22：「有些新頁表格標題缺少排序等功能」）。

機房詳情、機櫃電力卡、失效模擬抽屜這幾張新表格做出來時忘了用 autoSort（全站慣例：
useTableSort 的 autoSort 補預設排序，畫出來的欄位另寫 sorter；後端分頁的表格要後端排序）。
這支守門：有 <n-data-table> 的元件一定要有排序。加守門時既有的 26 個檔案當天一起補完（後端分頁的 IP 變更評估清單、
服務、機房裝置改成後端排序），NOT_YET 是空的，不要往裡面加。
"""
from __future__ import annotations

import re
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "frontend" / "src"

#: 加守門時（2026-11-22）就沒有排序的檔案；只能減少
NOT_YET: set[str] = set()   # 2026-11-22 加守門當天就全部補完


def _tables() -> dict[str, str]:
    return {str(p.relative_to(FE)): p.read_text(encoding="utf-8") for p in FE.rglob("*.vue")
            if "<n-data-table" in p.read_text(encoding="utf-8")}


def test_every_table_can_be_sorted() -> None:
    missing = sorted(f for f, src in _tables().items()
                     if f not in NOT_YET and not re.search(r"\bautoSort\b|\bsorter\b", src))
    assert not missing, f"這些表格沒有排序（用 autoSort 或自己寫 sorter；後端分頁要後端排序）：{missing}"


def test_the_exception_list_only_shrinks() -> None:
    tables = _tables()
    fixed = sorted(f for f in NOT_YET if f in tables and re.search(r"\bautoSort\b|\bsorter\b", tables[f]))
    gone = sorted(f for f in NOT_YET if f not in tables)
    assert not fixed and not gone, f"已經有排序或已經沒有表格，從 NOT_YET 拿掉：{fixed + gone}"
