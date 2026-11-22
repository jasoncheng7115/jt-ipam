"""一個位址有多筆 A 記錄時，DNS 這個來源該回報哪一個名字。

實機（客戶問到）：一台反向代理有 **26 筆 A 記錄**指向它，系統挑了字母序最小的
`chai.example.com`。挑法是穩定的（不會每次同步跳來跳去），但那個名字只是眾多租戶
服務中的一個 —— 它不是那台機器的名字。結果：

* 主機名稱來源顯示「各來源回報的主機名稱不一致（3 種）」，其實不是資料有問題，
  是我們硬從一堆服務名裡挑了一個當「這台機器的名字」。
* 其他來源（manual／proxmox／wazuh／librenms）都說它叫 proxy-01。

規則改成：
1. 有名字與其他來源說法相符 → 用那一個（有佐證的優先）
2. 名字太多（反向代理／共用主機的樣態）→ **不回報**，而不是硬挑一個
3. 其餘 → 字母序最小（維持穩定，不跳動）
"""
from __future__ import annotations

from app.services.dns_sync import pick_dns_hostname


def test_a_corroborated_name_wins():
    """DNS 裡就有其他來源在用的那個名字時，那顯然才是這台機器的名字。"""
    names = {"chai.example.com", "proxy-01.example.com", "wb.example.com"}
    assert pick_dns_hostname(names, others={"proxy-01"}) == "proxy-01.example.com"


def test_corroboration_matches_on_the_first_label():
    """其他來源常常只給短名（proxy-01），DNS 給的是 FQDN。"""
    assert pick_dns_hostname({"proxy-01.example.com"}, others={"proxy-01"}) \
        == "proxy-01.example.com"
    assert pick_dns_hostname({"proxy-01.example.com"},
                             others={"proxy-01.example.com"}) == "proxy-01.example.com"


def test_many_unrelated_names_report_nothing():
    """反向代理：26 個服務名指向同一個位址，沒有一個是「那台機器」的名字。

    硬挑一個等於製造一個假的矛盾 —— 寧可不報。
    """
    names = {f"svc{i}.example.com" for i in range(26)}
    assert pick_dns_hostname(names, others={"proxy-01"}) is None


def test_a_couple_of_names_still_picks_the_stable_one():
    """兩三個名字（主機名 + 別名）挑字母序最小的，維持原本的穩定行為。"""
    names = {"beta.example.com", "alpha.example.com"}
    assert pick_dns_hostname(names, others=set()) == "alpha.example.com"


def test_a_single_name_is_used_as_is():
    assert pick_dns_hostname({"only.example.com"}, others=set()) == "only.example.com"


def test_no_names_reports_nothing():
    assert pick_dns_hostname(set(), others={"x"}) is None


def test_the_choice_is_stable_across_calls():
    """穩定是原本這個設計的重點 —— 改法不能把它弄丟（否則異動記錄會被洗版）。"""
    names = {"b.example.com", "a.example.com", "c.example.com"}
    assert pick_dns_hostname(names, others=set()) == pick_dns_hostname(names, others=set())
