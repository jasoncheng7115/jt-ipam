"""守門：文件與網站（GitHub Pages 的 docs/*.html、三份 README）跟得上程式（使用者 2026-10-08：
「別忘了文件、站台 pages 等等，該更新的都要一併更新到」「以後這些都要檢查，列入發版前作業跟守門」）。

文件落後的方式很安靜：網站照樣打得開、看起來完整，只是少寫了一個整合、一段只有中文。
這裡擋得住的就自動擋：
1. 網站每一頁的中／英／日段落數一致（新增一段中文卻沒補英、日文）
2. 每個整合（整合選單上的每一項）的產品名稱都出現在功能清單、首頁與三份 README
3. 主要功能出現在首頁與三份 README（新增大功能時把名稱加進 MAJOR_FEATURES）
4. 文件不用全形「／」與破折號「—」（使用者 2026-10-01 的規定）
擋不住的（內容是否還正確、截圖是否過時）列在 TEST_CHECKLIST 5k，發版前逐項看。
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
HTML = sorted(DOCS.glob("*.html"))
READMES = [ROOT / "README.md", ROOT / "README_zh-TW.md", ROOT / "README_ja.md"]

#: 整合選單的鍵 → 文件上一定要出現的產品名稱（新增整合時，test_integration_presence 的 MENU_KEYS 會多一個鍵，
#: 這裡沒有對照就會失敗 → 逼著把名稱寫進來，接著下面的測試就會檢查文件有沒有寫）
PRODUCT_NAMES: dict[str, list[str]] = {
    "dns": ["PowerDNS", "BIND", "Unbound", "Windows DNS", "Univention", "Technitium"],
    "adguard": ["AdGuard"], "librenms": ["LibreNMS"], "opnsense": ["OPNsense"], "pfsense": ["pfSense"],
    "fortigate": ["FortiGate"], "paloalto": ["Palo Alto"], "checkpoint": ["Check Point"], "mikrotik": ["MikroTik"],
    "snmp": ["SNMP"],
    "windows_dhcp": ["Windows DHCP"], "kea_dhcp": ["Kea"], "isc_dhcp": ["ISC DHCP"], "isoinsight": ["ISOinsight"],
    "technitium": ["Technitium"], "rustdesk": ["RustDesk"], "proxmox": ["Proxmox VE"], "esxi": ["ESXi"], "hyperv": ["Hyper-V"], "openstack": ["OpenStack", "CubeCOS"],
    "wazuh": ["Wazuh"], "zabbix": ["Zabbix"], "checkmk": ["Checkmk"], "ocs": ["OCS Inventory"], "graylog": ["Graylog"],
}

#: 主要功能：(中文, 英文, 日文) 名稱，首頁與三份 README 都要寫到
MAJOR_FEATURES: list[tuple[str, str, str]] = [
    ("IP 變更評估", "IP change assessment", "IP 変更の影響評価"),
]


def test_every_menu_integration_has_documented_product_names() -> None:
    from tests.test_integration_presence import MENU_KEYS
    missing = sorted(set(MENU_KEYS) - set(PRODUCT_NAMES))
    assert not missing, f"新整合要在 PRODUCT_NAMES 寫下文件上該出現的產品名稱：{missing}"


@pytest.mark.parametrize("path", [DOCS / "features.html", DOCS / "index.html", *READMES], ids=lambda p: p.name)
def test_integrations_appear_on_the_site_and_readmes(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
    missing = sorted({n for names in PRODUCT_NAMES.values() for n in names if n not in text})
    assert not missing, f"{path.name} 沒有寫到這些整合：{missing}"


@pytest.mark.parametrize("path", [DOCS / "index.html", *READMES], ids=lambda p: p.name)
def test_major_features_appear(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
    for zh, en, ja in MAJOR_FEATURES:
        want = {"README.md": [en], "README_zh-TW.md": [zh], "README_ja.md": [ja]}.get(path.name, [zh, en, ja])
        for w in want:
            assert w in text, f"{path.name} 沒有寫到主要功能「{w}」"


@pytest.mark.parametrize("path", HTML, ids=lambda p: p.name)
def test_site_pages_are_trilingual(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
    zh, en, ja = (len(re.findall(rf'class="{c}"', text)) for c in ("zh", "en", "ja"))
    assert zh == en == ja, f"{path.name} 的三語段落數對不上：zh={zh} en={en} ja={ja}（有段落只寫了其中一種語言）"


@pytest.mark.parametrize("path", [*HTML, *READMES, *sorted(DOCS.glob("DATA_MODEL*.md")), *sorted(DOCS.glob("INSTALL*.md")),
                                  *sorted(DOCS.glob("COMPLIANCE*.md"))],
                         ids=lambda p: p.name)
def test_docs_use_halfwidth_slash_and_no_em_dash(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
    bad = [(i + 1, line.strip()[:80]) for i, line in enumerate(text.splitlines()) if "／" in line or "—" in line]
    assert not bad, f"{path.name} 用了全形「／」或破折號「—」（文件一律半形 /、不用破折號）：{bad[:5]}"


def test_compliance_docs_are_generated_and_linked() -> None:
    """合規對照（使用者 2026-10-09）：三份 .md 與官網頁面由 scripts/gen-compliance-docs.py 從同一份內容產生，
    手改其中一份就會跟其他語言對不上；首頁要連得到。"""
    import subprocess
    import sys
    root = DOCS.parent
    r = subprocess.run([sys.executable, str(root / "scripts" / "gen-compliance-docs.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    index = (DOCS / "index.html").read_text(encoding="utf-8")
    assert 'href="compliance.html"' in index
    for name in ("README.md", "README_zh-TW.md", "README_ja.md"):
        assert "docs/COMPLIANCE" in (root / name).read_text(encoding="utf-8"), f"{name} 沒有連到合規對照"


def test_compliance_evidence_exists() -> None:
    """合規對照每一項的「驗證方式」都要指到真的存在的測試或檔案（使用者 2026-10-09：做不到的不要寫）。

    改名或刪掉測試時，這裡擋下：不然文件會繼續拿一個已經不存在的測試當佐證。"""
    import re
    root = DOCS.parent
    src = (root / "scripts" / "gen-compliance-docs.py").read_text(encoding="utf-8")
    refs = set(re.findall(r"`([^`\s]+\.(?:py|ts|sh|sql|conf|yml|md))`", src))
    assert len(refs) > 30, f"只抓到 {len(refs)} 個佐證，偵測方式可能失效了"
    search_dirs = [root, root / "backend", root / "backend" / "tests", root / "frontend"]
    missing = []
    for ref in sorted(refs):
        cands = [d / ref for d in search_dirs]
        if ref.startswith("app/"):
            cands.append(root / "backend" / ref)
        if not any(c.exists() for c in cands):
            missing.append(ref)
    assert not missing, f"合規對照引用了不存在的佐證：{missing}"



def test_compliance_rows_name_real_controls() -> None:
    """合規頁要寫出對應的條文與控制項（使用者 2026-10-09）。

    每一列都要有對應；編號必須是 ISO/IEC 27001:2022（93 項）／ISO/IEC 42001:2023（38 項）附錄 A 真的有的，
    打錯或寫到不存在的編號（例如 A.8.35、A.6.2.9）會讓導入組織拿去對照時找不到。"""
    import importlib.util
    root = DOCS.parent
    spec = importlib.util.spec_from_file_location("gen_compliance", root / "scripts" / "gen-compliance-docs.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert len(mod.ANNEX_27001) == 93 and len(mod.ANNEX_42001) == 38
    mod._check_refs()                       # 產生時也會跑；這裡讓失敗訊息直接指到原因
    assert "A.8.35" not in mod.ANNEX_27001 and "A.6.2.9" not in mod.ANNEX_42001
    for rows, refs in ((mod.ISO27001, mod.REFS_27001), (mod.ISO42001, mod.REFS_42001)):
        for area, _how, _evid in rows:
            assert refs[area["en"]], f"{area['en']} 沒有對應的控制項"
    zh = (DOCS / "COMPLIANCE_zh-TW.md").read_text(encoding="utf-8")
    assert "A.8.5" in zh and "A.6.2.8" in zh and "6.1.3" in zh


def test_site_pages_have_mobile_menu_and_browser_language() -> None:
    """2026-10-10 使用者回報：手機上整個導覽不見了（小螢幕把連結藏起來卻沒有選單按鈕），
    而且網站不看瀏覽器語言、一律先顯示英文。有導覽列的頁面都要有「選單」按鈕與瀏覽器語言判斷。"""
    pages = [p for p in DOCS.glob("*.html") if 'class="nav-links"' in p.read_text(encoding="utf-8")]
    assert len(pages) >= 8, f"只找到 {len(pages)} 個有導覽列的頁面，偵測方式可能失效了"
    for p in pages:
        src = p.read_text(encoding="utf-8")
        assert "nav-menu-btn" in src and "mnav" in src, f"{p.name} 小螢幕沒有選單按鈕"
        assert "fromBrowser()" in src, f"{p.name} 沒有依瀏覽器語言決定預設語言"
        # 頂列固定一行：放不下時先收 GitHub（nav-c1）、再收語言（nav-c2），不可以換成兩行（使用者 2026-10-10）
        assert "nav-c1" in src and "nav-c2" in src and "nav .nav-in{flex-wrap:nowrap}" in src, f"{p.name} 頂列會換行"
