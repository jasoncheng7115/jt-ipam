"""派送代理確認連到的是真的 jt-ipam（2026-11-04 使用者問「有人偽造自己是 jt-ipam 憑證 server」）。

舊的安裝指令一律關掉 TLS 驗證（curl -k / JT_IPAM_INSECURE）：能攔截網路的人可以假冒 jt-ipam，拿走代理金鑰、
推一支「新版代理」讓主機以 root / SYSTEM 執行（自我更新只比對同一台伺服器給的 sha256）。

守的是：
1. 釘選：Linux 代理帶 `--pinnedpubkey`，指紋對的伺服器連得上、別台（假冒的）在送出任何東西前就被拒
2. 自我更新只裝 jt-ipam 簽章金鑰簽過的版本；簽章不對或沒有 openssl 就不換
3. 出貨的兩支代理都有正確的簽章（改了代理沒重簽，這裡會擋）
4. 伺服器送出代理的位元組原封不動（驗的是這份的簽章）、記下代理回報的驗證方式、提供本機憑證指紋
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import re
import shutil
import ssl
import subprocess
import textwrap
import threading
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tests.test_cert_agent_state_key import (
    AGENT_PS1,
    AGENT_SH,
    INSTALLER_PS1,
    INSTALLER_SH,
    _extract_bash_function,
)
from tests.test_cert_agent_verify import _pair

pytestmark = pytest.mark.anyio
NEED_TOOLS = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("openssl") is None
                                or shutil.which("curl") is None, reason="需要 bash、openssl、curl")


def _pubkey_pem() -> bytes:
    m = re.search(r"AGENT_SIGNING_PUBKEY='(-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----)'",
                  AGENT_SH.read_text(), re.S)
    assert m, "agent.sh 要內建簽章公鑰"
    return m.group(1).encode()


def test_shipped_agents_are_signed_with_the_embedded_key() -> None:
    pub = serialization.load_pem_public_key(_pubkey_pem())
    for f in (AGENT_SH, AGENT_PS1):
        sig = base64.b64decode(Path(f"{f}.sig").read_text().strip())
        pub.verify(sig, f.read_bytes(), padding.PKCS1v15(), hashes.SHA256())   # 不對就丟例外
    # Windows 內建的是同一把（modulus / exponent）
    ps = AGENT_PS1.read_text()
    mod = re.search(r'\$AGENT_SIGNING_MODULUS = "([^"]+)"', ps).group(1)
    exp = re.search(r'\$AGENT_SIGNING_EXPONENT = "([^"]+)"', ps).group(1)
    nums = pub.public_numbers()
    assert int.from_bytes(base64.b64decode(mod), "big") == nums.n
    assert int.from_bytes(base64.b64decode(exp), "big") == nums.e


class _Https:
    def __init__(self, tmp: Path) -> None:
        pem, key, _ = _pair("ipam.example.net")
        (tmp / "srv.pem").write_text(pem)
        (tmp / "srv.key").write_text(key)
        self.der = ssl.PEM_cert_to_DER_cert(pem)

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                # 要帶長度：沒有的話回應靠斷線結束，OpenSSL 3.5（Ubuntu 26.04）的 curl 會當成 unexpected eof（rc=56）
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):  # type: ignore[no-untyped-def]
                pass
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(tmp / "srv.pem", tmp / "srv.key")
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def pin(self) -> str:
        from cryptography import x509
        spki = x509.load_der_x509_certificate(self.der).public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        return "sha256//" + base64.b64encode(hashlib.sha256(spki).digest()).decode()

    def close(self) -> None:
        self.httpd.shutdown()


def _bash(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)  # noqa: S603, S607


@NEED_TOOLS
def test_pinned_agent_talks_only_to_the_pinned_server(tmp_path) -> None:
    srv = _Https(tmp_path)
    other_pin = "sha256//" + base64.b64encode(hashlib.sha256(b"someone else").digest()).decode()
    fn = _extract_bash_function(AGENT_SH.read_text(), "build_curl")
    try:
        def run(pin: str, verify: str = "true") -> subprocess.CompletedProcess[str]:
            return _bash(textwrap.dedent(f"""
                AGENT_KEY=k AGENT_VERSION=t VERIFY_TLS={verify} PINNED_PUBKEY='{pin}'
                {fn}
                build_curl; echo "mode=$TLS_MODE"
                "${{CURL[@]}}" https://127.0.0.1:{srv.port}/; echo " rc=$?"
            """))
        good = run(srv.pin())
        assert "mode=pinned" in good.stdout and "ok rc=0" in good.stdout, good.stdout + good.stderr
        bad = run(other_pin)
        assert "rc=0" not in bad.stdout, "指紋不同的伺服器（假冒的）要連不上"
        assert "mode=ca" in run("").stdout
        assert "mode=insecure" in run("", verify="false").stdout
    finally:
        srv.close()


def _self_update_script(tmp: Path, new_agent: str, sig_b64: str, *, openssl: bool = True) -> str:
    src = AGENT_SH.read_text()
    pub = re.search(r"(AGENT_SIGNING_PUBKEY='.*?')", src, re.S).group(1)
    funcs = "\n".join(_extract_bash_function(src, n) for n in ("log", "verify_sig", "self_update"))
    (tmp / "served").mkdir(exist_ok=True)
    (tmp / "served" / "agent.sh").write_text(new_agent)
    (tmp / "served" / "agent.sh.sig").write_text(sig_b64)
    (tmp / "self.sh").write_text("#!/bin/bash\necho OLD\n")
    sha = hashlib.sha256(new_agent.encode()).hexdigest()
    nossl = "command() { [ \"$2\" = openssl ] && return 1; builtin command \"$@\"; }" if not openssl else ""
    return textwrap.dedent(f"""
        set -u
        DEBUG=0; AUTO_UPDATE=true; SELF="{tmp}/self.sh"; ARGS=(); API=http://jt
        {pub}
        {funcs}
        fake_curl() {{ local out="" url=""; while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2;; *) url="$1"; shift;; esac; done
                      cp "{tmp}/served/${{url##*/}}" "$out"; }}
        CURL=(fake_curl)
        {nossl}
        self_update {sha} 0
        echo "after=$(cat "$SELF" | tail -1)"
    """)


def _sign(data: bytes) -> str:
    key_path = Path.home() / ".config" / "jt-ipam" / "agent-signing-key.pem"
    if not key_path.exists():
        pytest.skip("簽章私鑰只在開發機上")
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    return base64.b64encode(key.sign(data, padding.PKCS1v15(), hashes.SHA256())).decode()


@NEED_TOOLS
def test_self_update_installs_only_a_signed_agent(tmp_path) -> None:
    new = "#!/bin/bash\necho NEW\n"
    ok = _bash(_self_update_script(tmp_path, new, _sign(new.encode())))
    assert "NEW" in ok.stdout and "signature verified" in ok.stdout, ok.stdout + ok.stderr

    forged = "#!/bin/bash\necho PWNED\n"
    bad = _bash(_self_update_script(tmp_path, forged, _sign(b"something else")))
    assert "SIGNATURE CHECK FAILED" in bad.stdout and "after=echo OLD" in bad.stdout, bad.stdout + bad.stderr


@NEED_TOOLS
def test_self_update_without_openssl_does_not_replace_the_agent(tmp_path) -> None:
    new = "#!/bin/bash\necho NEW\n"
    r = _bash(_self_update_script(tmp_path, new, _sign(new.encode()), openssl=False))
    assert "openssl is not installed" in r.stdout and "after=echo OLD" in r.stdout, r.stdout + r.stderr


def test_windows_agent_pins_and_checks_the_signature() -> None:
    ps = AGENT_PS1.read_text()
    upd = ps[ps.index("function Invoke-SelfUpdate"):]
    upd = upd[:upd.index("\n}\n")]
    assert upd.index("Test-JtSignature") < upd.index("Move-Item"), "驗章要在替換之前"
    assert '"X-Agent-Tls" = $TLS_MODE' in ps
    assert "Enable-JtPin -Sha256 $CERT_SHA256" in ps
    inst = INSTALLER_PS1.read_text()
    assert "JT_IPAM_CERT_SHA256" in inst and "Enable-JtPin -Sha256 $CertSha256" in inst
    sh = INSTALLER_SH.read_text()
    assert '--pinnedpubkey "$JT_IPAM_PIN"' in sh and "PINNED_PUBKEY=${JT_IPAM_PIN}" in sh


async def test_agent_files_are_served_byte_for_byte_with_signatures(client) -> None:
    for name, path in (("agent.sh", AGENT_SH), ("agent.ps1", AGENT_PS1)):
        r = await client.get(f"/api/v1/cert-agents/{name}")
        assert r.status_code == 200
        assert r.content == path.read_bytes()
        sig = await client.get(f"/api/v1/cert-agents/{name}.sig")
        assert sig.status_code == 200
        assert sig.text.strip() == Path(f"{path}.sig").read_text().strip()


async def test_check_records_how_the_agent_verifies_the_server(client, auth_headers) -> None:
    r = await client.post("/api/v1/cert-agents", headers=auth_headers, json={"name": "trust-a"})
    a = r.json()
    for mode, want in (("pinned", "pinned"), ("insecure", "insecure"), ("bogus", None)):
        await client.get("/api/v1/cert-agents/check", headers={"X-Agent-Key": a["enroll_key"], "X-Agent-Tls": mode})
        row = next(x for x in (await client.get("/api/v1/cert-agents", headers=auth_headers)).json()["items"]
                   if x["id"] == a["id"])
        assert row["tls_mode"] == want
    await client.get("/api/v1/cert-agents/check", headers={"X-Agent-Key": a["enroll_key"]})
    row = next(x for x in (await client.get("/api/v1/cert-agents", headers=auth_headers)).json()["items"]
               if x["id"] == a["id"])
    assert row["tls_mode"] is None, "舊版代理沒帶標頭"


@NEED_TOOLS
async def test_server_identity_reads_the_local_certificate(client, auth_headers, tmp_path) -> None:
    srv = _Https(tmp_path)
    try:
        r = await client.get("/api/v1/cert-agents/server-identity", headers=auth_headers,
                             params={"origin": f"https://ipam.example.net:{srv.port}"})
        d = r.json()
        assert d["available"] is True, d
        assert d["spki_pin"] == srv.pin()
        assert d["cert_sha256"] == hashlib.sha256(srv.der).hexdigest().upper()
        assert d["ca_trusted"] is False
        assert d["chain_valid"] is False, "自簽的憑證鏈不是公開 CA 簽的"
    finally:
        srv.close()
    r = await client.get("/api/v1/cert-agents/server-identity", headers=auth_headers,
                         params={"origin": "http://ipam.example.net"})
    assert r.json() == {"available": False, "reason": "not_https"}


@NEED_TOOLS
def test_server_identity_never_offers_old_tls(tmp_path, monkeypatch) -> None:
    """讀本機憑證的連線也只用 TLS 1.2 以上（CodeQL #61 #62：create_default_context 預設還允許 1.0 / 1.1）。"""
    from app.services import server_tls
    made: list[ssl.SSLContext] = []
    real = ssl.create_default_context

    def spy(*a, **kw):  # type: ignore[no-untyped-def]
        ctx = real(*a, **kw)
        made.append(ctx)
        return ctx
    monkeypatch.setattr(server_tls.ssl, "create_default_context", spy)
    srv = _Https(tmp_path)
    try:
        server_tls._fetch("ipam.example.net", srv.port)
    finally:
        srv.close()
    assert len(made) >= 2 and all(c.minimum_version >= ssl.TLSVersion.TLSv1_2 for c in made)
