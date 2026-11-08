"""推送步驟明細的每個步驟都要有三語名稱（2026-11-03：precheck、import_ca、staged、revert_candidate、
rollback_verify 曾經直接顯示英文的內部名稱）。"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _steps() -> set[str]:
    src = "".join((ROOT / "backend/app/services/cert_push" / f).read_text() for f in ("engine.py", "drivers.py"))
    # 步驟名稱出現的寫法：steps.ok/fail("x")、self._ok("x")、PushError("x")、_xml("x")、patch("x")、step="x"
    return set(re.findall(r'(?:steps\.(?:ok|fail|warn)|_ok|PushError|_xml|patch)\(\s*"([a-z_]+)"', src)) | \
        set(re.findall(r'\bstep(?::\s*str)?\s*=\s*"([a-z_]+)"', src))


def test_every_push_step_has_a_label_in_every_language() -> None:
    steps = _steps()
    assert {"verify", "rollback", "rollback_verify", "precheck", "staged"} <= steps
    for lang in ("zh-TW", "en-US", "ja-JP"):
        labels = json.loads((ROOT / f"frontend/src/i18n/{lang}.json").read_text())["certpush"]
        missing = sorted(s for s in steps if f"step_{s}" not in labels)
        assert not missing, f"{lang} 少了步驟名稱：{missing}"
