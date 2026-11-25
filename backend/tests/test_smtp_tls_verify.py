"""SMTP 的 STARTTLS / SMTPS 要驗證伺服器憑證（內部原始碼掃描：Insecure Transport: Mail Transmission）。

smtplib 的 `starttls()` 與 `SMTP_SSL()` 沒給 context 時用 `ssl._create_stdlib_context()`，
那個 context **不驗憑證、不比主機名稱**：中間人可以拿任意憑證接住連線，SMTP 帳密就跟著外洩。
改成預設驗證；內部轉信主機用自簽憑證或企業 CA 的站台，在「通知發送設定」貼上那張憑證（照樣驗證），
真的拿不到才關掉「驗證伺服器憑證」。
"""
from __future__ import annotations

import datetime as _dt
import ipaddress
import socket
import ssl
import threading
from email.message import EmailMessage
from typing import Any

import pytest
from app.services import email as email_service
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


class _FakeSMTP:
    """記下 starttls / SMTP_SSL 收到的 context，不連網路。"""

    calls: list[dict[str, Any]] = []

    def __init__(self, host: str, port: int, timeout: float = 0, context: ssl.SSLContext | None = None) -> None:
        self.rec: dict[str, Any] = {"host": host, "port": port, "ctor_context": context, "starttls_context": None}
        _FakeSMTP.calls.append(self.rec)

    def ehlo(self) -> None:
        pass

    def starttls(self, *, context: ssl.SSLContext | None = None) -> None:
        self.rec["starttls_context"] = context

    def login(self, user: str, pw: str) -> None:
        self.rec["login"] = user

    def send_message(self, msg: EmailMessage) -> None:
        self.rec["sent"] = True

    def quit(self) -> None:
        pass


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTP.calls = []
    monkeypatch.setattr(email_service.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(email_service, "_guard", lambda host, port: None)
    return _FakeSMTP.calls


def _msg() -> EmailMessage:
    m = EmailMessage()
    m["To"] = "a@example.com"
    m.set_content("x")
    return m


def _verifying(ctx: ssl.SSLContext | None) -> bool:
    return ctx is not None and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


@pytest.mark.parametrize("mode,key", [("starttls", "starttls_context"), ("tls", "ctor_context")])
def test_db_config_verifies_the_server_certificate_by_default(fake_smtp, mode, key) -> None:
    email_service._send_sync_cfg({"smtp_host": "smtp.example.com", "smtp_port": 587, "smtp_tls": mode}, _msg())
    assert _verifying(fake_smtp[0][key])


@pytest.mark.parametrize("mode,key", [("starttls", "starttls_context"), ("tls", "ctor_context")])
def test_db_config_can_skip_verification_for_self_signed_relays(fake_smtp, mode, key) -> None:
    email_service._send_sync_cfg({"smtp_host": "smtp.example.com", "smtp_port": 587, "smtp_tls": mode,
                                  "smtp_verify_tls": False}, _msg())
    ctx = fake_smtp[0][key]
    assert ctx is not None and ctx.verify_mode == ssl.CERT_NONE


@pytest.mark.parametrize("mode,key", [("starttls", "starttls_context"), ("tls", "ctor_context")])
def test_env_config_verifies_the_server_certificate(fake_smtp, monkeypatch, mode, key) -> None:
    from app.core.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(s, "smtp_tls_mode", mode)
    assert s.smtp_verify_tls is True
    email_service._send_sync(_msg())
    assert _verifying(fake_smtp[0][key])


def test_certificate_failure_is_reported_as_its_own_code(monkeypatch) -> None:
    class _Bad(_FakeSMTP):
        def starttls(self, *, context=None):  # type: ignore[no-untyped-def]
            raise ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate")

    monkeypatch.setattr(email_service.smtplib, "SMTP", _Bad)
    monkeypatch.setattr(email_service, "_guard", lambda host, port: None)
    with pytest.raises(email_service.EmailSendError) as ei:
        email_service._send_sync_cfg({"smtp_host": "smtp.example.com", "smtp_tls": "starttls"}, _msg())
    assert ei.value.code == "smtp_cert_verify_failed"
    assert "self-signed" in ei.value.params["reason"]


async def test_verify_setting_defaults_on_and_persists(db_session, admin_user) -> None:
    from app.services import system_config as sc
    sc._ncfg_cache.clear()
    assert (await sc.get_notification_channels(db_session))["smtp_verify_tls"] is True
    await sc.set_notification_channels(db_session, data={"smtp_verify_tls": False}, updated_by_user_id=admin_user.id)
    await db_session.commit()
    sc._ncfg_cache.clear()
    assert (await sc.get_notification_channels(db_session))["smtp_verify_tls"] is False


async def test_api_exposes_and_accepts_the_verify_setting(client, auth_headers) -> None:
    r = await client.get("/api/v1/system/notification-channels", headers=auth_headers)
    assert r.status_code == 200 and r.json()["smtp_verify_tls"] is True
    r = await client.put("/api/v1/system/notification-channels", headers=auth_headers,
                         json={"smtp_verify_tls": False})
    assert r.status_code == 200 and r.json()["smtp_verify_tls"] is False


async def test_api_test_email_reports_certificate_failure_code(client, auth_headers, monkeypatch) -> None:
    async def boom(cfg, **kw):  # type: ignore[no-untyped-def]
        raise email_service.EmailSendError("certificate verify failed", code="smtp_cert_verify_failed",
                                           reason="certificate verify failed")
    monkeypatch.setattr(email_service, "send_email_via_config", boom)
    await client.put("/api/v1/system/notification-channels", headers=auth_headers,
                     json={"smtp_host": "smtp.example.com"})
    r = await client.post("/api/v1/system/notification-channels/test-email", headers=auth_headers,
                          json={"to": "a@example.com"})
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "smtp_cert_verify_failed"


# ───────────── 真的 TLS 交握：自簽憑證的轉信主機（使用者 11-25：「但如果是自簽憑證呢」）─────────────
def _self_signed(tmp_path) -> tuple[str, str, str]:
    """跟 `openssl req -x509` 產生的一樣：自己簽自己、SAN 是 127.0.0.1。回傳 (cert 檔, key 檔, PEM)。"""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mail.internal.test")])
    now = _dt.datetime.now(_dt.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _dt.timedelta(minutes=5)).not_valid_after(now + _dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), False)
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    cf, kf = tmp_path / "c.pem", tmp_path / "k.pem"
    cf.write_text(pem)
    kf.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    return str(cf), str(kf), pem


class _TlsSmtpServer:
    """最小的 SMTP 伺服器：支援 STARTTLS（587）與 SMTPS（465）兩種，收到一封信就記下來。"""

    def __init__(self, certfile: str, keyfile: str, implicit_tls: bool) -> None:
        self.ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ctx.load_cert_chain(certfile, keyfile)
        self.implicit = implicit_tls
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.got: list[str] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def _one(self, conn: socket.socket) -> None:
        try:
            if self.implicit:
                conn = self.ctx.wrap_socket(conn, server_side=True)
            f = conn.makefile("rwb")

            def say(s: str) -> None:
                f.write((s + "\r\n").encode())
                f.flush()

            say("220 test ESMTP")
            data = False
            buf: list[str] = []
            while True:
                line = f.readline()
                if not line:
                    return
                t = line.decode().rstrip("\r\n")
                if data:
                    if t == ".":
                        data = False
                        self.got.append("\n".join(buf))
                        say("250 queued")
                    else:
                        buf.append(t)
                    continue
                cmd = t.upper()
                if cmd.startswith("EHLO"):
                    say("250-test" if not self.implicit and not isinstance(conn, ssl.SSLSocket) else "250 test")
                    if not self.implicit and not isinstance(conn, ssl.SSLSocket):
                        say("250 STARTTLS")
                elif cmd == "STARTTLS":
                    say("220 go ahead")
                    conn = self.ctx.wrap_socket(conn, server_side=True)
                    f = conn.makefile("rwb")
                elif cmd == "DATA":
                    say("354 end with .")
                    data = True
                elif cmd == "QUIT":
                    say("221 bye")
                    return
                else:
                    say("250 ok")
        except (OSError, ssl.SSLError):
            return
        finally:
            conn.close()

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def no_guard(monkeypatch):
    monkeypatch.setattr(email_service, "_guard", lambda host, port: None)


@pytest.mark.parametrize("mode", ["starttls", "tls"])
def test_self_signed_relay_is_rejected_by_default(tmp_path, no_guard, mode) -> None:
    cf, kf, _pem = _self_signed(tmp_path)
    srv = _TlsSmtpServer(cf, kf, implicit_tls=mode == "tls")
    try:
        with pytest.raises(email_service.EmailSendError) as ei:
            email_service._send_sync_cfg({"smtp_host": "127.0.0.1", "smtp_port": srv.port, "smtp_tls": mode}, _msg())
        assert ei.value.code == "smtp_cert_verify_failed"
        assert srv.got == []
    finally:
        srv.close()


@pytest.mark.parametrize("mode", ["starttls", "tls"])
def test_self_signed_relay_is_trusted_when_its_certificate_is_pasted(tmp_path, no_guard, mode) -> None:
    cf, kf, pem = _self_signed(tmp_path)
    srv = _TlsSmtpServer(cf, kf, implicit_tls=mode == "tls")
    try:
        email_service._send_sync_cfg({"smtp_host": "127.0.0.1", "smtp_port": srv.port, "smtp_tls": mode,
                                      "smtp_ca_cert_pem": pem}, _msg())
        assert len(srv.got) == 1
    finally:
        srv.close()


def test_pasted_certificate_still_checks_the_host_name(tmp_path, no_guard) -> None:
    # 信任了這張憑證，但連的名稱不在 SAN 裡：仍然拒絕（貼 CA 不等於不驗）
    cf, kf, pem = _self_signed(tmp_path)
    srv = _TlsSmtpServer(cf, kf, implicit_tls=False)
    try:
        with pytest.raises(email_service.EmailSendError) as ei:
            email_service._send_sync_cfg({"smtp_host": "localhost", "smtp_port": srv.port, "smtp_tls": "starttls",
                                          "smtp_ca_cert_pem": pem}, _msg())
        assert ei.value.code == "smtp_cert_name_mismatch"
    finally:
        srv.close()


def test_verification_off_still_encrypts(tmp_path, no_guard) -> None:
    cf, kf, _pem = _self_signed(tmp_path)
    srv = _TlsSmtpServer(cf, kf, implicit_tls=False)
    try:
        email_service._send_sync_cfg({"smtp_host": "127.0.0.1", "smtp_port": srv.port, "smtp_tls": "starttls",
                                      "smtp_verify_tls": False}, _msg())
        assert len(srv.got) == 1
    finally:
        srv.close()


async def test_api_stores_validates_and_shows_the_pasted_certificate(client, auth_headers, tmp_path) -> None:
    _cf, _kf, pem = _self_signed(tmp_path)
    r = await client.put("/api/v1/system/notification-channels", headers=auth_headers,
                         json={"smtp_ca_cert_pem": "not a certificate"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "smtp_ca_invalid"
    r = await client.put("/api/v1/system/notification-channels", headers=auth_headers, json={"smtp_ca_cert_pem": pem})
    assert r.status_code == 200
    body = r.json()
    assert body["smtp_ca_cert_pem"].strip() == pem.strip()
    assert body["smtp_ca_cert_subjects"] == ["CN=mail.internal.test"]
    # 清空＝回到系統信任清單
    r = await client.put("/api/v1/system/notification-channels", headers=auth_headers, json={"smtp_ca_cert_pem": ""})
    assert r.status_code == 200 and not r.json()["smtp_ca_cert_pem"] and r.json()["smtp_ca_cert_subjects"] == []


def test_name_mismatch_tells_which_names_the_certificate_has(tmp_path, no_guard) -> None:
    """憑證是發給名稱的、設定卻填 IP（或別的名稱）：錯誤直接講出憑證上的名稱，使用者才知道要改填什麼。

    實際升級時遇過：轉信主機的憑證是 *.example.net 的萬用憑證，SMTP Host 填的卻是 IP（11-25）。"""
    cf, kf, _pem = _self_signed(tmp_path)        # SAN 只有 127.0.0.1
    srv = _TlsSmtpServer(cf, kf, implicit_tls=False)
    try:
        with pytest.raises(email_service.EmailSendError) as ei:
            email_service._send_sync_cfg({"smtp_host": "localhost", "smtp_port": srv.port, "smtp_tls": "starttls",
                                          "smtp_ca_cert_pem": _pem}, _msg())
        assert ei.value.code == "smtp_cert_name_mismatch"
        assert ei.value.params["host"] == "localhost"
        assert "127.0.0.1" in ei.value.params["names"]
    finally:
        srv.close()
