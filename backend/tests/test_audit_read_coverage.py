"""守門：「讀了就等於拿走」的端點也要留稽核。

改資料的端點有 test_audit_coverage 守著；這裡守另外兩種：
1. **下載檔案**（Content-Disposition／FileResponse）：子網路 CSV、系統匯出、報告 PDF……
   資料被存成檔案帶走，事後要查得到是誰、什麼時候、帶走多少。
2. **檢視機密明文**（金鑰、權杖）：看到代理金鑰的人可以冒充那台代理（憑證代理能領走私鑰）。

2026-10-09 合規核對時，子網路 CSV 匯出、憑證代理金鑰、對外 MCP 金鑰、系統匯出檔下載都沒有稽核。
新增例外要寫進 EXEMPT 並附理由。偵測方式與 test_audit_coverage 相同（看穿一層同檔案 helper）。
"""

from __future__ import annotations

import ast
import pathlib
import re

ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
_DOWNLOAD = re.compile(r"Content-Disposition|FileResponse\(|attachment;")
#: 路徑最後一段是這些 → 回的是機密明文
_SECRET_PATH = re.compile(r"/(?:[\w-]*-)?(?:key|token|secret|password|reveal)(?:/reveal)?$")
_AUDIT = re.compile(r"\w*audit\w*\(", re.IGNORECASE)

EXEMPT: dict[str, str] = {
    "locations.py::get_floorplan": "地點的平面圖圖片，畫面顯示用（同一張圖在地點頁本來就看得到）",
    "k8s.py::readonly_manifest": "固定的範本（唯讀 ServiceAccount 的 YAML），沒有站台資料也沒有機密；原始碼裡就有同一份",
}


def _endpoints():
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "api"
    for path in sorted(root.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
        for node in funcs.values():
            route = None
            for d in node.decorator_list:
                if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                        and d.func.attr in ROUTE_METHODS and d.args
                        and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str)):
                    route = d.args[0].value
            if route is None:
                continue
            body = ast.get_source_segment(src, node) or ""
            # 看穿一層同檔案 helper（例如憑證匯出走 _export_version_file）
            called = {c.func.id for c in ast.walk(node)
                      if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in funcs}
            text = body + "".join(ast.get_source_segment(src, funcs[n]) or "" for n in called)
            yield f"{path.name}::{node.name}", route, text


def _needs_audit(route: str, text: str) -> str | None:
    if _DOWNLOAD.search(text):
        return "download"
    if _SECRET_PATH.search(route):
        return "secret"
    return None


def test_downloads_and_secret_views_are_audited() -> None:
    checked, missing = 0, []
    for key, route, text in _endpoints():
        kind = _needs_audit(route, text)
        if kind is None:
            continue
        checked += 1
        if key in EXEMPT:
            continue
        if not _AUDIT.search(text):
            missing.append(f"{key}（{kind}：{route}）")
    assert checked >= 8, f"只找到 {checked} 個下載／檢視機密的端點，偵測方式可能失效了"
    assert not missing, f"這些端點讓資料或機密離開系統，卻沒有留稽核：{missing}"


def test_exempt_entries_still_exist() -> None:
    keys = {k for k, _r, _t in _endpoints()}
    stale = sorted(set(EXEMPT) - keys)
    assert not stale, f"EXEMPT 裡的端點已經不存在，請移除：{stale}"


def test_export_event_endpoint_records_an_audit() -> None:
    """表格／報告在瀏覽器裡產生，靠前端回報；回報端點本身要寫稽核。"""
    src = (pathlib.Path(__file__).resolve().parents[1] / "app/api/v1/endpoints/export_events.py").read_text(
        encoding="utf-8")
    assert "append_audit(" in src and "check_rate_limit(" in src
