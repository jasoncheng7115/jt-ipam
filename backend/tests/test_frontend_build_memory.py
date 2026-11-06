"""前端正式建置不跑型別檢查（2026-11-06 部署時 prod 建置失敗）。

`npm run build` 原本是 `vue-tsc --noEmit && vite build`。8 GB 的主機上 Node 的預設堆積上限約 2 GB，
vue-tsc 隨專案變大超過上限而中止：prod 的前端沒更新，客戶升級時（scripts/jt-ipam.sh 跑同一個 build）
也會整個失敗。型別檢查是開發與 CI 的事（CI 另外跑 `vue-tsc --noEmit`），客戶主機只需要打包。
vite build 實測峰值約 1.9 GB，所以把上限調到 4 GB 留餘裕。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_production_build_skips_the_type_check_and_raises_the_heap() -> None:
    scripts = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert "vue-tsc" not in scripts["build"], "正式建置不要跑 vue-tsc（記憶體），型別檢查用 typecheck / CI"
    assert re.search(r"--max-old-space-size=\d{4,}", scripts["build"])
    assert "vue-tsc" in scripts["typecheck"]


def test_ci_still_type_checks() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "vue-tsc --noEmit" in ci
    assert "npm run typecheck" in (ROOT / "scripts" / "ci.sh").read_text(encoding="utf-8")
