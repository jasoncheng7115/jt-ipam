"""升級要把相依套件一起升到最新（11-25 發版前 pip-audit 發現）。

以前升級只跑 `pip install -e .`：已經裝著、版本又滿足下限的套件一律不動。全新安裝拿到的是最新版
（CI 的 pip-audit 也是這樣解析，所以永遠是綠的），但升級上來的站台一直停在舊版 —— 正式站台的
Authlib 1.7.2、dnspython 2.8.0、pyasn1 0.6.4 都有已公開的弱點，CI 看不到。
"""
from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "jt-ipam.sh"


def test_upgrade_installs_dependencies_eagerly() -> None:
    text = SCRIPT.read_text()
    start = text.index("# -- 4. backend dependencies --")
    block = text[start:start + 1500]
    installs = [ln for ln in block.splitlines() if re.search(r"\.venv/bin/pip install .*-e \.", ln)]
    assert installs, "找不到升級時安裝後端相依套件的那一行"
    for ln in installs:
        assert "--upgrade" in ln and "--upgrade-strategy eager" in ln, ln
