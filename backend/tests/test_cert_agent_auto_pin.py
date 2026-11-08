"""已經裝好的代理自己改成驗證 jt-ipam（2026-11-07）。

使用者：「叫已經裝過代理的客戶一台一台去做，非常不合理」。代理升級後：
1. 不驗證的代理抓一次信任資料（不帶代理金鑰），先試 CA 驗證、不行就釘選公鑰，**用新設定連得上才換**
2. jt-ipam 關掉自動採用時不換；本機看到的憑證跟代理看到的不同（中間有負載平衡器）時也不換
3. jt-ipam 換憑證：用記下的身分金鑰驗章、再實際連一次，才跟著換；別的身分、或簽章不對都不跟
4. 已經在驗證的代理（1.0.20 起裝的）也記下身分，之後換憑證才跟得上

這裡跑 Linux 派送代理真正的 bash 函式，對一台在本機起的假 jt-ipam（HTTPS，簽章資料由測試自己簽）。
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import shutil
import ssl
import subprocess
import textwrap
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tests.test_cert_agent_state_key import AGENT_SH, _extract_bash_function
from tests.test_cert_agent_verify import _pair

NEED_TOOLS = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("openssl") is None
                                or shutil.which("curl") is None, reason="需要 bash、openssl、curl")


def _b64int(n: int) -> str:
    return base64.b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).decode()


def spki_pin(der: bytes) -> str:
    spki = x509.load_der_x509_certificate(der).public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return "sha256//" + base64.b64encode(hashlib.sha256(spki).digest()).decode()


def signed_bundle(identity: rsa.RSAPrivateKey, der: bytes, *, auto_pin: bool = True,
                  sign_with: rsa.RSAPrivateKey | None = None, pin: str | None = None,
                  not_after: int | None = None) -> str:
    """跟伺服器端 services/agent_trust.bundle 同一個格式。"""
    pub = identity.public_key()
    nums = pub.public_numbers()
    lines = [
        "jt-ipam-agent-trust=1",
        f"auto_pin={1 if auto_pin else 0}",
        "identity_spki=" + base64.b64encode(pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode(),
        f"identity_n={_b64int(nums.n)}",
        f"identity_e={_b64int(nums.e)}",
        f"spki_pin={pin or spki_pin(der)}",
        "cert_sha256=" + hashlib.sha256(der).hexdigest().upper(),
        f"not_after={not_after or int((datetime.now(UTC) + timedelta(days=30)).timestamp())}",
    ]
    body = "".join(x + "\n" for x in lines)
    sig = (sign_with or identity).sign(body.encode(), padding.PKCS1v15(), hashes.SHA256())
    return body + "sig=" + base64.b64encode(sig).decode() + "\n"


class FakeIpam:
    """假的 jt-ipam：/api/v1/agent-trust 回簽過章的資料，其他路徑回 ok（當成代理平常的請求）。"""

    def __init__(self, tmp: Path, name: str, identity: rsa.RSAPrivateKey, cn: str = "ipam.example.net",
                 **bundle_kw) -> None:
        pem, key, _ = _pair(cn)
        self.pem_path = tmp / f"{name}.pem"
        self.pem_path.write_text(pem)
        (tmp / f"{name}.key").write_text(key)
        self.der = ssl.PEM_cert_to_DER_cert(pem)
        self.pin = spki_pin(self.der)
        self.cert_sha256 = hashlib.sha256(self.der).hexdigest().upper()
        self.bundle = signed_bundle(identity, self.der, **bundle_kw)
        self.seen: list[tuple[str, dict[str, str]]] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
                body = (outer.bundle if self.path.startswith("/api/v1/agent-trust") else '{"ok": true}').encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # type: ignore[no-untyped-def]
                pass
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(self.pem_path, tmp / f"{name}.key")
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


@pytest.fixture
def identity() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


@pytest.fixture
def servers(tmp_path):  # type: ignore[no-untyped-def]
    made: list[FakeIpam] = []

    def make(name: str, identity: rsa.RSAPrivateKey, **kw) -> FakeIpam:  # type: ignore[no-untyped-def]
        s = FakeIpam(tmp_path, name, identity, **kw)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


FUNCS = ("log", "dbg", "build_curl", "valid_pin", "valid_b64", "load_trust", "trust_get", "tfield",
         "trust_sig_ok", "save_trust", "ca_opts", "trust_maintain", "trust_recover")


def _agent(tmp: Path, server: str, *, verify: str = "true", pin: str = "", ca_cert: str = "",
           body: str) -> subprocess.CompletedProcess[str]:
    src = AGENT_SH.read_text()
    funcs = "\n".join(_extract_bash_function(src, n) for n in FUNCS)
    state = tmp / "state"
    script = textwrap.dedent(f"""
        set -u
        DEBUG=1; AGENT_KEY=secret-key; AGENT_VERSION=t; AUTO_PIN=true
        SERVER='{server}'; VERIFY_TLS={verify}; PINNED_PUBKEY='{pin}'
        {"CA_CERT=" + repr(ca_cert) if ca_cert else ""}
        STATE_DIR='{state}'; TRUST_FILE='{state}/trust'; TRUST_IDENTITY=""
        {funcs}
        load_trust; build_curl
        {body}
        echo "mode=$TLS_MODE pin=$PINNED_PUBKEY"
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120)  # noqa: S603, S607


def _trust(tmp: Path) -> dict[str, str]:
    f = tmp / "state" / "trust"
    return dict(line.split("=", 1) for line in f.read_text().splitlines()) if f.exists() else {}


@NEED_TOOLS
def test_insecure_agent_pins_the_server_by_itself_without_sending_its_key(tmp_path, servers, identity) -> None:
    srv = servers("a", identity)
    r = _agent(tmp_path, f"https://127.0.0.1:{srv.port}", verify="false", body="trust_maintain")
    out = r.stdout + r.stderr
    assert f"mode=pinned pin={srv.pin}" in out, out
    t = _trust(tmp_path)
    assert t["TRUST_MODE"] == "pinned" and t["TRUST_PIN"] == srv.pin and t["TRUST_IDENTITY"]
    assert (tmp_path / "state" / "trust").stat().st_mode & 0o077 == 0
    trust_reqs = [h for p, h in srv.seen if p.startswith("/api/v1/agent-trust")]
    assert trust_reqs and all("x-agent-key" not in h for h in trust_reqs), "還沒確認對面之前不交出金鑰"

    # 下一次執行：讀信任檔就是釘選，不必再抓
    before = len(srv.seen)
    again = _agent(tmp_path, f"https://127.0.0.1:{srv.port}", verify="false",
                   body='trust_maintain; "${CURL[@]}" -o /dev/null "$SERVER/x"; echo "rc=$?"')
    assert f"mode=pinned pin={srv.pin}" in again.stdout and "rc=0" in again.stdout, again.stdout + again.stderr
    assert len(srv.seen) == before + 1


@NEED_TOOLS
def test_ca_verification_is_preferred_when_it_works(tmp_path, servers, identity) -> None:
    srv = servers("a", identity, cn="localhost")
    r = _agent(tmp_path, f"https://localhost:{srv.port}", verify="false", ca_cert=str(srv.pem_path),
               body="trust_maintain")
    assert "mode=ca pin=" in r.stdout, r.stdout + r.stderr
    assert _trust(tmp_path)["TRUST_MODE"] == "ca"


@NEED_TOOLS
def test_switch_off_on_the_server_keeps_the_agent_as_is(tmp_path, servers, identity) -> None:
    srv = servers("a", identity, auto_pin=False)
    r = _agent(tmp_path, f"https://127.0.0.1:{srv.port}", verify="false", body="trust_maintain")
    assert "mode=insecure" in r.stdout and _trust(tmp_path) == {}, r.stdout + r.stderr


@NEED_TOOLS
def test_no_switch_when_the_agent_sees_another_certificate(tmp_path, servers, identity) -> None:
    """中間有負載平衡器：jt-ipam 本機看到的不是代理看到的那張 → 用那個指紋連不上 → 不換、不會把自己鎖在外面。"""
    other = "sha256//" + base64.b64encode(hashlib.sha256(b"lb").digest()).decode()
    srv = servers("a", identity, pin=other)
    r = _agent(tmp_path, f"https://127.0.0.1:{srv.port}", verify="false", body="trust_maintain")
    assert "mode=insecure" in r.stdout and _trust(tmp_path) == {}, r.stdout + r.stderr


@NEED_TOOLS
def test_pinned_agent_learns_the_identity_and_follows_a_signed_certificate_change(tmp_path, servers, identity) -> None:
    old = servers("old", identity)
    r = _agent(tmp_path, f"https://127.0.0.1:{old.port}", pin=old.pin, body="trust_maintain")
    assert _trust(tmp_path)["TRUST_PIN"] == old.pin, r.stdout + r.stderr

    new = servers("new", identity)                       # 同一台 jt-ipam 換了憑證（新的金鑰）
    r = _agent(tmp_path, f"https://127.0.0.1:{new.port}", pin=old.pin, body=textwrap.dedent("""
        "${CURL[@]}" -o /dev/null "$SERVER/x"; rc=$?; echo "first=$rc"
        case "$rc" in 51|60|90) trust_recover && { "${CURL[@]}" -o /dev/null "$SERVER/x"; echo "retry=$?"; } ;; esac
    """))
    out = r.stdout + r.stderr
    assert "first=90" in out and "retry=0" in out and "following it" in out, out
    assert _trust(tmp_path)["TRUST_PIN"] == new.pin


@NEED_TOOLS
@pytest.mark.parametrize("case", ["other_identity", "bad_signature", "expired"])
def test_does_not_follow_an_unsigned_change(tmp_path, servers, identity, case) -> None:
    old = servers("old", identity)
    _agent(tmp_path, f"https://127.0.0.1:{old.port}", pin=old.pin, body="trust_maintain")
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    kw = {"other_identity": {"identity": stranger},
          "bad_signature": {"identity": identity, "sign_with": stranger},
          "expired": {"identity": identity, "not_after": 1_000_000}}[case]
    fake = servers("fake", **kw)
    r = _agent(tmp_path, f"https://127.0.0.1:{fake.port}", pin=old.pin, body=textwrap.dedent("""
        "${CURL[@]}" -o /dev/null "$SERVER/x"; rc=$?
        trust_recover; echo "recover=$?"
    """))
    out = r.stdout + r.stderr
    assert "recover=1" in out and _trust(tmp_path)["TRUST_PIN"] == old.pin, out
    assert all("x-agent-key" not in h for p, h in fake.seen), "假冒的伺服器收不到金鑰"
    if case == "bad_signature":
        assert "not signed by this jt-ipam" in out


def test_installer_forgets_what_the_agent_learned() -> None:
    """重新安裝（管理員明確選了驗證方式）要清掉代理自己學到的設定。"""
    from tests.test_cert_agent_state_key import INSTALLER_SH
    assert INSTALLER_SH.read_text().count('rm -f "${STATEDIR}/trust"') == 2
