"""後端給的每個錯誤代碼，三個語系都要翻得出來。

為什麼要守門：`ui_detail("xxx", "中文")` 少補一個 `errors.xxx` 不會壞掉，只會**安靜地**
退回中文句子 —— 英文與日文的使用者照樣看到中文，而那正是這一整套結構化錯誤要解決的問題。
沒有測試的話，下一個新增代碼的人不會知道自己漏了。

掃的是原始碼而不是執行結果：這些例外多半要有真實的外部服務才觸發得到，
跑不到的分支一樣會出現在使用者面前。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_I18N = _BACKEND.parent / "frontend" / "src" / "i18n"
_LOCALES = ("zh-TW", "en-US", "ja-JP")


def _codes_in_source() -> dict[str, set[str]]:
    """代碼 → 用到它的檔案（出錯時要說得出去哪裡補）。"""
    found: dict[str, set[str]] = {}
    for path in sorted((_BACKEND / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            picked: list[ast.expr] = []
            if name == "ui_detail" and node.args:
                picked.append(node.args[0])
            if name == "detail_of" and len(node.args) > 1:
                picked.append(node.args[1])
            # 裝置群組的服務層例外：GroupError(status, code, message, **params)，端點轉成 ui_detail
            if name == "GroupError" and len(node.args) > 1:
                picked.append(node.args[1])
            picked += [kw.value for kw in node.keywords if kw.arg == "code"]
            for expr in picked:
                # f-string 組出來的代碼（check_size／ws_timeouts）不在這裡檢查，
                # 它們各自有自己的測試把可能的值列出來
                if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
                    found.setdefault(expr.value, set()).add(str(path.relative_to(_BACKEND)))
    return found


def test_every_error_code_is_translated_in_every_locale() -> None:
    codes = _codes_in_source()
    assert len(codes) > 100, "掃不到代碼 —— 掃描邏輯壞了，不是真的沒有代碼"
    missing: list[str] = []
    for locale in _LOCALES:
        table = json.loads((_I18N / f"{locale}.json").read_text(encoding="utf-8"))["errors"]
        for code, files in sorted(codes.items()):
            if code not in table:
                missing.append(f"{locale}: errors.{code}（來自 {', '.join(sorted(files))}）")
    assert not missing, "少了翻譯：\n" + "\n".join(missing)


def test_dynamic_codes_are_translated_too() -> None:
    """用 f-string 組出來的代碼：把所有可能的值明列在這裡，否則掃不到。"""
    from app.core.ws_timeouts import _WAITING_FOR
    from app.services.sftp import _WHAT_ZH

    dynamic = {f"sftp_{what}_{kind}"
               for what in _WHAT_ZH
               for kind in ("size_unknown", "size_invalid", "too_large")}
    dynamic |= {code for code, _zh in _WAITING_FOR.values()}
    dynamic.add("ws_timeout")   # 認不得的 what 用的退路
    # ISOinsight 整合：IsoError 由 spec_code 組成 isoinsight_<小寫>（測試連線畫面與同步記錄都會顯示）
    from app.services.isoinsight.errors import UI_CODES
    dynamic |= set(UI_CODES)

    for locale in _LOCALES:
        table = json.loads((_I18N / f"{locale}.json").read_text(encoding="utf-8"))["errors"]
        assert not (dynamic - set(table)), f"{locale} 少了：{sorted(dynamic - set(table))}"
