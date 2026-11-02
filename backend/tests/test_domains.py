"""網域管理（客戶許願清單 2026-10-28）：RDAP、DNS 託管推測、API、到期通知。

- 名稱正規化（大小寫、結尾的點、網址、中文網域轉 punycode），不是網域的擋掉
- RDAP：註冊商、註冊日、到期日、NS、狀態；沒有 RDAP 的頂級網域（.io）回 rdap_no_service，到期日用手填的
- 查失敗不清掉上次查到的資料
- 從 NS 推測 DNS 託管（Cloudflare…），手填的優先
- 註冊商網址只收 http / https（畫面會變成連結）
- 寫入只限管理員、留稽核；全域讀取看得到清單
- 到期前 30、14、7、1 天與到期當天各通知一次；續約（到期日變了）重新算
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.models.audit import AuditLog
from app.models.domain import Domain
from app.models.permission import Permission
from app.models.user import User
from app.services import domains as svc
from sqlalchemy import select

BOOT = {"services": [[["com", "net"], ["https://rdap.example-registry.test/com/v1/"]],
                     [["tw"], ["https://ccrdap.example-registry.test/tw/"]]]}


def _rdap(name: str, expires: datetime, ns: list[str]) -> dict[str, Any]:
    return {
        "objectClassName": "domain", "ldhName": name.upper(), "status": ["client transfer prohibited"],
        "events": [{"eventAction": "registration", "eventDate": "2015-03-01T00:00:00Z"},
                   {"eventAction": "expiration", "eventDate": expires.strftime("%Y-%m-%dT%H:%M:%SZ")}],
        "nameservers": [{"ldhName": x.upper()} for x in ns],
        "entities": [{"roles": ["registrar"], "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                                                       ["fn", {}, "text", "Example Registrar"]]]}],
    }


class _Resp:
    def __init__(self, status: int, data: Any) -> None:
        self.status_code, self._data, self.url = status, data, "https://rdap.example-registry.test/"

    def json(self) -> Any:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


@pytest.fixture
def fake_rdap(monkeypatch):  # type: ignore[no-untyped-def]
    """註冊管理機構：example.com 30 天後到期（NS 在 Cloudflare）；其他 .com 查無；連不上用 'down'。"""
    state: dict[str, Any] = {"down": False, "expires": datetime.now(UTC) + timedelta(days=30), "calls": []}

    async def _req(method: str, url: str, **_k: Any) -> _Resp:
        state["calls"].append(url)
        if state["down"]:
            raise OSError("connection refused")
        if url == svc.BOOTSTRAP_URL:
            return _Resp(200, BOOT)
        if url.endswith("/domain/example.com"):
            return _Resp(200, _rdap("example.com", state["expires"], ["ada.ns.cloudflare.com", "bob.ns.cloudflare.com"]))
        if url.endswith("/domain/example.com.tw"):
            return _Resp(200, _rdap("example.com.tw", state["expires"], ["dns1.hinet.net"]))
        return _Resp(404, {"errorCode": 404})

    svc._bootstrap.update(at=0.0, map={})
    monkeypatch.setattr(svc, "safe_request", _req)
    return state


async def _user(db, **kw: Any) -> User:  # type: ignore[no-untyped-def]
    from app.core.security import hash_password
    u = User(username=f"u-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local", display_name="U",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True, **kw)
    db.add(u)
    await db.commit()
    return u


def _hdr(user: User) -> dict[str, str]:
    from app.services.auth import issue_access_token
    return {"Authorization": f"Bearer {issue_access_token(user)}"}


def _code(r) -> str:  # type: ignore[no-untyped-def]
    d = r.json().get("detail")
    return d.get("code") if isinstance(d, dict) else str(d)


# ── 純函式 ──

def test_normalize_domain() -> None:
    assert svc.normalize_domain(" Example.COM. ") == "example.com"
    assert svc.normalize_domain("https://www.example.com/path") == "www.example.com"
    assert svc.normalize_domain("例子.台灣") == "xn--fsqu00a.xn--kpry57d"
    for bad in ("localhost", "exa mple.com", "-bad.com", "example.123", "", "a" * 64 + ".com"):
        with pytest.raises(svc.DomainInputError):
            svc.normalize_domain(bad)


def test_parse_rdap_and_infer_dns_provider() -> None:
    exp = datetime(2027, 8, 13, 4, tzinfo=UTC)
    r = svc.parse_rdap_domain(_rdap("example.com", exp, ["ELLIOTT.NS.CLOUDFLARE.COM", "hera.ns.cloudflare.com."]))
    assert r.registrar == "Example Registrar"
    assert r.expires_at == exp and r.registered_at == datetime(2015, 3, 1, tzinfo=UTC)
    assert r.nameservers == ["elliott.ns.cloudflare.com", "hera.ns.cloudflare.com"]
    assert svc.infer_dns_provider(r.nameservers) == "Cloudflare"
    assert svc.infer_dns_provider(["ns-123.awsdns-45.org"]) == "AWS Route 53"
    assert svc.infer_dns_provider(["ns1.example.net"]) is None


def test_expiry_buckets() -> None:
    assert svc.bucket(45) is None
    assert [svc.bucket(x) for x in (30, 20, 14, 8, 7, 2, 1, 0, -3)] == [30, 30, 14, 14, 7, 7, 1, 0, 0]


# ── API ──

@pytest.mark.anyio
async def test_add_a_domain_fills_rdap_fields(client, auth_headers, db_session, fake_rdap) -> None:
    r = await client.post("/api/v1/domains", headers=auth_headers, json={
        "name": "Example.com", "registrar_account": "acct-001", "services": "郵件（Google Workspace）",
        "registrar_url": "https://registrar.example.net/login"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["name"] == "example.com"
    assert d["registrar_effective"] == "Example Registrar"
    assert d["dns_provider_effective"] == "Cloudflare" and d["dns_provider_inferred"] is True
    assert d["expires_source"] == "rdap" and d["days_left"] in (29, 30)
    assert d["nameservers"] == ["ada.ns.cloudflare.com", "bob.ns.cloudflare.com"]
    # 手填的 DNS 託管優先
    r = await client.patch(f"/api/v1/domains/{d['id']}", headers=auth_headers, json={"dns_provider": "自架"})
    assert r.json()["dns_provider_effective"] == "自架" and r.json()["dns_provider_inferred"] is False
    # 同名再加一次
    r = await client.post("/api/v1/domains", headers=auth_headers, json={"name": "EXAMPLE.COM."})
    assert r.status_code == 409 and _code(r) == "domain_exists"
    rows = (await db_session.execute(select(AuditLog).where(AuditLog.object_type == "domain"))).scalars().all()
    assert {x.action for x in rows} >= {"create", "update"}


@pytest.mark.anyio
async def test_tld_without_rdap_uses_the_manual_date(client, auth_headers, fake_rdap) -> None:
    # 剩幾天以站台時區的「今天」算（APP_TIMEZONE）
    due = (svc.today() + timedelta(days=100)).isoformat()
    r = await client.post("/api/v1/domains", headers=auth_headers, json={"name": "example.io", "expires_at": due})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["rdap_error"] == "rdap_no_service"
    assert d["expires"] == due and d["expires_source"] == "manual" and d["days_left"] == 100


@pytest.mark.anyio
async def test_a_failed_lookup_keeps_the_last_result(client, auth_headers, fake_rdap) -> None:
    d = (await client.post("/api/v1/domains", headers=auth_headers, json={"name": "example.com.tw"})).json()
    assert d["dns_provider_effective"] == "HiNet"
    fake_rdap["down"] = True
    r = await client.post(f"/api/v1/domains/{d['id']}/rdap", headers=auth_headers)
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["rdap_error"] == "rdap_unreachable"
    assert after["expires"] == d["expires"] and after["nameservers"] == ["dns1.hinet.net"]


@pytest.mark.anyio
async def test_input_checks(client, auth_headers, fake_rdap) -> None:
    r = await client.post("/api/v1/domains", headers=auth_headers, json={"name": "not a domain"})
    assert r.status_code == 422 and _code(r) == "domain_invalid"
    r = await client.post("/api/v1/domains", headers=auth_headers,
                          json={"name": "example.net", "registrar_url": "javascript:alert(1)"})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_bulk_add_skips_existing_and_invalid(client, auth_headers, fake_rdap) -> None:
    await client.post("/api/v1/domains", headers=auth_headers, json={"name": "example.com", "lookup": False})
    r = await client.post("/api/v1/domains/bulk", headers=auth_headers,
                          json={"names": ["example.com", "example.net", "nope", "example.net"]})
    assert r.status_code == 200, r.text
    b = r.json()
    assert [x["name"] for x in b["created"]] == ["example.net"]
    assert {(x["value"], x["reason"]) for x in b["skipped"]} == {("example.com", "exists"), ("nope", "invalid"),
                                                                 ("example.net", "duplicate")}


@pytest.mark.anyio
async def test_permissions(client, db_session, fake_rdap) -> None:
    viewer = await _user(db_session)
    db_session.add(Permission(object_type="subnet", object_id=None, principal_type="user",
                              principal_id=viewer.id, level="read"))
    db_session.add(Domain(name="example.org"))
    await db_session.commit()
    r = await client.get("/api/v1/domains", headers=_hdr(viewer))
    assert r.status_code == 200 and [x["name"] for x in r.json()["items"]] == ["example.org"]
    r = await client.post("/api/v1/domains", headers=_hdr(viewer), json={"name": "example.com"})
    assert r.status_code == 403
    scoped = await _user(db_session)      # 只被指派特定物件：看不到全域資料
    r = await client.get("/api/v1/domains", headers=_hdr(scoped))
    assert r.status_code == 403


@pytest.mark.anyio
async def test_detail_lists_certificates_and_zones(client, auth_headers, db_session, fake_rdap) -> None:
    from app.models.certificate import Certificate
    from app.models.dns import DNSServer, DNSZone
    d = (await client.post("/api/v1/domains", headers=auth_headers, json={"name": "example.com"})).json()
    srv = DNSServer(name=f"ns-{uuid.uuid4().hex[:4]}", type="powerdns", enabled=True)
    db_session.add(srv)
    await db_session.flush()
    db_session.add_all([DNSZone(server_id=srv.id, name="example.com", type="forward"),
                        DNSZone(server_id=srv.id, name="other.net", type="forward")])
    cols = {c.name for c in Certificate.__table__.columns}
    kw: dict[str, Any] = {"name": "wild", "domains": ["*.example.com", "example.com"]}
    if "not_after" in cols:
        kw["not_after"] = datetime.now(UTC) + timedelta(days=60)
    db_session.add(Certificate(**{k: v for k, v in kw.items() if k in cols}))
    await db_session.commit()
    r = await client.get(f"/api/v1/domains/{d['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [z["name"] for z in body["zones"]] == ["example.com"]
    assert [c["name"] for c in body["certificates"]] == ["wild"]


# ── 到期通知 ──

@pytest.mark.anyio
async def test_expiry_notifies_each_threshold_once_and_restarts_after_renewal(db_session, admin_user) -> None:
    from app.models.notification import Notification
    d = Domain(name="example.com", rdap_expires_at=datetime.now(UTC) + timedelta(days=10))
    db_session.add(d)
    await db_session.commit()
    assert (await svc.check_domain_expiry(db_session))["notified"] == 1
    await db_session.commit()
    assert (await svc.check_domain_expiry(db_session))["notified"] == 0      # 同一個門檻不重發
    d.rdap_expires_at = datetime.now(UTC) + timedelta(days=5)
    await db_session.commit()
    assert (await svc.check_domain_expiry(db_session))["notified"] == 1      # 進到 7 天門檻
    d.rdap_expires_at = datetime.now(UTC) + timedelta(days=370)               # 續約了
    await db_session.commit()
    assert (await svc.check_domain_expiry(db_session))["notified"] == 0
    rows = (await db_session.execute(select(Notification).where(Notification.object_type == "domain"))).scalars().all()
    assert rows and all(x.link == f"/advanced/domains/{d.id}" for x in rows)


@pytest.mark.anyio
async def test_daily_refresh_checks_the_oldest_first(db_session, fake_rdap) -> None:
    db_session.add_all([Domain(name="example.com"), Domain(name="example.io")])
    await db_session.commit()
    res = await svc.refresh_all(db_session, pause=0)
    assert res == {"checked": 2, "ok": 1, "failed": 1}
