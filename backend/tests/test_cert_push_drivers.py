"""憑證推送的各家 driver（使用者 2026-10-22：「派送憑證去 fg pa ck opn pf」）。

用模擬設備驗：
- 推得上去、換得上（綁到管理介面／SSL VPN、或替換既有憑證的內容）
- 同一版重推不會失敗（可重複執行：排程會一直推到確認成功為止）
- 換回去的函式真的把設備恢復成原狀（引擎確認失敗時用）
- 設備回錯誤時停在那一步、說得出哪一步，不會繼續往下做
"""
from __future__ import annotations

import base64
import json as _json
import re
from typing import Any

import pytest

from app.services.cert_push.base import Bundle, PushError, Steps
from app.services.cert_push.drivers import FortiGateDriver, OPNsenseDriver
from app.services.cert_service import generate_self_signed

pytestmark = pytest.mark.anyio


def _bundle(name: str = "wildcard-example", cn: str = "www.example.com") -> Bundle:
    cert, key = generate_self_signed(cn, [cn])
    from app.services.cert_service import validate_bundle
    info = validate_bundle(cert, key)
    return Bundle(cert_name=name, fingerprint=info.fingerprint_sha256, cert_pem=cert, chain_pem="", key_pem=key)


class Resp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else _json.dumps(body)

    def json(self) -> Any:
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class FakeFortiGate:
    def __init__(self) -> None:
        self.global_cfg = {"admin-server-cert": "self-sign"}
        self.sslvpn = {"servercert": "Fortinet_Factory"}
        self.local: dict[str, dict[str, str]] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_on: str | None = None

    async def __call__(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                       content: bytes | None = None, headers: dict | None = None, timeout: float = 30.0) -> Resp:
        self.calls.append((method, path))
        if self.fail_on and self.fail_on in path and method != "GET":
            return Resp(500, {"status": "error", "error": -651, "cli_error": "simulated failure"})
        if path == "/api/v2/cmdb/system/global":
            if method == "GET":
                return Resp(200, {"status": "success", "results": dict(self.global_cfg)})
            self.global_cfg.update(json)
            return Resp(200, {"status": "success"})
        if path == "/api/v2/cmdb/vpn.ssl/settings":
            if method == "GET":
                return Resp(200, {"status": "success", "results": dict(self.sslvpn)})
            self.sslvpn.update(json)
            return Resp(200, {"status": "success"})
        if path == "/api/v2/monitor/vpn-certificate/local/import":
            name = json["certname"]
            if name in self.local:
                return Resp(500, {"status": "error", "error": -5})       # duplicate entry
            cert = base64.b64decode(json["file_content"]).decode()
            key = base64.b64decode(json["key_file_content"]).decode()
            assert "BEGIN CERTIFICATE" in cert and "PRIVATE KEY" in key and json["type"] == "regular"
            self.local[name] = {"cert": cert, "key": key}
            return Resp(200, {"status": "success"})
        return Resp(404, {"status": "error", "http_status": 404})


async def test_fortigate_imports_and_binds_admin_and_sslvpn() -> None:
    dev, b = FakeFortiGate(), _bundle()
    steps = Steps()
    rollback = await FortiGateDriver(dev, {"bind": ["admin", "sslvpn"], "vdom": "root"}).push(b, steps)
    name = b.device_name()
    assert name in dev.local and len(name) <= 35
    assert dev.global_cfg["admin-server-cert"] == name
    assert dev.sslvpn["servercert"] == name
    assert all(s["ok"] for s in steps.items)
    # 換回去：兩個服務都回到原本的憑證
    assert rollback is not None
    await rollback()
    assert dev.global_cfg["admin-server-cert"] == "self-sign"
    assert dev.sslvpn["servercert"] == "Fortinet_Factory"


async def test_fortigate_repush_same_version_is_fine() -> None:
    dev, b = FakeFortiGate(), _bundle()
    await FortiGateDriver(dev, {"bind": ["admin"]}).push(b, Steps())
    steps = Steps()
    await FortiGateDriver(dev, {"bind": ["admin"]}).push(b, steps)       # 名稱已存在：當成已匯入
    assert all(s["ok"] for s in steps.items)


async def test_fortigate_import_only_binds_nothing() -> None:
    dev, b = FakeFortiGate(), _bundle()
    rollback = await FortiGateDriver(dev, {"bind": []}).push(b, Steps())
    assert b.device_name() in dev.local
    assert dev.global_cfg["admin-server-cert"] == "self-sign"
    assert rollback is None


async def test_fortigate_stops_at_failed_step() -> None:
    dev, b = FakeFortiGate(), _bundle()
    dev.fail_on = "/api/v2/cmdb/system/global"
    steps = Steps()
    with pytest.raises(PushError) as e:
        await FortiGateDriver(dev, {"bind": ["admin", "sslvpn"]}).push(b, steps)
    assert e.value.step == "bind_admin"
    assert "simulated failure" in e.value.detail
    assert dev.sslvpn["servercert"] == "Fortinet_Factory"                 # 後面的步驟沒有做


class FakeOPNsense:
    def __init__(self) -> None:
        old_crt, old_key = generate_self_signed("fw.example.com", ["fw.example.com"])
        self.certs = {"u-1": {"descr": "Web GUI", "refid": "5f1a2b3c4d5e6", "crt": base64.b64encode(old_crt.encode()).decode(),
                              "prv": base64.b64encode(old_key.encode()).decode()}}
        self.restarts = 0
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                       content: bytes | None = None, headers: dict | None = None, timeout: float = 30.0) -> Resp:
        self.calls.append((method, path))
        m = re.fullmatch(r"/api/trust/cert/(get|set)/(.+)", path)
        if m and m.group(2) in self.certs:
            c = self.certs[m.group(2)]
            if m.group(1) == "get":
                return Resp(200, {"cert": {**c, "action": {"import": {"value": "Import", "selected": 1}}}})
            body = json["cert"]
            if "BEGIN CERTIFICATE" not in body.get("crt_payload", ""):
                return Resp(200, {"result": "failed", "validations": {"cert.crt_payload": "invalid"}})
            c["crt"] = base64.b64encode(body["crt_payload"].encode()).decode()
            c["prv"] = base64.b64encode(body["prv_payload"].encode()).decode()
            return Resp(200, {"result": "saved"})
        if path == "/api/core/service/restart/webgui":
            self.restarts += 1
            return Resp(200, {"result": "ok"})
        if path == "/api/trust/cert/search":
            return Resp(200, {"rows": [{"uuid": u, "descr": c["descr"], "commonname": "fw.example.com"}
                                       for u, c in self.certs.items()]})
        return Resp(404, {"errorMessage": "Endpoint not found"})


async def test_opnsense_replaces_existing_cert_in_place_and_restarts_webgui() -> None:
    dev, b = FakeOPNsense(), _bundle()
    old = dict(dev.certs["u-1"])
    steps = Steps()
    rollback = await OPNsenseDriver(dev, {"cert_uuid": "u-1", "restart_webgui": True}).push(b, steps)
    now = base64.b64decode(dev.certs["u-1"]["crt"]).decode()
    assert b.cert_pem.strip() in now
    assert dev.certs["u-1"]["refid"] == old["refid"]                     # 同一張（引用它的服務都跟著換）
    assert dev.restarts == 1
    await rollback()
    assert dev.certs["u-1"]["crt"] == old["crt"] and dev.certs["u-1"]["prv"] == old["prv"]
    assert dev.restarts == 2


async def test_opnsense_requires_a_target_cert() -> None:
    with pytest.raises(PushError) as e:
        await OPNsenseDriver(FakeOPNsense(), {}).push(_bundle(), Steps())
    assert e.value.code == "certpush_opnsense_no_target"


async def test_opnsense_rejected_payload_reports_validation() -> None:
    dev, b = FakeOPNsense(), _bundle()
    b.cert_pem = "garbage"
    with pytest.raises(PushError) as e:
        await OPNsenseDriver(dev, {"cert_uuid": "u-1"}).push(b, Steps())
    assert e.value.step == "replace" and "crt_payload" in e.value.detail


async def test_opnsense_lists_certificates_for_the_picker() -> None:
    rows = await OPNsenseDriver(FakeOPNsense(), {}).list_device_certs()
    assert len(rows) == 1
    assert {k: rows[0][k] for k in ("id", "name", "subject")} == {"id": "u-1", "name": "Web GUI", "subject": "fw.example.com"}


# ───────────────────────── Palo Alto（XML API） ─────────────────────────
from app.services.cert_push.drivers import PaloAltoDriver, PfSenseDriver  # noqa: E402


def _xml(inner: str, status: str = "success") -> str:
    return f'<response status="{status}">{inner}</response>'


class FakePanos:
    def __init__(self) -> None:
        self.profile_cert = "old-gp-cert"
        self.keypairs: dict[str, str] = {}
        self.certs: dict[str, str] = {}
        self.pending = False
        self.commits = 0
        self.commit_result = "OK"

    async def __call__(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                       content: bytes | None = None, headers: dict | None = None, timeout: float = 30.0) -> Resp:
        p = params or {}
        assert path == "/api/"
        t = p.get("type")
        if t == "import":
            body = (content or b"").decode()
            name = p["certificate-name"]
            if p["category"] == "keypair":
                assert "ENCRYPTED PRIVATE KEY" in body and p.get("passphrase")   # 私鑰一定要加密後才送
                self.keypairs[name] = body
            else:
                self.certs[name] = body
            return Resp(200, _xml("<msg>Successfully imported</msg>"))
        if t == "config" and p.get("action") == "get":
            return Resp(200, _xml(f"<result><certificate>{self.profile_cert}</certificate></result>"))
        if t == "config" and p.get("action") == "set":
            m = re.search(r"<certificate>(.+)</certificate>", p["element"])
            self.profile_cert = m.group(1)
            self.pending = True
            return Resp(200, _xml("<msg>command succeeded</msg>"))
        if t == "commit":
            self.commits += 1
            self.pending = False
            return Resp(200, _xml("<result><job>42</job></result>"))
        if t == "op" and "<pending-changes>" in p.get("cmd", ""):    # 推之前先查有沒有別人待提交的變更
            return Resp(200, _xml("<result>no</result>"))
        if t == "op" and "<jobs>" in p.get("cmd", ""):
            return Resp(200, _xml(f"<result><job><status>FIN</status><result>{self.commit_result}</result>"
                                  "<details><line>Configuration committed</line></details></job></result>"))
        return Resp(200, _xml("<msg>unknown</msg>", "error"))


async def test_paloalto_imports_keypair_sets_profile_and_commits() -> None:
    dev, b = FakePanos(), _bundle()
    steps = Steps()
    rollback = await PaloAltoDriver(dev, {"profile": "gp-portal"}).push(b, steps)
    name = b.device_name(31)
    assert name in dev.keypairs and dev.profile_cert == name and dev.commits == 1
    await rollback()
    assert dev.profile_cert == "old-gp-cert" and dev.commits == 2


async def test_paloalto_commit_failure_is_an_error() -> None:
    dev, b = FakePanos(), _bundle()
    dev.commit_result = "FAIL"
    with pytest.raises(PushError) as e:
        await PaloAltoDriver(dev, {"profile": "gp-portal"}).push(b, Steps())
    assert e.value.step == "commit"


async def test_paloalto_needs_a_profile() -> None:
    with pytest.raises(PushError) as e:
        await PaloAltoDriver(FakePanos(), {}).push(_bundle(), Steps())
    assert e.value.code == "certpush_paloalto_no_profile"


# ───────────────────────── pfSense（pfSense-pkg-RESTAPI v2） ─────────────────────────
class FakePfSense:
    def __init__(self) -> None:
        self.certs: list[dict[str, Any]] = [{"id": 0, "refid": "abc000", "descr": "webConfigurator default"}]
        self.webgui = {"protocol": "https", "port": "443", "sslcertref": "abc000"}

    async def __call__(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                       content: bytes | None = None, headers: dict | None = None, timeout: float = 30.0) -> Resp:
        if path == "/api/v2/system/certificate" and method == "POST":
            assert "BEGIN CERTIFICATE" in json["crt"] and "PRIVATE KEY" in json["prv"]
            row = {"id": len(self.certs), "refid": f"new{len(self.certs):03d}", "descr": json["descr"]}
            self.certs.append(row)
            return Resp(200, {"code": 200, "status": "ok", "data": row})
        if path == "/api/v2/system/certificates" and method == "GET":
            return Resp(200, {"code": 200, "status": "ok", "data": self.certs})
        if path == "/api/v2/system/webgui/settings":
            if method == "GET":
                return Resp(200, {"code": 200, "status": "ok", "data": dict(self.webgui)})
            self.webgui.update(json)
            return Resp(200, {"code": 200, "status": "ok", "data": dict(self.webgui)})
        return Resp(404, {"code": 404, "status": "not found", "message": "endpoint not found"})


async def test_pfsense_creates_cert_and_points_webgui_at_it() -> None:
    dev, b = FakePfSense(), _bundle()
    rollback = await PfSenseDriver(dev, {"bind_webgui": True}).push(b, Steps())
    assert dev.webgui["sslcertref"].startswith("new")
    assert dev.certs[-1]["descr"] == b.device_name(40)
    await rollback()
    assert dev.webgui["sslcertref"] == "abc000"


async def test_pfsense_reuses_the_cert_when_repushed() -> None:
    dev, b = FakePfSense(), _bundle()
    await PfSenseDriver(dev, {"bind_webgui": True}).push(b, Steps())
    n = len(dev.certs)
    steps = Steps()
    await PfSenseDriver(dev, {"bind_webgui": True}).push(b, steps)
    assert len(dev.certs) == n                                         # 同一版不重複建立


async def test_opnsense_picker_shows_status_issuer_and_validity() -> None:
    """下拉清單要看得出狀態與有效日（使用者 10-23）：使用中、使用者憑證、簽發者、有效期間；使用中的排前面。
    valid_to 可能是 epoch 秒數或日期字串（OPNsense 版本不同），一律轉成 ISO 日期。"""
    class Dev:
        async def __call__(self, method, path, **kw):
            assert path == "/api/trust/cert/search"
            return Resp(200, {"rows": [
                {"uuid": "u-old", "descr": "Old GUI", "commonname": "fw.example.com", "in_use": "0", "is_user": "0",
                 "caref": "Internal CA", "valid_from": "1704067200", "valid_to": "1735689600"},
                {"uuid": "u-gui", "descr": "Web GUI", "commonname": "fw.example.com", "in_use": "1", "is_user": "0",
                 "caref": "", "valid_from": "2026-01-01 00:00:00", "valid_to": "2027-01-31 12:00:00"},
                {"uuid": "u-vpn", "descr": "alice", "commonname": "alice", "in_use": "1", "is_user": "1",
                 "caref": "OpenVPN_CA", "valid_to": ""},
            ]})
    rows = await OPNsenseDriver(Dev(), {}).list_device_certs()
    assert [r["id"] for r in rows] == ["u-gui", "u-vpn", "u-old"], "使用中的排前面"
    gui = rows[0]
    assert gui["in_use"] is True
    assert gui["is_user"] is False
    assert gui["valid_to"].startswith("2027-01-31")
    old = rows[2]
    assert old["in_use"] is False
    assert old["issuer"] == "Internal CA"
    assert old["valid_to"].startswith("2025-01-01")
    assert rows[1]["is_user"] is True
    assert rows[1]["valid_to"] is None


async def test_pfsense_lists_device_certs_with_webgui_use_and_validity() -> None:
    """pfSense 也要看得到設備上的憑證（使用者 10-23「pfsense 也看看」）：哪張是網頁管理介面正在用的、
    CN、簽發者、有效期間。pfSense 的 API 不給有效日，從憑證本體解析；crt 可能是 PEM 或 base64 包的 PEM。"""
    import base64
    b1, b2 = _bundle(cn="gui.example.com"), _bundle(cn="old.example.com")
    dev = FakePfSense()
    dev.certs = [{"id": 0, "refid": "r-old", "descr": "old one", "crt": base64.b64encode(b2.cert_pem.encode()).decode()},
                 {"id": 1, "refid": "r-gui", "descr": "jtipam-gui", "crt": b1.cert_pem},
                 {"id": 2, "refid": "r-bad", "descr": "broken", "crt": "garbage"}]
    dev.webgui["sslcertref"] = "r-gui"
    rows = await PfSenseDriver(dev, {}).list_device_certs()
    assert [r["id"] for r in rows] == ["r-gui", "r-old", "r-bad"], "使用中的排前面"
    gui = rows[0]
    assert gui["in_use"] is True
    assert gui["subject"] == "gui.example.com"
    assert gui["valid_to"]
    assert rows[1]["subject"] == "old.example.com"
    assert rows[1]["in_use"] is False
    assert rows[2]["valid_to"] is None                     # 解析不了就不顯示日期，不猜
