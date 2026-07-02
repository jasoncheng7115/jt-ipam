"""掃描代理 _derive_os：banner/服務資訊優先於 -O 指紋；裝置型號的積極猜測要留白。

agent 是 repo 根目錄的獨立 stdlib 腳本，用 importlib 從路徑載入來測。
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_AGENT = pathlib.Path(__file__).resolve().parents[2] / "agent" / "jt_ipam_agent.py"


@pytest.fixture(scope="module")
def agent():
    if not _AGENT.exists():
        pytest.skip(f"agent not found at {_AGENT}")
    spec = importlib.util.spec_from_file_location("jt_ipam_agent_under_test", _AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(("text", "expected"), [
    # SSH banner 發行版優先於「HP P2000 NAS」的積極猜測
    ("22/tcp open ssh OpenSSH 10.0p2 Debian 7\n"
     "Aggressive OS guesses: HP P2000 G3 NAS device (93%)\n"
     "Service Info: OS: Linux", "Debian"),
    # smb-os-discovery 給到精確 Windows 版本，勝過「Windows XP SP3」猜測
    ("445/tcp open microsoft-ds\n"
     "| smb-os-discovery:\n"
     "|   OS: Windows 10 Pro 19045 (Windows 10 Pro 6.3)\n"
     "Aggressive OS guesses: Microsoft Windows XP SP3 (89%)", "Windows 10 Pro 19045"),
    # 只有裝置型號的積極猜測（OpenWrt/router）→ 留白，不給錯型號
    ("443/tcp open ssl/http GoAhead WebServer\n"
     "Aggressive OS guesses: OpenWrt Kamikaze 7.09 (Linux 2.6.22) (94%)", None),
    # 純通用 OS 的積極猜測 → 保留
    ("Aggressive OS guesses: Linux 3.10 - 4.11 (95%), Linux 3.2 (92%)", "Linux 3.10 - 4.11 (95%)"),
    # Service Info OS（無 banner/精確匹配）
    ("445/tcp open microsoft-ds\nService Info: OS: Windows", "Windows"),
    # 什麼都沒有 → None
    ("80/tcp open http nginx", None),
])
def test_derive_os(agent, text, expected):
    assert agent._derive_os(text) == expected
