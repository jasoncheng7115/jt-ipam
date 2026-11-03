"""憑證推送到防火牆：外部審查（2026-11-03）指出、逐條對照程式後確認的問題。

1. 手動與排程同時推同一台：沒有互斥（審查說「自動推送沒接排程」不成立：scripts/jt-ipam-sync.py 每輪呼叫 due_targets）
2. OPNsense：crt_payload 只收一張（上游 CertController：count != 1 就拒）→ 中繼 CA 另外匯進信任庫、憑證只送本體
3. OPNsense：重啟 webgui 回 403 也記成功 → 4xx/5xx 失敗；只有「重啟中斷線」算預期
4. Palo Alto：commit 整份候選設定 → 有別人待提交的變更就不動；指定管理員時用 partial commit
5. Palo Alto：commit 失敗後候選設定仍指向新憑證 → 換回 candidate 再回報失敗；逾時不知道結果就不亂動
6. Palo Alto：不 commit 時被驗證撤回 → 狀態「已寫入待 commit」，不驗證、不撤回
7. FortiGate：換回去沒檢查回應 → 檢查；引擎換回後再連一次確認真的換回
8. pfSense：換管理介面憑證時重啟斷線 → 不當失敗，交給引擎連過去確認，失敗照樣換回
另：FortiGate 同時換管理介面與 SSL VPN 兩個都要確認；只換 SSL VPN 卻沒填確認位址時不可以拿管理介面去判斷
"""
from __future__ import annotations

import base64
import re
from typing import Any

import pytest

from app.services.cert_push import engine
from app.services.cert_push.base import Bundle, PushError, Steps
from app.services.cert_push.drivers import FortiGateDriver, OPNsenseDriver, PaloAltoDriver, PfSenseDriver
from app.services.cert_service import generate_self_signed
from tests.test_cert_push_drivers import FakeFortiGate, FakeOPNsense, FakePanos, FakePfSense, Resp, _bundle, _xml
from tests.test_cert_push_engine import _cert_with_version, _target

pytestmark = pytest.mark.anyio


def _chain_bundle() -> Bundle:
    b = _bundle()
    ca, _k = generate_self_signed("Example Intermediate CA", ["ca.example.com"])
    b.chain_pem = ca
    return b


class StrictOPNsense(FakeOPNsense):
    """照上游的規則：crt_payload 只能一張；有 CA 信任庫（search / get / add）。"""

    def __init__(self) -> None:
        super().__init__()
        self.cas: dict[str, dict[str, str]] = {}
        self.restart_status = 200
        self.restart_raises = False

    async def __call__(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                       content: bytes | None = None, headers: dict | None = None, timeout: float = 30.0) -> Resp:
        if path.startswith("/api/trust/cert/set/"):
            n = len(re.findall("BEGIN CERTIFICATE", (json or {}).get("cert", {}).get("crt_payload", "")))
            if n != 1:
                return Resp(200, {"result": "failed", "validations": {
                    "cert.crt_payload": f"Only a single certificate is allowed (found {n} sections)"}})
        if path == "/api/trust/ca/search":
            return Resp(200, {"rows": [{"uuid": u, "descr": c["descr"]} for u, c in self.cas.items()]})
        m = re.fullmatch(r"/api/trust/ca/get/(.+)", path)
        if m:
            return Resp(200, {"ca": self.cas.get(m.group(1), {})})
        if path == "/api/trust/ca/add":
            ca = json["ca"]
            assert ca["action"] == "existing" and ca["crt_payload"].count("BEGIN CERTIFICATE") == 1
            self.cas[f"ca-{len(self.cas) + 1}"] = {"descr": ca["descr"],
                                                   "crt": base64.b64encode(ca["crt_payload"].encode()).decode()}
            return Resp(200, {"result": "saved"})
        if path == "/api/core/service/restart/webgui":
            if self.restart_raises:
                raise PushError("connect", "certpush_transport", "Server disconnected without sending a response")
            if self.restart_status >= 400:
                return Resp(self.restart_status, {"status": 403, "message": "Forbidden"})
        return await super().__call__(method, path, json=json, params=params, content=content, headers=headers,
                                      timeout=timeout)


# ── 2. OPNsense 憑證鏈 ──

async def test_opnsense_sends_one_certificate_and_imports_the_chain_as_ca_once() -> None:
    dev, b = StrictOPNsense(), _chain_bundle()
    await OPNsenseDriver(dev, {"cert_uuid": "u-1", "restart_webgui": False}).push(b, Steps())
    crt = base64.b64decode(dev.certs["u-1"]["crt"]).decode()
    assert crt.count("BEGIN CERTIFICATE") == 1 and b.cert_pem.strip() in crt
    assert len(dev.cas) == 1, "中繼 CA 匯進信任庫，OPNsense 才能把憑證連到它"
    await OPNsenseDriver(dev, {"cert_uuid": "u-1", "restart_webgui": False}).push(b, Steps())
    assert len(dev.cas) == 1, "同一張 CA 已經在了就不重複匯入"


# ── 3. OPNsense 重啟 ──

async def test_opnsense_restart_forbidden_is_a_failure() -> None:
    dev = StrictOPNsense()
    dev.restart_status = 403
    with pytest.raises(PushError) as e:
        await OPNsenseDriver(dev, {"cert_uuid": "u-1"}).push(_bundle(), Steps())
    assert e.value.step == "restart_webgui"


async def test_opnsense_restart_disconnect_is_expected() -> None:
    dev = StrictOPNsense()
    dev.restart_raises = True
    steps = Steps()
    rollback = await OPNsenseDriver(dev, {"cert_uuid": "u-1"}).push(_bundle(), steps)
    assert rollback is not None
    assert any(s["step"] == "restart_webgui" and "connection closed" in s["detail"] for s in steps.items)


# ── 4–6. Palo Alto ──

class StrictPanos(FakePanos):
    def __init__(self) -> None:
        super().__init__()
        self.others_pending = False
        self.commit_cmds: list[str] = []
        self.job_status = "FIN"

    async def __call__(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                       content: bytes | None = None, headers: dict | None = None, timeout: float = 30.0) -> Resp:
        p = params or {}
        if p.get("type") == "op" and "<pending-changes>" in p.get("cmd", ""):
            return Resp(200, _xml(f"<result>{'yes' if self.others_pending else 'no'}</result>"))
        if p.get("type") == "commit":
            self.commit_cmds.append(p["cmd"])
        if p.get("type") == "op" and "<jobs>" in p.get("cmd", "") and self.job_status != "FIN":
            return Resp(200, _xml(f"<result><job><status>{self.job_status}</status></job></result>"))
        return await super().__call__(method, path, json=json, params=params, content=content, headers=headers,
                                      timeout=timeout)


async def test_paloalto_refuses_when_other_changes_are_pending() -> None:
    dev = StrictPanos()
    dev.others_pending = True
    with pytest.raises(PushError) as e:
        await PaloAltoDriver(dev, {"profile": "gp-portal"}).push(_bundle(), Steps())
    assert e.value.code == "certpush_paloalto_pending_changes"
    assert dev.profile_cert == "old-gp-cert" and not dev.commit_cmds, "什麼都沒動"


async def test_paloalto_partial_commit_for_the_named_admin() -> None:
    dev = StrictPanos()
    await PaloAltoDriver(dev, {"profile": "gp-portal", "commit_admin": "jtipam-api"}).push(_bundle(), Steps())
    assert "<partial><admin><member>jtipam-api</member></admin></partial>" in dev.commit_cmds[0]


async def test_paloalto_failed_commit_reverts_the_candidate() -> None:
    dev = StrictPanos()
    dev.commit_result = "FAIL"
    with pytest.raises(PushError) as e:
        await PaloAltoDriver(dev, {"profile": "gp-portal"}).push(_bundle(), Steps())
    assert e.value.code == "certpush_commit_failed"
    assert dev.profile_cert == "old-gp-cert", "候選設定換回原本的，下次別人 commit 不會意外套上"


async def test_paloalto_commit_timeout_leaves_candidate_alone(monkeypatch) -> None:
    from app.services.cert_push import drivers
    monkeypatch.setattr(drivers, "COMMIT_POLLS", 2)
    monkeypatch.setattr(drivers, "COMMIT_POLL_S", 0)
    dev = StrictPanos()
    dev.job_status = "ACT"
    b = _bundle()
    with pytest.raises(PushError) as e:
        await PaloAltoDriver(dev, {"profile": "gp-portal"}).push(b, Steps())
    assert e.value.code == "certpush_commit_timeout" and "42" in e.value.detail
    assert dev.profile_cert == b.device_name(31), "commit 可能還在跑、結果不知道：不亂改候選設定"


async def test_paloalto_without_commit_is_staged() -> None:
    dev = StrictPanos()
    d = PaloAltoDriver(dev, {"profile": "gp-portal", "commit": False})
    await d.push(_bundle(), Steps())
    assert d.staged is True and not dev.commit_cmds


# ── 7. FortiGate 換回去 ──

async def test_fortigate_rollback_checks_the_response() -> None:
    dev, b = FakeFortiGate(), _bundle()
    rollback = await FortiGateDriver(dev, {"bind": ["admin"]}).push(b, Steps())
    dev.fail_on = "/api/v2/cmdb/system/global"
    with pytest.raises(PushError) as e:
        await rollback()
    assert e.value.step == "rollback"


# ── 8. pfSense 重啟斷線 ──

class DroppingPfSense(FakePfSense):
    async def __call__(self, method: str, path: str, **kw: Any) -> Resp:
        if path == "/api/v2/system/webgui/settings" and method == "PATCH":
            await super().__call__(method, path, **kw)          # 設定其實改了
            raise PushError("connect", "certpush_transport", "Server disconnected")
        return await super().__call__(method, path, **kw)


async def test_pfsense_restart_disconnect_still_returns_rollback() -> None:
    dev, steps = DroppingPfSense(), Steps()
    rollback = await PfSenseDriver(dev, {"bind_webgui": True}).push(_bundle(), steps)
    assert rollback is not None and dev.webgui["sslcertref"].startswith("new")
    assert any(s["step"] == "bind_webgui" and "connection dropped" in s["detail"] for s in steps.items)
    await rollback()
    assert dev.webgui["sslcertref"] == "abc000"


# ── 引擎：多個確認位址、換回後確認、待 commit、互斥 ──

def _probe_by_host(table: dict[tuple[str, int], list[str | None]]):
    its = {k: iter(v) for k, v in table.items()}
    last: dict[tuple[str, int], str | None] = {}

    async def probe(host, port, timeout=8.0):
        k = (host, port)
        try:
            last[k] = next(its[k])
        except StopIteration:
            pass
        return last.get(k)
    return probe


@pytest.fixture
def fg(monkeypatch):
    dev = FakeFortiGate()
    monkeypatch.setattr(engine, "make_http", lambda target, secret: dev)
    monkeypatch.setattr(engine, "VERIFY_INTERVAL_S", 0)
    return dev


async def test_sslvpn_only_without_endpoint_is_unverified_not_rolled_back(client, auth_headers, db_session, fg) -> None:
    cid, fp = await _cert_with_version(client, auth_headers)
    t = await _target(db_session, cid)
    t.options = {"bind": ["sslvpn"]}
    await db_session.commit()
    old = "a" * 64
    r = await engine.push_target(db_session, t, probe=_probe_by_host({("192.0.2.1", 8443): [old]}))
    assert r["status"] == "unverified", "管理介面本來就不會換：不可以拿它判斷 SSL VPN 失敗"
    assert fg.sslvpn["servercert"].startswith("jtipam-")


async def test_admin_and_sslvpn_both_verified_else_rolled_back(client, auth_headers, db_session, fg) -> None:
    cid, fp = await _cert_with_version(client, auth_headers)
    t = await _target(db_session, cid, verify_endpoint="vpn.example.net:10443")
    t.options = {"bind": ["admin", "sslvpn"]}
    await db_session.commit()
    old = "a" * 64
    vpn_takes = {"ok": True}

    async def probe(host, port, timeout=8.0):          # 依設備目前的設定回答它出示哪一張
        if (host, port) == ("192.0.2.1", 8443):
            return fp if fg.global_cfg["admin-server-cert"].startswith("jtipam-") else old
        if (host, port) == ("vpn.example.net", 10443):
            return fp if vpn_takes["ok"] and fg.sslvpn["servercert"].startswith("jtipam-") else old
        return None
    ok = await engine.push_target(db_session, t, probe=probe)
    assert ok["status"] == "ok"
    assert {s["step"] for s in ok["steps"]} >= {"verify"}
    assert sum(1 for s in ok["steps"] if s["step"] == "verify") == 2, "兩個位址各確認一次"
    fg.global_cfg["admin-server-cert"], fg.sslvpn["servercert"] = "self-sign", "Fortinet_Factory"
    vpn_takes["ok"] = False
    bad = await engine.push_target(db_session, t, probe=probe)
    assert bad["status"] == "rolled_back", "SSL VPN 沒換上：整次換回，而且換回後確認兩邊都回到原本那張"
    assert fg.global_cfg["admin-server-cert"] == "self-sign"


async def test_rollback_that_does_not_take_effect_is_reported(client, auth_headers, db_session, fg, monkeypatch) -> None:
    monkeypatch.setattr(engine, "ROLLBACK_TRIES", 2)
    cid, fp = await _cert_with_version(client, auth_headers)
    t = await _target(db_session, cid)
    old, other = "a" * 64, "b" * 64
    # 換上後出示的不是新版（確認失敗）→ 換回 → 但設備還是出示別的，不是原本那張
    r = await engine.push_target(db_session, t, probe=_probe_by_host({("192.0.2.1", 8443): [old] + [other] * 40}))
    assert r["status"] == "error" and r["error"] == "certpush_rollback_unconfirmed"


async def test_paloalto_staged_is_not_verified_or_reverted(client, auth_headers, db_session, monkeypatch) -> None:
    dev = StrictPanos()
    monkeypatch.setattr(engine, "make_http", lambda target, secret: dev)
    cid, fp = await _cert_with_version(client, auth_headers)
    t = await _target(db_session, cid)
    t.kind, t.options = "paloalto", {"profile": "gp-portal", "commit": False}
    await db_session.commit()
    r = await engine.push_target(db_session, t, probe=_probe_by_host({("192.0.2.1", 8443): ["a" * 64]}))
    assert r["status"] == "staged"
    assert dev.profile_cert != "old-gp-cert", "候選設定保留，等管理員自己 commit"


async def test_same_target_is_not_pushed_twice_at_once(client, auth_headers, db_session, fg) -> None:
    from sqlalchemy import text

    from app.core.db import SessionLocal
    cid, fp = await _cert_with_version(client, auth_headers)
    t = await _target(db_session, cid)
    async with SessionLocal() as other:
        await other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": engine.lock_key(t.id)})
        try:
            r = await engine.push_target(db_session, t, probe=_probe_by_host({("192.0.2.1", 8443): [fp]}))
            assert r["status"] == "busy" and r["error"] == "certpush_busy"
            assert t.last_status is None, "沒推就不改紀錄"
        finally:
            await other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": engine.lock_key(t.id)})


# ── 沒填確認位址：Palo Alto、OPNsense 不拿管理介面去判斷（使用者 11-03 同意）──
# 這兩家換的憑證可能給 GlobalProtect、HAProxy 用，管理介面本來就不會換 —— 以前會被誤判失敗而換回。

async def _kind_target(db_session, client, auth_headers, kind: str, options: dict, verify_endpoint: str | None = None):
    cid, fp = await _cert_with_version(client, auth_headers)
    t = await _target(db_session, cid, verify_endpoint=verify_endpoint)
    t.kind, t.options = kind, options
    await db_session.commit()
    return t, fp


async def test_paloalto_without_verify_address_is_unverified(client, auth_headers, db_session, monkeypatch) -> None:
    dev = StrictPanos()
    monkeypatch.setattr(engine, "make_http", lambda target, secret: dev)
    t, fp = await _kind_target(db_session, client, auth_headers, "paloalto", {"profile": "gp-portal"})
    r = await engine.push_target(db_session, t, probe=_probe_by_host({("192.0.2.1", 8443): ["a" * 64]}))
    assert r["status"] == "unverified" and dev.profile_cert != "old-gp-cert", "沒有確認位址：不換回"


async def test_paloalto_with_verify_address_rolls_back_on_failure(client, auth_headers, db_session, monkeypatch) -> None:
    dev = StrictPanos()
    monkeypatch.setattr(engine, "make_http", lambda target, secret: dev)
    monkeypatch.setattr(engine, "VERIFY_INTERVAL_S", 0)
    t, fp = await _kind_target(db_session, client, auth_headers, "paloalto", {"profile": "gp-portal"},
                               verify_endpoint="gp.example.net:443")
    old = "a" * 64
    r = await engine.push_target(db_session, t, probe=_probe_by_host({("gp.example.net", 443): [old] * 40}))
    assert r["status"] == "rolled_back" and dev.profile_cert == "old-gp-cert"


async def test_opnsense_without_verify_address_is_unverified(client, auth_headers, db_session, monkeypatch) -> None:
    dev = StrictOPNsense()
    monkeypatch.setattr(engine, "make_http", lambda target, secret: dev)
    t, fp = await _kind_target(db_session, client, auth_headers, "opnsense", {"cert_uuid": "u-1"})
    before = dev.certs["u-1"]["crt"]
    r = await engine.push_target(db_session, t, probe=_probe_by_host({("192.0.2.1", 8443): ["a" * 64]}))
    assert r["status"] == "unverified" and dev.certs["u-1"]["crt"] != before
