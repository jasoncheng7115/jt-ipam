"""Windows 派送代理的 PowerShell：語法、以及不必重裝就改成驗證 jt-ipam 的函式（2026-11-07）。

1.0.20 出貨的 Windows 代理有一行 `"$(Get-Cfg $CFG "CERT_SHA256" "")"`：子運算式裡的 `""` 被當成跳脫的引號，
整支腳本解析失敗、一行都不會執行。以前的測試只用文字比對腳本內容，看不到。這裡用 pwsh 真的解析每一支 .ps1，
並抽出信任相關的函式實際跑（簽章由測試用 Python 簽，跟伺服器同一個格式）。

pwsh 是 PowerShell 7（.NET），代理跑在 Windows PowerShell 5.1：連線的部分（ServicePointManager 回呼）
在 pwsh 不生效，所以這裡只跑不連網的函式；語法兩者相同。GitHub 的 Ubuntu 執行環境內建 pwsh。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.test_cert_agent_auto_pin import signed_bundle
from tests.test_cert_agent_verify import _pair

ROOT = Path(__file__).resolve().parents[2]
AGENT_PS1 = ROOT / "agent" / "jt_ipam_cert_agent.ps1"
PWSH = os.environ.get("JT_PWSH") or shutil.which("pwsh")
NEED_PWSH = pytest.mark.skipif(not PWSH, reason="需要 pwsh（PowerShell 7）")


def _pwsh(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],  # type: ignore[list-item]  # noqa: S603
                          capture_output=True, text=True, timeout=180)


def _ps1_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "*.ps1", "*.psm1"], cwd=ROOT, capture_output=True, text=True,  # noqa: S607
                         check=True).stdout.split()
    return [ROOT / f for f in out]


@NEED_PWSH
@pytest.mark.parametrize("path", _ps1_files(), ids=lambda p: p.name)
def test_every_powershell_script_parses(path: Path) -> None:
    r = _pwsh(f"""$e = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile('{path}', [ref]$null, [ref]$e)
        if ($e) {{ $e | ForEach-Object {{ $_.ToString() }}; exit 1 }}""")
    assert r.returncode == 0, r.stdout + r.stderr


def _functions(*names: str) -> str:
    """從代理腳本抽出函式定義（用 PowerShell 自己的語法樹，不靠文字切）。"""
    want = ",".join(f"'{n}'" for n in names)
    r = _pwsh(f"""$ast = [System.Management.Automation.Language.Parser]::ParseFile('{AGENT_PS1}', [ref]$null, [ref]$null)
        $ast.FindAll({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and @({want}) -contains $n.Name }}, $true) |
            ForEach-Object {{ $_.Extent.Text }}""")
    assert r.returncode == 0, r.stderr
    return r.stdout


def _jtpin_type() -> str:
    """Enable-JtPin 裡編譯的 C# 類別原文。"""
    src = AGENT_PS1.read_text()
    start = src.index('Add-Type -TypeDefinition @"')
    end = src.index('"@', start)
    return src[start:end + 2]


@pytest.fixture
def identity() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


@NEED_PWSH
def test_trust_data_is_parsed_and_its_signature_checked(tmp_path, identity) -> None:
    pem, _, _ = _pair("ipam.example.net")
    import ssl
    der = ssl.PEM_cert_to_DER_cert(pem)
    good = signed_bundle(identity, der)
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    forged = signed_bundle(identity, der, sign_with=stranger)
    (tmp_path / "good.txt").write_text(good)
    (tmp_path / "forged.txt").write_text(forged)
    funcs = _functions("Test-JtRsa", "ConvertFrom-JtTrust")
    r = _pwsh(funcs + f"""
        $out = @{{}}
        foreach ($k in 'good', 'forged') {{
            $f = ConvertFrom-JtTrust ([IO.File]::ReadAllText('{tmp_path}/' + $k + '.txt'))
            $out[$k] = @{{ sha = $f['cert_sha256']; auto = $f['auto_pin'];
                          ok = (Test-JtRsa $f['_body'] $f['_sig'] $f['identity_n'] $f['identity_e']) }}
        }}
        $out['garbage'] = $null -eq (ConvertFrom-JtTrust "hello")
        $out | ConvertTo-Json -Compress""")
    assert r.returncode == 0, r.stdout + r.stderr
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert res["good"] == {"sha": hashlib.sha256(der).hexdigest().upper(), "auto": "1", "ok": True}
    assert res["forged"]["ok"] is False
    assert res["garbage"] is True


@NEED_PWSH
def test_certificate_check_modes(tmp_path) -> None:
    """編譯後的回呼：0 不驗、1 比憑證 SHA-256、2 一般驗證；拒絕時舉旗（之後會試著跟上換證）。"""
    pem, _, _ = _pair("ipam.example.net")
    (tmp_path / "c.pem").write_text(pem)
    import ssl
    sha = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper()
    r = _pwsh(_jtpin_type() + f"""
        $c = [Security.Cryptography.X509Certificates.X509Certificate]::new('{tmp_path}/c.pem')
        $none = [Net.Security.SslPolicyErrors]::None
        $bad = [Net.Security.SslPolicyErrors]::RemoteCertificateChainErrors
        $r = @()
        [JtPin]::Mode = 1; [JtPin]::Sha256 = '{sha}'; $r += [JtPin]::Check($null, $c, $null, $bad)
        $r += [JtPin]::Refused
        [JtPin]::Sha256 = '{"00" * 32}'; $r += [JtPin]::Check($null, $c, $null, $none); $r += [JtPin]::Refused
        [JtPin]::Refused = $false; [JtPin]::Mode = 2
        $r += [JtPin]::Check($null, $c, $null, $none); $r += [JtPin]::Check($null, $c, $null, $bad)
        [JtPin]::Mode = 0; $r += [JtPin]::Check($null, $c, $null, $bad)
        $r -join ','""")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip().splitlines()[-1] == "True,False,False,True,True,False,True"


def test_windows_installer_forgets_what_the_agent_learned() -> None:
    inst = (ROOT / "agent" / "jt-ipam-cert-agent-installer.ps1").read_text()
    assert inst.count('Remove-Item -LiteralPath (Join-Path $DATA_DIR "trust")') == 2


def test_b64_helpers_match_the_server_format() -> None:
    """身分公鑰的 modulus 用最短的大端位元組（RSAParameters 不收前導 0）。"""
    from app.services.agent_trust import _b64_int
    n = (1 << 3071) | 12345
    raw = base64.b64decode(_b64_int(n))
    assert raw[0] != 0 and int.from_bytes(raw, "big") == n


@pytest.mark.parametrize("path", _ps1_files(), ids=lambda p: p.name)
def test_powershell_scripts_are_plain_ascii(path: Path) -> None:
    """Windows PowerShell 5.1 讀沒有 BOM 的腳本時用系統編碼：中文版 Windows 是 Big5（cp950）。

    中文字的 UTF-8 位元組在 Big5 下會跟後面的位元組黏成一個字，連換行一起吃掉，下一行程式碼就變成註解。
    2026-11-08 在繁中 Windows 11 測試機實測：1.0.20 的派送代理有一行中文註解結尾吃掉換行，下一行的 `{` 不見了，
    整支腳本解析失敗；1.0.19 起另一行把 WinRM 錯誤訊息那行吃掉。英文版 Windows 不會出事，所以一直沒發現。
    所以代理與安裝程式的 .ps1 一律只用 ASCII（註解也是）。pwsh 7 預設用 UTF-8，測不出這個問題。
    """
    bad = [i for i, line in enumerate(path.read_bytes().split(b"\n"), 1) if any(b > 127 for b in line)]
    assert not bad, f"{path.name} 第 {bad[:10]} 行有非 ASCII 字元（中文版 Windows 會解析失敗）"
