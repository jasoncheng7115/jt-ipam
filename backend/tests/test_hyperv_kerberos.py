"""Hyper-V 的 Kerberos 驗證（使用者 2026-10-20：「也要支援 Kerberos，客戶有用」）。

做法見 services/winrm_kerberos：kinit 拿帳密換 TGT 放進這次專用的快取，連線期間把環境變數指過去，結束還原並刪掉。
單元測試用假的 kinit 與假的 WinRM；最後一支連真的 KDC（`scripts/kerberos-test-kdc/run.sh` 起的 Samba AD DC 容器），沒設環境變數時跳過：

    JTIPAM_TEST_KDC=kdc.corp.test JTIPAM_TEST_KRB_USER=svc-ipam@CORP.TEST JTIPAM_TEST_KRB_PASS=… \\
    JTIPAM_TEST_KRB_SPN_HOST=hv1.corp.test pytest tests/test_hyperv_kerberos.py
"""
from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from app.services import hyperv
from app.services import winrm_kerberos as krb

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(("user", "realm", "want"), [
    ("svc-ipam@corp.example", None, ("svc-ipam@CORP.EXAMPLE", "CORP.EXAMPLE")),
    ("CORP\\svc-ipam", "corp.example", ("svc-ipam@CORP.EXAMPLE", "CORP.EXAMPLE")),
    ("svc-ipam", "CORP.EXAMPLE", ("svc-ipam@CORP.EXAMPLE", "CORP.EXAMPLE")),
    ("svc-ipam@corp.example", "other.example", ("svc-ipam@OTHER.EXAMPLE", "OTHER.EXAMPLE")),   # 填了領域就用填的
])
def test_principal(user: str, realm: str | None, want: tuple[str, str]) -> None:
    assert krb.principal_for(user, realm) == want


@pytest.mark.parametrize(("user", "realm"), [("CORP\\svc", None), ("svc", None), ("a b@corp.example", None),
                                             ("svc;rm@corp.example", None)])
def test_principal_rejected(user: str, realm: str | None) -> None:
    with pytest.raises(krb.KerberosError):
        krb.principal_for(user, realm)


def test_fqdn_required() -> None:
    krb.require_fqdn("hv1.corp.example")
    for bad in ("10.0.0.5", "hv1", "fe80::1"):
        with pytest.raises(krb.KerberosError) as e:
            krb.require_fqdn(bad)
        assert e.value.code == "kerberos_needs_fqdn"


def test_krb5_conf() -> None:
    dns = krb.krb5_conf("CORP.EXAMPLE", [])
    assert "dns_lookup_kdc = true" in dns
    assert "[realms]" not in dns
    fixed = krb.krb5_conf("CORP.EXAMPLE", ["dc1.corp.example", "dc2.corp.example"])
    assert "dns_lookup_kdc = false" in fixed
    assert "kdc = dc1.corp.example" in fixed and "kdc = dc2.corp.example" in fixed
    assert "dns_canonicalize_hostname = false" in fixed and "rdns = false" in fixed
    assert krb.parse_kdcs("dc1.corp.example,\ndc2.corp.example dc1.corp.example") == ["dc1.corp.example",
                                                                                       "dc2.corp.example"]
    with pytest.raises(krb.KerberosError):
        krb.parse_kdcs("dc1;reboot")


def _fake_kinit(monkeypatch, rc: int = 0, stderr: str = "") -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def run(argv, **kw):  # type: ignore[no-untyped-def]
        calls.append({"argv": argv, **kw, "conf": open(kw["env"]["KRB5_CONFIG"]).read()})
        return SimpleNamespace(returncode=rc, stdout="", stderr=stderr)

    monkeypatch.setattr(krb, "missing", lambda: [])
    monkeypatch.setattr(krb.subprocess, "run", run)
    return calls


def test_ticket_scopes_the_environment(monkeypatch) -> None:
    monkeypatch.delenv("KRB5CCNAME", raising=False)
    monkeypatch.setenv("KRB5_CONFIG", "/etc/krb5.conf")
    calls = _fake_kinit(monkeypatch)
    with krb.ticket("svc-ipam@corp.example", "S3cret!", kdcs=["dc1.corp.example"]) as principal:
        assert principal == "svc-ipam@CORP.EXAMPLE"
        cache = os.environ["KRB5CCNAME"]
        tmpdir = os.path.dirname(cache.removeprefix("FILE:"))
        assert os.path.isdir(tmpdir)
        assert oct(os.stat(tmpdir).st_mode & 0o777) == "0o700"
    assert "KRB5CCNAME" not in os.environ                         # 還原
    assert os.environ["KRB5_CONFIG"] == "/etc/krb5.conf"
    assert not os.path.exists(tmpdir)                             # 快取與設定檔刪掉
    c = calls[0]
    assert "S3cret!" not in " ".join(c["argv"])                   # 密碼不在參數裡（ps 看得到）
    assert c["input"] == "S3cret!\n"
    assert c["argv"][-1] == "svc-ipam@CORP.EXAMPLE"
    assert "kdc = dc1.corp.example" in c["conf"]


@pytest.mark.parametrize(("stderr", "code"), [
    ("kinit: Password incorrect while getting initial credentials", "kerberos_auth"),
    ("kinit: Client 'x@CORP.EXAMPLE' not found in Kerberos database while getting initial credentials",
     "kerberos_principal"),
    ("kinit: Cannot find KDC for realm \"CORP.EXAMPLE\" while getting initial credentials", "kerberos_kdc"),
    ("kinit: Clock skew too great while getting initial credentials", "kerberos_clock"),
    ("kinit: something else", "kerberos_error"),
    # 客戶 2026-10-20：帳號只有舊的 RC4 金鑰、網域控制站停用 RC4 → 要講出怎麼修，不只轉述原文
    ("kinit: KDC has no support for encryption type while getting initial credentials", "kerberos_enctype"),
])
def test_kinit_errors(monkeypatch, stderr: str, code: str) -> None:
    _fake_kinit(monkeypatch, rc=1, stderr=stderr)
    with pytest.raises(krb.KerberosError) as e, krb.ticket("svc@corp.example", "x"):
        pass
    assert e.value.code == code
    assert "KRB5CCNAME" not in os.environ or not os.environ["KRB5CCNAME"].startswith("FILE:/tmp/jt-ipam-krb-")


def test_unavailable_server(monkeypatch) -> None:
    monkeypatch.setattr(krb, "missing", lambda: ["krb5-user", "pykerberos"])
    with pytest.raises(krb.KerberosError) as e, krb.ticket("svc@corp.example", "x"):
        pass
    assert e.value.code == "kerberos_unavailable"
    assert e.value.params["missing"] == "krb5-user, pykerberos"


def test_hyperv_client_uses_kerberos(monkeypatch) -> None:
    import contextlib
    got: list[dict[str, Any]] = []

    class _S:
        def __init__(self, **kw: Any) -> None:
            got.append({**kw, "ccache": os.environ.get("KRB5CCNAME")})

        def run_ps(self, script: str) -> Any:
            return SimpleNamespace(status_code=0, std_out=b'{"host": "HV1"}', std_err=b"")

    @contextlib.contextmanager
    def _ticket(username, password, *, realm=None, kdcs=None):  # type: ignore[no-untyped-def]
        assert kdcs == ["dc1.corp.example"]
        os.environ["KRB5CCNAME"] = "FILE:/tmp/fake"
        try:
            yield "svc-ipam@CORP.EXAMPLE"
        finally:
            os.environ.pop("KRB5CCNAME", None)

    monkeypatch.setitem(sys.modules, "winrm", SimpleNamespace(Session=_S))
    monkeypatch.setattr("app.core.net_guard.check_target", lambda *a, **k: [])
    monkeypatch.setattr(krb, "ticket", _ticket)
    cli = hyperv.HyperVClient(host="hv1.corp.example", username="svc-ipam@corp.example", password="p",
                              auth_method="kerberos", kerberos_kdc="dc1.corp.example")
    assert cli.run_json(hyperv._PING_SCRIPT) == {"host": "HV1"}
    assert got[0]["transport"] == "kerberos"
    assert got[0]["auth"][0] == "svc-ipam@CORP.EXAMPLE"
    assert got[0]["ccache"] == "FILE:/tmp/fake"                   # 連線時票已經在快取裡
    assert got[0]["message_encryption"] == "always"               # HTTP 一樣要加密


def test_hyperv_client_rejects_ip_with_kerberos(monkeypatch) -> None:
    monkeypatch.setattr("app.core.net_guard.check_target", lambda *a, **k: [])
    with pytest.raises(hyperv.HyperVError) as e:
        hyperv.HyperVClient(host="10.0.0.5", username="svc@corp.example", password="p", auth_method="kerberos")
    assert e.value.code == "kerberos_needs_fqdn"


def test_kerberos_error_reaches_the_diagnosis_with_its_code(monkeypatch) -> None:
    monkeypatch.setattr("app.core.net_guard.check_target", lambda *a, **k: [])
    _fake_kinit(monkeypatch, rc=1, stderr="kinit: Password incorrect while getting initial credentials")
    monkeypatch.setitem(sys.modules, "winrm", SimpleNamespace(Session=lambda **kw: None))
    cli = hyperv.HyperVClient(host="hv1.corp.example", username="svc@corp.example", password="bad",
                              auth_method="kerberos")
    with pytest.raises(hyperv.HyperVError) as e:
        cli.run_json(hyperv._PING_SCRIPT)
    assert e.value.code == "kerberos_auth"
    assert e.value.params["host"] == "hv1.corp.example"


# ── API ──

def _payload(**kw: Any) -> dict[str, Any]:
    return {"name": f"hvk-{uuid.uuid4().hex[:6]}", "mode": "hosts", "host": "hv1.corp.example", "extra_hosts": None,
            "port": 5985, "use_ssl": False, "verify_tls": True, "username": "svc-ipam@corp.example",
            "password": "S3cret-2026", "auth_method": "kerberos", "kerberos_realm": None,
            "kerberos_kdc": "dc1.corp.example", "enabled": True, "sync_interval_seconds": 600,
            "scope_subnet_ids": None, "auto_create_ips": False, "description": None, **kw}


async def test_api_kerberos_fields(client, auth_headers) -> None:
    r = await client.post("/api/v1/hyperv", json=_payload(), headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["auth_method"], body["kerberos_kdc"]) == ("kerberos", "dc1.corp.example")
    assert (await client.post("/api/v1/hyperv", json=_payload(host="10.0.0.5"),
                              headers=auth_headers)).status_code == 422
    assert (await client.post("/api/v1/hyperv", json=_payload(extra_hosts="hv2"),
                              headers=auth_headers)).status_code == 422
    # 部分更新：只改主機成 IP，schema 看不到驗證方式 —— 存檔前再檢查一次
    r = await client.patch(f"/api/v1/hyperv/{body['id']}", json={"host": "10.0.0.5"}, headers=auth_headers)
    assert r.status_code == 422
    r = await client.patch(f"/api/v1/hyperv/{body['id']}", json={"auth_method": "ntlm", "host": "10.0.0.5"},
                           headers=auth_headers)
    assert r.status_code == 200


async def test_api_capabilities(client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(krb, "missing", lambda: ["pykerberos"])
    r = await client.get("/api/v1/hyperv/capabilities", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"kerberos": False, "missing": ["pykerberos"]}


# ── 真的 KDC（選用）──

@pytest.mark.skipif(not os.environ.get("JTIPAM_TEST_KDC"), reason="需要 JTIPAM_TEST_KDC（Samba AD DC 容器）")
def test_real_kdc_ticket_and_service_token() -> None:
    import importlib
    kerberos = importlib.import_module("kerberos")
    user, pw = os.environ["JTIPAM_TEST_KRB_USER"], os.environ["JTIPAM_TEST_KRB_PASS"]
    spn_host = os.environ.get("JTIPAM_TEST_KRB_SPN_HOST", "hv1.corp.test")
    with krb.ticket(user, pw, kdcs=[os.environ["JTIPAM_TEST_KDC"]]):
        _rc, ctx = kerberos.authGSSClientInit(f"HTTP@{spn_host}",
                                             gssflags=kerberos.GSS_C_MUTUAL_FLAG | kerberos.GSS_C_SEQUENCE_FLAG)
        kerberos.authGSSClientStep(ctx, "")
        assert len(kerberos.authGSSClientResponse(ctx) or "") > 100   # 拿得到 HTTP/<主機> 的服務票
    with pytest.raises(krb.KerberosError) as e, krb.ticket(user, pw + "-wrong", kdcs=[os.environ["JTIPAM_TEST_KDC"]]):
        pass
    assert e.value.code == "kerberos_auth"


def test_enctype_error_with_a_netbios_realm_says_so(monkeypatch) -> None:
    """領域填成 NetBIOS 名稱（CORP）而不是 AD 的 DNS 網域名稱（CORP.EXAMPLE）時，訊息另外點出來。"""
    _fake_kinit(monkeypatch, rc=1, stderr="kinit: KDC has no support for encryption type while getting initial credentials")
    with pytest.raises(krb.KerberosError) as e, krb.ticket("corp\\administrator", "x", realm="CORP"):
        pass
    assert e.value.code == "kerberos_enctype_netbios"
    assert e.value.params["realm"] == "CORP"
