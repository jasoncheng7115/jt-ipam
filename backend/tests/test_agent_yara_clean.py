"""發佈的代理腳本本身不可以含「下載後直接執行」的字串組合（使用者 2026-11-06 的 Wazuh 告警）。

開發機上的 YARA（signature-base 的 SUSP_Command_Line_Combos_Feb24_2）把放在 /tmp 的
jt-ipam-cert-agent-installer.ps1 判成可疑：命中的只是開頭說明註解裡那一行用法範例
（`iwr -UseBasicParsing … | iex`）。規則只看字串、不管是不是註解；客戶的 EDR 用同一套規則時，
在 git clone 或下載下來的檔案上也會這樣判。畫面上給使用者貼的安裝指令是另一回事（使用者自己執行），
這裡只守「發佈出去的檔案」。
"""
from __future__ import annotations

import re
from pathlib import Path

AGENT = Path(__file__).resolve().parents[2] / "agent"

# signature-base Feb24_2 實際命中的字串（2026-11-06 用開發機的 yr 對 agent/ 全部掃過：只有 PowerShell 那支中，
# 命中 $sa1「| iex」與 $sa2「iwr -UseBasicParsing」；.sh 裡的 curl 用法說明不會中）
_COMBOS = (
    re.compile(r"\|\s*iex\b", re.I),
    re.compile(r"\biwr\s+-UseBasicParsing\b", re.I),
)


def test_agent_files_do_not_carry_download_and_run_combos() -> None:
    files = [p for p in AGENT.iterdir() if p.suffix == ".ps1"]
    assert files, "找不到代理腳本 —— 路徑錯了"
    hits = [f"{p.name}: {m.group(0)!r}" for p in files for rx in _COMBOS
            for m in [rx.search(p.read_text(encoding="utf-8", errors="replace"))] if m]
    assert not hits, "代理腳本含會被 YARA 判可疑的字串組合（說明文字請改寫）：\n" + "\n".join(hits)
