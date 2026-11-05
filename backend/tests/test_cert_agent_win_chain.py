"""Windows 派送代理把 PFX 裡的整條憑證鏈放進正確的存放區（客戶 2026-11-05：p12 內含根、中繼、伺服器憑證，
在 Windows Server 裝完還要逐個歸類）。

以前 `Import-JtCertificate` 用 `X509Certificate2(bytes)` 只取出帶私鑰的那張，中繼與根憑證留在 PFX 裡沒裝 ——
IIS 可能不送中繼憑證，用戶端看到不完整的憑證鏈。

守的是（PowerShell 在 Linux 的測試環境跑不了，這裡檢查結構；實際行為在 Windows 實機驗）：
- 匯入整組憑證；帶私鑰的伺服器憑證放進指定的存放區（預設 My）
- 中繼憑證（CA、非自簽）一律放進 LocalMachine\\CA：只用來補齊憑證鏈，不會讓主機多信任任何東西
- 根憑證（自簽 CA）要部署設定 `DEPLOY_<N>_INSTALL_ROOT=true` 才放進 LocalMachine\\Root：加信任根是重大的信任變更，
  預設自動做的話，伺服器被入侵就能把根憑證推到所有 Windows 主機
- 不是 CA 的憑證不碰；已經在存放區的不重複加入
- 每個用到 PFX 的部署都把 INSTALL_ROOT 傳下去；同步結果附上每張放到哪裡
"""
from __future__ import annotations

import re

from tests.test_cert_agent_state_key import AGENT_PS1


def _fn(src: str, name: str) -> str:
    a = src.index(f"function {name}")
    b = src.index("\n}\n", a)
    return src[a:b]


def test_import_takes_the_whole_pfx_and_places_the_chain() -> None:
    src = AGENT_PS1.read_text()
    imp = _fn(src, "Import-JtCertificate")
    assert "X509Certificate2Collection" in imp and ".Import(" in imp, "要匯入整組憑證，不是只取一張"
    assert "HasPrivateKey" in imp, "伺服器憑證是帶私鑰的那張"
    assert "Install-JtChain" in imp
    chain = _fn(src, "Install-JtChain")
    assert '"CA"' in chain and '"Root"' in chain
    assert "CertificateAuthority" in chain, "只放 CA 憑證"
    assert "FindByThumbprint" in chain, "已經存在的不重複加入"
    assert re.search(r"if \(\$selfSigned -and -not \$InstallRoot\)", chain), "根憑證預設不裝"


def test_every_pfx_deployment_passes_install_root() -> None:
    src = AGENT_PS1.read_text()
    calls = re.findall(r"= Import-JtCertificate [^\n]+", src)
    assert len(calls) >= 4
    for c in calls:
        assert "-InstallRoot (Test-CfgTrue (Get-Cfg $D \"INSTALL_ROOT\" \"false\"))" in c, c


def test_report_says_where_the_chain_went() -> None:
    src = AGENT_PS1.read_text()
    assert "$script:JtChainNote" in _fn(src, "Add-Report")
    assert "INSTALL_ROOT" in src[:src.index("param(")], "設定說明要寫這個選項"


def test_chain_is_verified_after_import_and_reported() -> None:
    """部署後在主機上建一次憑證鏈（Windows 與 IIS 用的同一套存放區），完整與否寫進同步結果。"""
    src = AGENT_PS1.read_text()
    assert "Test-JtChain -Leaf $leaf" in _fn(src, "Import-JtCertificate")
    chk = _fn(src, "Test-JtChain")
    assert "X509Chain]::new($true)" in chk, "用本機電腦的存放區建鏈"
    assert "NoCheck" in chk, "不查撤銷（主機可能沒網路）"
    for flag in ("PartialChain", "UntrustedRoot", "NotTimeValid"):
        assert flag in chk
    assert "INSTALL_ROOT" in chk, "私有 CA 的根不受信任時，提示怎麼開"


def test_a_bundled_root_that_was_not_installed_is_named_as_such() -> None:
    """根憑證沒裝時 Windows 回報的是「鏈不完整」，不是「根不受信任」：要說清楚是根沒裝（實機 2026-11-05 看到的誤報）。"""
    chk = _fn(AGENT_PS1.read_text(), "Test-JtChain")
    assert "came with the certificate but is not installed" in chk
    assert "$c.Subject -eq $issuer -and $c.Subject -eq $c.Issuer" in chk
