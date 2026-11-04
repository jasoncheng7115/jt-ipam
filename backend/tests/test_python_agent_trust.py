"""掃描代理、RustDesk 代理確認連到的是真的 jt-ipam（2026-11-04，派送代理之後的同一批修正）。

兩支 Python 代理以前的安裝指令一律 `JT_IPAM_INSECURE=1`，自我更新只比對同一台伺服器給的 sha256：
能攔截網路的人可以假冒 jt-ipam，拿走代理金鑰，或推一支「新版代理」讓主機以 root 執行。

守的是：
1. 釘選：`JT_IPAM_PIN=sha256//<base64>`（公鑰的 SHA-256，跟 curl `--pinnedpubkey` 同格式）時，
   只跟出示這把公鑰的伺服器講話；別台在送出任何請求（含金鑰）之前就被拒。主控台中繼的 WebSocket 也一樣。
2. 每次連線帶 `X-Agent-Tls`（pinned / ca / insecure），伺服器記下來、清單標出還沒驗證的代理
3. 自我更新只裝 jt-ipam 簽章金鑰簽過的版本（純 Python 驗 RSA，不靠 openssl）
4. 兩支代理的信任程式碼一字不差（各自是單檔、不能共用模組）
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import importlib.util
import inspect
import ssl
import threading
import uuid
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tests.test_cert_agent_verify import _pair

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"
SCAN = AGENT_DIR / "jt_ipam_agent.py"
RUSTDESK = AGENT_DIR / "jt_ipam_rustdesk_agent.py"
AGENTS = pytest.mark.parametrize("path", [SCAN, RUSTDESK], ids=["scan", "rustdesk"])
TRUST_FUNCS = ("_tls_mode", "_ctx", "_der_tlv", "_spki_pin", "_check_pin", "_urlopen", "_verify_signature")


def _load(path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(f"jt_trust_{uuid.uuid4().hex[:6]}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class _Https:
    """記下每個請求的標頭；指紋不對的代理應該連一個請求都送不到這裡。"""

    def __init__(self, tmp: Path) -> None:
        pem, key, _ = _pair("ipam.example.net")
        (tmp / "srv.pem").write_text(pem)
        (tmp / "srv.key").write_text(key)
        self.der = ssl.PEM_cert_to_DER_cert(pem)
        self.seen: list[dict[str, str]] = []
        seen = self.seen

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append({k.lower(): v for k, v in self.headers.items()})
                body = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # type: ignore[no-untyped-def]
                pass
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(tmp / "srv.pem", tmp / "srv.key")
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def pin(self) -> str:
        spki = x509.load_der_x509_certificate(self.der).public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        return "sha256//" + base64.b64encode(hashlib.sha256(spki).digest()).decode()

    def close(self) -> None:
        self.httpd.shutdown()


@pytest.fixture
def srv(tmp_path):  # type: ignore[no-untyped-def]
    s = _Https(tmp_path)
    yield s
    s.close()


@AGENTS
def test_spki_pin_matches_the_curl_format(path, srv) -> None:
    a = _load(path)
    assert a._spki_pin(srv.der) == srv.pin()


@AGENTS
def test_pinned_agent_talks_only_to_the_pinned_server(path, srv) -> None:
    a = _load(path)
    # 憑證發給 ipam.example.net、連的是 127.0.0.1：有釘選就不看主機名稱與 CA，只看公鑰
    a.SERVER, a.KEY, a.INSECURE, a.PIN = f"https://127.0.0.1:{srv.port}", "secret-key", False, srv.pin()
    assert a._tls_mode() == "pinned"
    assert a._req("GET", "/x") == {"ok": True}
    assert srv.seen[-1]["x-agent-key"] == "secret-key"
    assert srv.seen[-1]["x-agent-tls"] == "pinned"

    before = len(srv.seen)
    a.PIN = "sha256//" + base64.b64encode(hashlib.sha256(b"someone else").digest()).decode()
    with pytest.raises(OSError, match="JT_IPAM_PIN"):      # urllib 會把 SSLError 包成 URLError
        a._req("GET", "/x")
    with pytest.raises(OSError, match="JT_IPAM_PIN"):
        a._get_bytes("/agent.py")
    assert len(srv.seen) == before, "指紋不同的伺服器（假冒的）連一個請求、一個金鑰都收不到"


@AGENTS
def test_modes_without_a_pin(path, srv) -> None:
    a = _load(path)
    a.SERVER, a.KEY, a.PIN, a.INSECURE = f"https://127.0.0.1:{srv.port}", "k", "", False
    assert a._tls_mode() == "ca"
    with pytest.raises(OSError, match="CERTIFICATE_VERIFY_FAILED"):     # 自簽憑證、系統 CA 驗不過
        a._req("GET", "/x")
    a.INSECURE = True
    assert a._tls_mode() == "insecure"
    assert a._req("GET", "/x") == {"ok": True}
    assert srv.seen[-1]["x-agent-tls"] == "insecure"
    a.SERVER, a.INSECURE = "http://127.0.0.1:1", False
    assert a._tls_mode() == "insecure", "http 根本沒有加密"


def test_console_relay_websocket_is_pinned_too(srv) -> None:
    a = _load(SCAN)
    a.SERVER, a.KEY, a.INSECURE = f"https://127.0.0.1:{srv.port}", "secret-key", False
    a.PIN = "sha256//" + base64.b64encode(hashlib.sha256(b"someone else").digest()).decode()
    with pytest.raises(ssl.SSLError, match="JT_IPAM_PIN"):
        a._ws_connect("/api/v1/scan-agents/relay", {})
    assert srv.seen == []


def _signing_key():  # type: ignore[no-untyped-def]
    key_path = Path.home() / ".config" / "jt-ipam" / "agent-signing-key.pem"
    if not key_path.exists():
        pytest.skip("簽章私鑰只在開發機上")
    return serialization.load_pem_private_key(key_path.read_bytes(), password=None)


def _sign(data: bytes) -> str:
    return base64.b64encode(_signing_key().sign(data, padding.PKCS1v15(), hashes.SHA256())).decode()


@AGENTS
def test_signature_check(path) -> None:
    a = _load(path)
    data = b"print('new agent')\n"
    assert a._verify_signature(data, _sign(data)) is True
    assert a._verify_signature(data + b"#", _sign(data)) is False
    assert a._verify_signature(data, "not base64 !!") is False
    assert a._verify_signature(data, "") is False
    assert a._verify_signature(data, base64.b64encode(b"\x00" * 384).decode()) is False


@AGENTS
def test_self_update_installs_only_a_signed_agent(path, tmp_path, monkeypatch) -> None:
    a = _load(path)
    me = tmp_path / "agent.py"
    me.write_text("OLD\n")
    a.AGENT_PATH, a.AUTO_UPDATE = str(me), True
    execs: list[list[str]] = []
    monkeypatch.setattr(a.os, "execv", lambda exe, args: execs.append(args))
    new = b"NEW\n"
    served: dict[str, bytes] = {}
    monkeypatch.setattr(a, "_get_bytes", lambda p: served[p.rsplit("/", 1)[-1]])

    served.update({"agent.py": new, "agent.py.sig": _sign(b"something else").encode()})
    a._maybe_self_update(hashlib.sha256(new).hexdigest())
    assert me.read_text() == "OLD\n" and execs == [], "簽章不對 → 不換、不重新執行"

    served["agent.py.sig"] = _sign(new).encode()
    a._maybe_self_update(hashlib.sha256(new).hexdigest())
    assert me.read_bytes() == new and len(execs) == 1


def test_trust_code_is_identical_in_both_agents() -> None:
    scan, rd = _load(SCAN), _load(RUSTDESK)
    for name in TRUST_FUNCS:
        assert inspect.getsource(getattr(scan, name)) == inspect.getsource(getattr(rd, name)), name
    assert scan.AGENT_SIGNING_N == rd.AGENT_SIGNING_N and scan.AGENT_SIGNING_E == rd.AGENT_SIGNING_E


def test_embedded_key_is_the_signing_key() -> None:
    nums = _signing_key().public_key().public_numbers()
    a = _load(SCAN)
    assert (a.AGENT_SIGNING_N, a.AGENT_SIGNING_E) == (nums.n, nums.e)


@AGENTS
def test_shipped_python_agents_are_signed(path) -> None:
    a = _load(path)
    sig = Path(f"{path}.sig").read_text()
    assert a._verify_signature(path.read_bytes(), sig), f"{path.name} 改過沒重簽：跑 scripts/sign-agents.sh"


# ── 伺服器端：原封不動送出代理與簽章、記下代理怎麼驗證伺服器 ─────────────────

@pytest.mark.anyio
@pytest.mark.parametrize(("url", "path"), [("/api/v1/scan-agents/agent.py", SCAN),
                                           ("/api/v1/rustdesk/agent/agent.py", RUSTDESK)], ids=["scan", "rustdesk"])
async def test_agent_files_are_served_byte_for_byte_with_signatures(client, url, path) -> None:
    r = await client.get(url)
    assert r.status_code == 200 and r.content == path.read_bytes()
    sig = await client.get(f"{url}.sig")
    assert sig.status_code == 200
    assert sig.text.strip() == Path(f"{path}.sig").read_text().strip()


@pytest.mark.anyio
async def test_scan_agent_poll_records_how_it_verifies_the_server(client, auth_headers) -> None:
    r = await client.post("/api/v1/scan-agents", headers=auth_headers, json={"name": f"trust-{uuid.uuid4().hex[:6]}"})
    assert r.status_code in (200, 201), r.text
    aid, key = r.json()["id"], r.json()["enroll_key"]

    async def mode() -> str | None:
        items = (await client.get("/api/v1/scan-agents", headers=auth_headers)).json()["items"]
        return next(x for x in items if x["id"] == aid)["tls_mode"]
    for sent, want in (("pinned", "pinned"), ("insecure", "insecure"), ("bogus", None)):
        await client.get("/api/v1/scan-agents/poll", headers={"X-Agent-Key": key, "X-Agent-Tls": sent})
        assert await mode() == want
    await client.get("/api/v1/scan-agents/poll", headers={"X-Agent-Key": key})
    assert await mode() is None, "舊版代理沒帶標頭"


@pytest.mark.anyio
async def test_rustdesk_agent_poll_records_how_it_verifies_the_server(client, auth_headers) -> None:
    r = await client.post("/api/v1/rustdesk/servers", headers=auth_headers, json={
        "name": f"rd-{uuid.uuid4().hex[:6]}", "report_interval_seconds": 120, "client_address": "rd.example.net"})
    sid, raw = r.json()["id"], r.json()["agent_key"]

    async def mode() -> str | None:
        items = (await client.get("/api/v1/rustdesk/servers", headers=auth_headers)).json()["items"]
        return next(x for x in items if x["id"] == sid)["agent_tls_mode"]
    for sent, want in (("pinned", "pinned"), ("ca", "ca"), ("bogus", None)):
        await client.post("/api/v1/rustdesk/agent/poll", headers={"X-Agent-Key": raw, "X-Agent-Tls": sent},
                          json={"version": "1.3.0"})
        assert await mode() == want


def test_installers_pin_the_download_and_write_the_pin() -> None:
    scan = (AGENT_DIR / "jt-ipam-agent-installer.sh").read_text()
    assert 'CURL_TLS=(-k --pinnedpubkey "$JT_IPAM_PIN")' in scan and "JT_IPAM_PIN=${JT_IPAM_PIN}" in scan
    rd = (AGENT_DIR / "jt-ipam-rustdesk-agent-installer.sh").read_text()
    assert 'CURL_OPTS+=(-k --pinnedpubkey "$JT_IPAM_PIN")' in rd and 'echo "JT_IPAM_PIN=${JT_IPAM_PIN}"' in rd
    # 重跑時有給驗證方式（連空的 JT_IPAM_INSECURE= 都算）就整組換掉，舊指紋不會在換憑證後留著
    assert '[[ -n "${JT_IPAM_PIN+x}${JT_IPAM_INSECURE+x}" ]] && TRUST_GIVEN=1' in rd


@pytest.mark.parametrize(("installer", "end"), [
    ("jt-ipam-agent-installer.sh", 'JT_IPAM_DHCPD_LEASES="${JT_IPAM_DHCPD_LEASES:-}"'),
    ("jt-ipam-rustdesk-agent-installer.sh", '[[ -n "$JT_IPAM_PIN" ]] && JT_IPAM_INSECURE=""'),
], ids=["scan", "rustdesk"])
def test_rerunning_the_installer_keeps_the_key_and_replaces_the_server_check(installer, end, tmp_path) -> None:
    import subprocess
    src = (AGENT_DIR / installer).read_text()
    a = src.index('TRUST_GIVEN=""')
    part = src[a:src.index(end, a) + len(end)]
    env = tmp_path / "agent.env"
    env.write_text("JT_IPAM_URL=https://ipam.example.net\nJT_IPAM_AGENT_KEY=old-key\nJT_IPAM_INSECURE=1\n")

    def run(given: str) -> str:
        script = f'set -euo pipefail\nENVFILE="{env}"\n{given}\n{part}\necho "key=$JT_IPAM_AGENT_KEY pin=$JT_IPAM_PIN insecure=$JT_IPAM_INSECURE"'
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,  # noqa: S603, S607
                              check=True).stdout.strip()
    assert run("") == "key=old-key pin= insecure=1", "只是重跑升級：全部沿用"
    assert run("JT_IPAM_PIN='sha256//abc='") == "key=old-key pin=sha256//abc= insecure=", "補上釘選：金鑰沿用、不驗證拿掉"
    env.write_text(env.read_text() + "JT_IPAM_PIN=sha256//old=\n")
    assert run("JT_IPAM_INSECURE=") == "key=old-key pin= insecure=", "改用系統 CA：舊指紋一起清掉"
