"""守門：全新安裝裝了的東西，升級也要顧到（使用者 2026-10-08：「這都應該是你要知道要處理的，不需我提醒」）。

會出事的情境很固定：某個功能需要一個系統套件或一個安裝步驟（字型、指令、systemd 設定），
只加進了 `cmd_install`。新裝的站台一切正常，既有站台升級後功能看起來有、實際上缺東西，
而且多半不報錯，只會給出錯的結果或空白。

所以這裡拿 `scripts/jt-ipam.sh` 的兩條路徑比對：
- 安裝會呼叫的安裝類步驟（ensure_* / install_* / grant_* / apply_* / patch_* / build_*），升級也要呼叫
- 安裝會用 apt 裝的套件，升級也要裝（或確認存在）

真的只該在安裝時做的，寫進下面的清單並附理由；沒有理由的例外跟「忘了」分不出來。
新的執行期系統套件請放進 `ensure_runtime_deps`（安裝與升級共用），就不用動這裡。
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "jt-ipam.sh"

#: 只在安裝時呼叫的步驟 → 理由（升級有對等的作法也寫在這裡）
INSTALL_ONLY_STEPS: dict[str, str] = {
    "apply_nginx_config": "安裝時寫初始設定；升級不覆寫使用者改過的 nginx 設定，改走 patch_nginx_* 逐項補",
    "grant_bind_privileged_port": "systemd drop-in 檔，安裝時依 TLS 模式寫一次，升級後仍在",
    "install_freerdp_apt": "升級走 ensure_freerdp_if_selected（有選 FreeRDP 才補裝）",
    "install_guacd": "升級走 ensure_guacd_current（版本落後才重裝）",
}

#: 只在安裝時用 apt 裝的套件 → 理由
INSTALL_ONLY_PACKAGES: dict[str, str] = {
    "lsb-release": "安裝腳本判斷發行版用的，裝好就一直在",
    "ca-certificates": "基礎環境：加套件來源前就要有",
    "curl": "基礎環境：加套件來源前就要有",
    "gnupg": "基礎環境：匯入套件來源金鑰",
    "sudo": "基礎環境：以服務帳號執行 alembic 等",
    "redis-server": "伺服器元件：安裝時裝一次，升級不重裝資料服務",
    "build-essential": "編譯 Python 套件用：裝好就一直在",
    "libpq-dev": "編譯 Python 套件用：裝好就一直在",
    "pkg-config": "編譯 Python 套件用：裝好就一直在",
    "openssl": "基礎環境：產生自簽憑證",
    "nginx": "伺服器元件：只有 nginx 模式安裝時裝；升級不改 TLS 模式",
    # 發行版沒有合適的 python3.X 時的後備（有的話是 python3.X／-venv／-dev，變數展開抓不到）
    "python3": "Python 執行環境：安裝時建 venv，升級沿用同一個 venv",
    "python3-venv": "Python 執行環境：安裝時建 venv，升級沿用同一個 venv",
    "python3-dev": "Python 執行環境：安裝時建 venv，升級沿用同一個 venv",
}


def _body(name: str) -> str:
    src = SCRIPT.read_text(encoding="utf-8")
    start = src.index(f"\n{name}() {{")
    return src[start:src.index("\n}\n", start)]


def _defined() -> set[str]:
    return set(re.findall(r"^([a-z_][a-z0-9_]*)\(\) \{", SCRIPT.read_text(encoding="utf-8"), re.M))


def _steps(body: str) -> set[str]:
    prefixes = ("ensure_", "install_", "grant_", "apply_", "patch_", "build_")
    return {c for c in re.findall(r"(?<![\w$-])([a-z_][a-z0-9_]*)\b", body)
            if c in _defined() and c.startswith(prefixes)}


_PKG = re.compile(r"^[a-z0-9][a-z0-9.+-]+$")


def _apt_packages(body: str) -> set[str]:
    out: set[str] = set()
    for m in re.finditer(r"apt-get install([^\n]*)", body):
        out |= {t for t in m.group(1).split() if _PKG.match(t)}
    # 陣列寫法：local PKGS=( ... ) 之後 apt-get install "${PKGS[@]}"
    for m in re.finditer(r"\b[A-Z_]*PKGS\+?=\(([^)]*)\)", body):
        out |= {t for t in m.group(1).split() if _PKG.match(t)}
    return out


def test_every_install_step_also_runs_on_upgrade() -> None:
    inst, upg = _steps(_body("cmd_install")), _steps(_body("cmd_upgrade"))
    assert inst, "一個安裝步驟都沒抓到：腳本結構改了，守門形同虛設"
    missing = sorted(inst - upg - set(INSTALL_ONLY_STEPS))
    assert not missing, (
        "這些步驟安裝時會做、升級時不會：既有站台升級後會少東西。"
        "請在 cmd_upgrade 也呼叫，或在 INSTALL_ONLY_STEPS 寫明理由：\n  " + "\n  ".join(missing))


def test_every_package_installed_on_install_is_ensured_on_upgrade() -> None:
    inst, upg = _apt_packages(_body("cmd_install")), _apt_packages(_body("cmd_upgrade"))
    assert inst, "一個 apt 套件都沒抓到：腳本結構改了，守門形同虛設"
    missing = sorted(inst - upg - set(INSTALL_ONLY_PACKAGES))
    assert not missing, (
        "這些套件只有全新安裝會裝，升級的站台不會有。請放進 ensure_runtime_deps（安裝與升級共用），"
        "或在 cmd_upgrade 也裝，或在 INSTALL_ONLY_PACKAGES 寫明理由：\n  " + "\n  ".join(missing))


def test_shared_runtime_deps_run_on_both_paths() -> None:
    for fn in ("cmd_install", "cmd_upgrade"):
        assert "ensure_runtime_deps" in _steps(_body(fn)), f"{fn} 沒有呼叫 ensure_runtime_deps"


def test_exceptions_are_not_stale() -> None:
    """例外清單裡的東西如果兩邊都已經在做、或腳本裡根本沒有，就該拿掉（過期的例外會掩護真正的漏洞）。"""
    inst_steps, upg_steps = _steps(_body("cmd_install")), _steps(_body("cmd_upgrade"))
    stale = sorted(s for s in INSTALL_ONLY_STEPS if s not in inst_steps or s in upg_steps)
    assert not stale, f"INSTALL_ONLY_STEPS 有過期的項目：{stale}"
    inst_pk = _apt_packages(_body("cmd_install"))
    stale_pk = sorted(p for p in INSTALL_ONLY_PACKAGES if p not in inst_pk)
    assert not stale_pk, f"INSTALL_ONLY_PACKAGES 有腳本裡已經沒有的套件：{stale_pk}"


def test_backend_allows_numa_memory_policy_calls() -> None:
    """正式站台 2026-10-08：PDF 報告用的 fpdf2 會載入 numpy，numpy 的 OpenBLAS 一載入就呼叫 mbind；
    服務單元的 SystemCallFilter 擋掉 @resources、預設動作是殺掉程序 → 第一次匯出 PDF 就把 uvicorn
    工作程序靜靜打掉（同一個程序上的其他請求一起斷）。開發機沒有這層沙箱，測試全綠也抓不到。"""
    src = SCRIPT.read_text(encoding="utf-8")
    body = src[src.index("\nensure_backend_syscall_dropin() {"):]
    body = body[:body.index("\n}\n")]
    assert "SystemCallFilter=mbind" in body
    for fn in ("cmd_install", "cmd_upgrade"):
        assert "ensure_backend_syscall_dropin" in _steps(_body(fn)), f"{fn} 沒有寫入 NUMA 系統呼叫的 drop-in"


def test_pip_is_raised_to_a_safe_minimum_on_both_paths() -> None:
    """pip 內嵌自己的 certifi，安裝／升級時從 PyPI 下載就靠它驗證（2026-10 eMudhra/emSign 中間憑證金鑰外洩，
    pip 26.2.2 起不再信任；只更新系統憑證庫擋不住）。升級路徑以前完全不升級 pip，既有站台會一直留著舊版。"""
    import re
    src = SCRIPT.read_text(encoding="utf-8")
    install = src[src.index("# -- 5. backend venv --"):src.index("install_rdp_optional", src.index("# -- 5. backend venv --"))]
    upgrade = src[src.index("cmd_upgrade() {"):]
    # 升級時安裝後端相依套件那一行之前（那行的旗標會變：11-25 加了 --upgrade-strategy eager）
    upgrade = upgrade[:re.search(r"\.venv/bin/pip install [^\n]*-e \.", upgrade).start()]
    for name, block in (("install", install), ("upgrade", upgrade)):
        m = re.search(r'pip install[^\n]*--upgrade[^\n]*"pip>=(\d+)\.(\d+)\.(\d+)"', block)
        assert m, f"{name} 路徑要先把 pip 升到安全的最低版本"
        assert tuple(int(x) for x in m.groups()) >= (26, 2, 2), name
