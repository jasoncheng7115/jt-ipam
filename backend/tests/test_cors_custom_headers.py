"""前後端不同源時，瀏覽器會先發 CORS 預檢；前端送的自訂標頭都要在允許清單裡（2026-11-21）。

IP 變更評估建立計畫時帶 `Idempotency-Key`，沒列在 allow_headers → 預檢 400，瀏覽器根本不送 POST，
畫面只顯示「連線失敗」。正式站台同源（nginx 轉 /api）不發預檢所以沒發現。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

FE = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _frontend_custom_headers() -> set[str]:
    found: set[str] = set()
    for p in FE.rglob("*.ts"):
        if "__tests__" in p.parts:
            continue
        for m in re.finditer(r"headers:\s*\{([^}]*)\}", p.read_text(encoding="utf-8")):
            for h in re.findall(r"[\"']?([A-Z][A-Za-z-]+)[\"']?\s*:", m.group(1)):
                found.add(h)
    return found


def test_frontend_headers_are_listed() -> None:
    hdrs = _frontend_custom_headers()
    assert "Idempotency-Key" in hdrs
    import app.main as main
    src = Path(main.__file__).read_text(encoding="utf-8")
    allowed = set(re.findall(r'"([A-Za-z-]+)"', src.split("allow_headers=", 1)[1].split("]", 1)[0]))
    missing = {h for h in hdrs if h.lower() not in {a.lower() for a in allowed}}
    assert not missing, f"前端會送、CORS 沒允許的標頭：{sorted(missing)}"


@pytest.mark.anyio
async def test_preflight_with_idempotency_key(client) -> None:
    from app.core.config import get_settings
    origin = (get_settings().cors_origins or ["http://127.0.0.1:5173"])[0]
    r = await client.options("/api/v1/change-plans", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type,idempotency-key"})
    assert r.status_code == 200, r.text


def _frontend_read_headers() -> set[str]:
    """前端讀的回應標頭（`headers["content-disposition"]` 這種寫法）。"""
    found: set[str] = set()
    for p in FE.rglob("*.ts"):
        if "__tests__" in p.parts:
            continue
        found |= {h.lower() for h in re.findall(r"headers\[\s*[\"']([A-Za-z-]+)[\"']\s*\](?!\s*=[^=])", p.read_text(encoding="utf-8"))}
    return found


def test_response_headers_the_frontend_reads_are_exposed() -> None:
    """不同源時，回應標頭除了幾個基本的以外，沒列在 expose_headers 瀏覽器就不給讀（2026-11-22 發版 e2e 抓到：
    SBOM 與憑證下載讀不到 Content-Disposition，檔名變成預設值）。"""
    read = _frontend_read_headers()
    assert "content-disposition" in read
    import app.main as main
    src = Path(main.__file__).read_text(encoding="utf-8")
    exposed = {h.lower() for h in re.findall(r'"([A-Za-z-]+)"', src.split("expose_headers=", 1)[1].split("]", 1)[0])}
    safelisted = {"cache-control", "content-language", "content-length", "content-type", "expires", "last-modified", "pragma"}
    missing = sorted(read - exposed - safelisted)
    assert not missing, f"前端會讀、CORS 沒公開的回應標頭：{missing}"
