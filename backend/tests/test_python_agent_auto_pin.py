"""掃描代理、RustDesk 代理不必重裝就改成驗證 jt-ipam（2026-11-07，同 test_cert_agent_auto_pin 的規則）。

兩支 Python 代理常駐在背景：每輪回報前看一次（失敗一小時後再試），驗證過的連線因為憑證失敗時試著跟上
jt-ipam 簽過章的新憑證。學到的存在程式旁邊的 jt-ipam-trust（RustDesk 代理只有程式目錄可寫）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.test_cert_agent_auto_pin import FakeIpam

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"
AGENTS = pytest.mark.parametrize("path", [AGENT_DIR / "jt_ipam_agent.py", AGENT_DIR / "jt_ipam_rustdesk_agent.py"],
                                 ids=["scan", "rustdesk"])


def _load(path: Path, tmp: Path, server: str, *, insecure: bool = False, pin: str = ""):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(f"jt_autopin_{uuid.uuid4().hex[:6]}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.SERVER, mod.KEY, mod.INSECURE, mod.PIN = server, "secret-key", insecure, pin
    mod.TRUST_FILE, mod.TRUST_IDENTITY, mod.AUTO_PIN = str(tmp / "jt-ipam-trust"), None, True
    return mod


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


@AGENTS
def test_insecure_agent_pins_by_itself_without_sending_its_key(path, tmp_path, servers, identity) -> None:
    srv = servers("a", identity)
    a = _load(path, tmp_path, f"https://127.0.0.1:{srv.port}", insecure=True)
    a._trust_maintain()
    assert a._tls_mode() == "pinned" and a.PIN == srv.pin and a.TRUST_IDENTITY is not None
    assert (tmp_path / "jt-ipam-trust").stat().st_mode & 0o077 == 0
    assert all("x-agent-key" not in h for p, h in srv.seen), "還沒確認對面之前不交出金鑰"
    assert a._req("GET", "/x") == {"ok": True}
    assert srv.seen[-1][1]["x-agent-tls"] == "pinned"

    # 重新啟動：從檔案讀回來
    b = _load(path, tmp_path, f"https://127.0.0.1:{srv.port}", insecure=True)
    b._trust_load()
    assert b._tls_mode() == "pinned" and b.PIN == srv.pin and b.TRUST_IDENTITY == a.TRUST_IDENTITY


@AGENTS
def test_ca_is_preferred_when_it_works(path, tmp_path, servers, identity, monkeypatch) -> None:
    srv = servers("a", identity, cn="localhost")
    monkeypatch.setenv("SSL_CERT_FILE", str(srv.pem_path))       # 讓這張自簽憑證算「受信任的 CA」
    a = _load(path, tmp_path, f"https://localhost:{srv.port}", insecure=True)
    a._trust_maintain()
    assert a._tls_mode() == "ca" and a.TRUST_IDENTITY is not None


@AGENTS
@pytest.mark.parametrize("case", ["switched_off", "agent_sees_another_cert"])
def test_stays_as_is(path, tmp_path, servers, identity, case) -> None:
    kw = {"auto_pin": False} if case == "switched_off" else {
        "pin": "sha256//" + __import__("base64").b64encode(hashlib.sha256(b"lb").digest()).decode()}
    srv = servers("a", identity, **kw)
    a = _load(path, tmp_path, f"https://127.0.0.1:{srv.port}", insecure=True)
    a._trust_maintain()
    assert a._tls_mode() == "insecure" and not (tmp_path / "jt-ipam-trust").exists()


@AGENTS
def test_follows_a_signed_certificate_change(path, tmp_path, servers, identity) -> None:
    old = servers("old", identity)
    a = _load(path, tmp_path, f"https://127.0.0.1:{old.port}", pin=old.pin)
    a._trust_maintain()                                     # 1.0.20 起裝的（已釘選）也記下身分
    assert a.TRUST_IDENTITY is not None and a.PIN == old.pin

    new = servers("new", identity)
    a.SERVER = f"https://127.0.0.1:{new.port}"
    with pytest.raises(OSError) as exc:
        a._req("GET", "/x")
    assert a._trust_cert_error(exc.value)
    assert a._trust_recover() is True and a.PIN == new.pin
    assert a._req("GET", "/x") == {"ok": True}


@AGENTS
@pytest.mark.parametrize("case", ["other_identity", "bad_signature", "expired"])
def test_does_not_follow_an_unsigned_change(path, tmp_path, servers, identity, case) -> None:
    old = servers("old", identity)
    a = _load(path, tmp_path, f"https://127.0.0.1:{old.port}", pin=old.pin)
    a._trust_maintain()
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    kw = {"other_identity": {"identity": stranger},
          "bad_signature": {"identity": identity, "sign_with": stranger},
          "expired": {"identity": identity, "not_after": 1_000_000}}[case]
    fake = servers("fake", **kw)
    a.SERVER = f"https://127.0.0.1:{fake.port}"
    assert a._trust_recover() is False and a.PIN == old.pin
    assert all("x-agent-key" not in h for p, h in fake.seen)


def test_installers_forget_what_the_agent_learned() -> None:
    for name in ("jt-ipam-agent-installer.sh", "jt-ipam-rustdesk-agent-installer.sh"):
        assert "jt-ipam-trust" in (AGENT_DIR / name).read_text(), name
