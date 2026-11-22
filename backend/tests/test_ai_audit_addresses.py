"""AI 巡檢的敘述裡不可以出現查不到出處的位址。

實機（2026-08-30）同一次巡檢就出現兩種：

- `192.16CA.123.59` —— 模型把 `192.168.123.59` 寫壞了（連合法位址都不是）
- `196.168.123.39` —— 合法、但根本不存在（真正的是 `192.168.123.39`）

這種錯誤特別危險：它看起來精確、語氣肯定，讀的人會直接照著那個位址去查，
查不到又會回頭懷疑自己的資料。真正的依據在 `evidence` —— 那是我們自己從資料庫撈的，
畫面上也一直有顯示 —— 所以敘述裡對不上依據的位址一律拿掉。
**寧可少一句話，也不要留一個看起來像事實的錯誤。**
"""

from __future__ import annotations

from app.services.ai_audit import strip_unverifiable_addresses as strip


def test_corrupted_address_is_removed():
    text = "例如 192.16CA.123.59 (mail-test-01) 與 192.168.123.83"
    out, n = strip(text, {"192.168.123.83"})
    assert "192.16CA.123.59" not in out
    assert "192.168.123.83" in out, "對得上依據的位址不可以被誤刪"
    assert n == 1


def test_plausible_but_unlisted_address_is_removed():
    """合法卻不在依據裡 —— 這種最危險，肉眼看不出是編的。"""
    out, n = strip("在 196.168.123.39 上發現問題", {"192.168.123.39"})
    assert "196.168.123.39" not in out
    assert n == 1


def test_cidr_is_kept():
    """網段是在講範圍，不是指認某一台機器，不必出現在依據裡。"""
    out, n = strip("在服務網路（192.168.123.0/24）中發現行動裝置", set())
    assert "192.168.123.0/24" in out
    assert n == 0


def test_listed_addresses_survive_untouched():
    text = "IP 位址 192.168.123.165 的主機名稱為 pc-test-01"
    out, n = strip(text, {"192.168.123.165"})
    assert out == text
    assert n == 0


def test_hostnames_are_not_touched():
    """主機名稱不是位址，不可以被這條規則掃到（`router-01.example.com` 只有三段）。"""
    text = "router-01.example.com 與 mail-test-01 都在同一段"
    out, n = strip(text, set())
    assert out == text
    assert n == 0


def test_leftover_punctuation_is_tidied():
    """拿掉之後不要留下空括號那種明顯的殘骸。"""
    out, _ = strip("設備 (192.16CA.123.59) 需要確認", set())
    assert "()" not in out and "（）" not in out
