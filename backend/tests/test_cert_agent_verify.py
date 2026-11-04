"""Linux 派送代理：換上後實際連過去確認（2026-11-03 使用者核可）。

以前只有「寫檔 → config-test → reload，失敗才換回」：設定測過、reload 也成功，但服務送出去的
還是舊憑證（例如另一個 server 區塊在用別的路徑、中繼憑證放錯檔），這種情況一直回報 ok。

守的是（直接抽出代理裡真正會跑的函式，在本機起一個 TLS 服務當目標）：
1. 有指定確認位址、服務送出新憑證 → ok，訊息寫已確認
2. 服務一直送舊憑證 → 換回原本的檔案、再 reload 一次，回報 failed，並確認已經回到原本那張
3. 換之前就連不到 → 不換回，標「未確認」（跟防火牆推送同一套規則）
4. 沒指定確認位址就不確認；只有一個埠只送一張憑證的服務（Proxmox、Cockpit…）才預設確認
"""

from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import textwrap
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tests.test_cert_agent_state_key import AGENT_SH, _extract_bash_function

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("openssl") is None,
                                reason="需要 bash 與 openssl")


def _pair(cn: str) -> tuple[str, str, str]:
    k = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    c = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
         .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
         .public_key(k.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
         .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
         .sign(k, hashes.SHA256()))
    pem = c.public_bytes(serialization.Encoding.PEM).decode()
    key = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
    return pem, key, c.fingerprint(hashes.SHA256()).hex()


class _Tls:
    """每次連線都重新讀檔的 TLS 服務：等於「reload 之後就送新檔」的服務。"""

    def __init__(self, cert: Path, key: Path) -> None:
        self.cert, self.key = cert, key
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(self.cert, self.key)
                with ctx.wrap_socket(conn, server_side=True) as s:
                    s.recv(1)
            except Exception:  # 測試用服務，客戶端斷線都正常
                conn.close()

    def close(self) -> None:
        self.sock.close()


def _run(tmp_path: Path, *, served_cert: Path, served_key: Path, verify: str | None,
         new_pem: str, new_key: str, new_fp: str, profile: str = "nginx") -> dict[str, str]:
    src = AGENT_SH.read_text()
    names = ("log", "dbg", "run_q", "profile_spec", "add_report", "served_fp", "wait_fp", "apply_deployment")
    funcs = "\n".join(_extract_bash_function(src, n) for n in names)
    parts = tmp_path / "parts"
    parts.mkdir(exist_ok=True)
    (parts / "fullchain").write_text(new_pem)
    (parts / "key").write_text(new_key)
    reloads = tmp_path / "reloads"
    script = textwrap.dedent(f"""
        set -u
        DEBUG=0; DRY_RUN=0; TLS_BASE="{tmp_path}/tls"; VERIFY_TRIES=3; VERIFY_WAIT=0.2
        {funcs}
        fetch_part() {{ cp "{parts}/$2" "$3"; }}
        {"D_verify=" + repr(verify) if verify is not None else ""}
        D_reload="echo reload >> {reloads}"; D_test="true"
        D_fullchain_path="{served_cert}"; D_key_path="{served_key}"
        apply_deployment web {profile} {new_fp} 2030-01-01; echo "rc=[$?]"
        printf 'report=[%s]\\n' "${{REPORT_LINES[0]}}"
    """)
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120)  # noqa: S603, S607
    out = r.stdout + r.stderr
    rep = out.split("report=[", 1)[1].rsplit("]", 1)[0].split("\t")
    return {"out": out, "status": rep[2], "msg": rep[6],
            "reloads": reloads.read_text().count("reload") if reloads.exists() else 0}


def _serve(tmp_path: Path, pem: str, key: str) -> tuple[Path, Path, _Tls]:
    cert_p, key_p = tmp_path / "site.pem", tmp_path / "site.key"
    cert_p.write_text(pem)
    key_p.write_text(key)
    return cert_p, key_p, _Tls(cert_p, key_p)


def test_verified_when_the_service_presents_the_new_certificate(tmp_path) -> None:
    old_pem, old_key, _ = _pair("www.example.net")
    new_pem, new_key, new_fp = _pair("www.example.net")
    cert_p, key_p, srv = _serve(tmp_path, old_pem, old_key)
    try:
        r = _run(tmp_path, served_cert=cert_p, served_key=key_p, verify=f"www.example.net:{srv.port}@127.0.0.1",
                 new_pem=new_pem, new_key=new_key, new_fp=new_fp)
    finally:
        srv.close()
    assert r["status"] == "ok", r["out"]
    assert "verified" in r["msg"]
    assert cert_p.read_text() == new_pem


def test_service_still_presenting_the_old_certificate_is_rolled_back(tmp_path) -> None:
    old_pem, old_key, old_fp = _pair("www.example.net")
    new_pem, new_key, new_fp = _pair("www.example.net")
    # 服務實際用的是另一組檔案（例如另一個 server 區塊）：寫進去的檔案不會被送出去
    _, _, srv = _serve(tmp_path, old_pem, old_key)
    target_cert, target_key = tmp_path / "other.pem", tmp_path / "other.key"
    target_cert.write_text("previous-content\n")
    target_key.write_text("previous-key\n")
    try:
        r = _run(tmp_path, served_cert=target_cert, served_key=target_key,
                 verify=f"127.0.0.1:{srv.port}", new_pem=new_pem, new_key=new_key, new_fp=new_fp)
    finally:
        srv.close()
    assert r["status"] == "failed", r["out"]
    assert "verify" in r["msg"]
    assert old_fp[:12] in r["msg"], "要講出服務實際送的是哪一張"
    assert target_cert.read_text() == "previous-content\n", "要換回原本的檔案"
    assert target_key.read_text() == "previous-key\n"
    assert r["reloads"] == 2, "換回之後要再 reload 一次"
    assert "rolled back" in r["msg"]


def test_unreachable_before_the_change_is_not_rolled_back(tmp_path) -> None:
    new_pem, new_key, new_fp = _pair("www.example.net")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()   # 沒有人在聽
    cert_p, key_p = tmp_path / "site.pem", tmp_path / "site.key"
    r = _run(tmp_path, served_cert=cert_p, served_key=key_p, verify=f"127.0.0.1:{port}",
             new_pem=new_pem, new_key=new_key, new_fp=new_fp)
    assert r["status"] == "ok", r["out"]
    assert "not verified" in r["msg"]
    assert cert_p.read_text() == new_pem


def test_no_verify_address_means_no_check_for_multi_site_profiles(tmp_path) -> None:
    new_pem, new_key, new_fp = _pair("www.example.net")
    cert_p, key_p = tmp_path / "site.pem", tmp_path / "site.key"
    r = _run(tmp_path, served_cert=cert_p, served_key=key_p, verify=None,
             new_pem=new_pem, new_key=new_key, new_fp=new_fp)
    assert r["status"] == "ok", r["out"]
    assert r["msg"] == "applied"


def test_single_certificate_services_verify_by_default() -> None:
    src = AGENT_SH.read_text()
    spec = _extract_bash_function(src, "profile_spec")
    script = spec + "\n" + "\n".join(
        f'profile_spec {p} c /b; echo "{p}=[$PVERIFY]"'
        for p in ("pve", "pmg", "pbs", "pdm", "cockpit", "webmin", "nginx", "apache", "postfix"))
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout  # noqa: S603, S607
    got = dict(line.split("=[", 1) for line in out.strip().splitlines())
    got = {k: v.rstrip("]") for k, v in got.items()}
    assert got["pve"] == "127.0.0.1:8006"
    assert got["pbs"] == "127.0.0.1:8007"
    assert got["pdm"] == "127.0.0.1:8443"
    assert got["cockpit"] == "127.0.0.1:9090"
    assert (got["nginx"], got["apache"], got["postfix"]) == ("", "", ""), "多站台的服務不可以猜"


def test_verify_can_be_turned_off_and_is_documented() -> None:
    src = AGENT_SH.read_text()
    assert 'D_verify="$(_f "$n" VERIFY)"' in src
    assert "DEPLOY_1_VERIFY" in src
