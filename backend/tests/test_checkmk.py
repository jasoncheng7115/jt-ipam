"""Checkmk 整合（2026-11-14）：網址、位址判讀、同步對應、上線證據、涵蓋缺口、權限。

回應的形狀照 Checkmk 2.4（Raw）與 2.5（Community）實機抓下來的：
- 解析不到 DNS 名稱的主機 `address` 是 `0.0.0.0`，IPv6 的是 `::`，那不是位址
- 額外的位址在 `custom_variables` 的 `ADDRESSES_4_n`；資料夾在 `FILENAME`（`/wato/dc/hosts.mk`）
- guest 角色讀「設定」端點會靜靜回 0 筆，所以只用監控端點
真的打 Checkmk 的測試在 tests/test_checkmk_real.py。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.models.address import IPAddress
from app.models.checkmk import CheckmkHost, CheckmkInstance
from app.models.section import Section
from app.models.subnet import Subnet
from app.services import checkmk as svc


def test_api_base_accepts_what_people_paste() -> None:
    want = "https://cmk.example.net/mysite/check_mk/api/1.0"
    for url in ("https://cmk.example.net/mysite", "https://cmk.example.net/mysite/",
                "https://cmk.example.net/mysite/check_mk/", "https://cmk.example.net/mysite/check_mk/index.py?start_url=x",
                "https://cmk.example.net/mysite/check_mk/api/1.0", "https://cmk.example.net/mysite/check_mk/api/v1/"):
        assert svc.api_base(url) == want, url
    assert svc.api_base("http://192.0.2.5:5000/cmk") == "http://192.0.2.5:5000/cmk/check_mk/api/1.0"


def test_api_base_without_site_says_so() -> None:
    for url in ("https://cmk.example.net", "https://cmk.example.net/check_mk/"):
        with pytest.raises(svc.CheckmkError) as e:
            svc.api_base(url)
        assert e.value.code == "cmk_need_site"


def _h(name: str, address: str, *, state: int = 0, checked: int = 1, extra: dict[str, str] | None = None,
       last_check: int | None = None, checks: int = 1, downtime: int = 0) -> dict[str, Any]:
    cv = {"ADDRESS_4": address if ":" not in address else "", "ADDRESS_6": address if ":" in address else "",
          "FILENAME": "/wato/dc/hosts.mk", **(extra or {})}
    return {"name": name, "alias": name.upper(), "address": address, "state": state, "has_been_checked": checked,
            "last_check": last_check if last_check is not None else int(datetime.now(UTC).timestamp()) - 30,
            "active_checks_enabled": checks, "scheduled_downtime_depth": downtime,
            "plugin_output": "OK - rta 0.05ms", "groups": ["check_mk"],
            "labels": {"cmk/site": "cmk", "env": "prod"}, "custom_variables": cv}


def test_placeholder_addresses_are_not_addresses() -> None:
    assert svc.host_addresses(_h("dnsonly", "0.0.0.0", extra={"ADDRESS_4": "0.0.0.0"})) == (None, [])
    assert svc.host_addresses(_h("v6", "::")) == (None, [])
    ip, allips = svc.host_addresses(_h("sw", "192.0.2.1", extra={"ADDRESSES_4_1": "192.0.2.254", "ADDRESSES_4": "192.0.2.254"}))
    assert ip == "192.0.2.1"
    assert allips == ["192.0.2.1", "192.0.2.254"]
    assert svc._folder(_h("x", "192.0.2.1")) == "/dc"
    assert svc._folder({"custom_variables": {"FILENAME": "/wato/hosts.mk"}}) == "/"


async def _fixture(session) -> tuple[CheckmkInstance, Subnet, Subnet, list[IPAddress]]:
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    session.add(sec)
    await session.flush()
    a = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    b = Subnet(section_id=sec.id, cidr="203.0.113.0/24")
    session.add_all([a, b])
    await session.flush()
    ips = [IPAddress(subnet_id=a.id, ip="198.51.100.10", state="used"),
           IPAddress(subnet_id=a.id, ip="198.51.100.11", state="used"),
           IPAddress(subnet_id=b.id, ip="203.0.113.10", hostname="other", state="used")]
    session.add_all(ips)
    inst = CheckmkInstance(name=f"cmk-{uuid.uuid4().hex[:6]}", api_url="https://cmk.example.net/mysite",
                           api_user="jt-ipam-ro", scope_subnet_ids=[str(a.id)])
    session.add(inst)
    await session.flush()
    return inst, a, b, ips


@pytest.mark.anyio
async def test_sync_links_existing_ips_and_stamps_only_up_hosts(db_session, monkeypatch) -> None:
    inst, a, _b, ips = await _fixture(db_session)
    checked_at = int((datetime.now(UTC) - timedelta(minutes=2)).timestamp())
    hosts = [
        _h("web01", "198.51.100.10", last_check=checked_at),
        _h("db01", "198.51.100.11", state=1),                    # DOWN：不是上線證據
        _h("stale", "198.51.100.99", checks=0),                  # 沒有這個 IP：不新建
        _h("dnsonly.example.net", "0.0.0.0"),
        _h("outside", "203.0.113.10"),                            # 在範圍外：不對應
    ]

    async def fake(_inst: Any, **_k: Any) -> list[dict[str, Any]]:
        return hosts
    monkeypatch.setattr(svc, "list_hosts", fake)
    out = await svc.sync_instance(db_session, inst)
    await db_session.flush()
    assert out == {"hosts": 5, "linked": 2, "removed": 0}

    rows = {r.host_name: r for r in (await db_session.execute(
        select(CheckmkHost).where(CheckmkHost.instance_id == inst.id))).scalars()}
    assert set(rows) == {"web01", "db01", "stale", "dnsonly.example.net", "outside"}
    assert rows["web01"].jt_ipam_address_id == ips[0].id
    assert rows["web01"].state == "up" and rows["web01"].folder == "/dc"
    assert rows["web01"].labels == {"env": "prod"}            # cmk/ 開頭的內建標籤不存
    assert rows["db01"].state == "down"
    assert rows["dnsonly.example.net"].ip is None
    assert rows["outside"].jt_ipam_address_id is None
    assert rows["stale"].checks_enabled is False

    await db_session.refresh(ips[0])
    await db_session.refresh(ips[1])
    # 時間是 Checkmk 實際檢查的時間，不是同步的時間
    assert ips[0].last_seen_checkmk == datetime.fromtimestamp(checked_at, UTC)
    assert ips[1].last_seen_checkmk is None
    n = len((await db_session.execute(select(IPAddress).where(IPAddress.subnet_id == a.id))).all())
    assert n == 2, "只標既有 IP、不新建"

    # 主機名稱來源
    from app.models.ip_hostname import IPHostnameObservation
    obs = (await db_session.execute(select(IPHostnameObservation).where(
        IPHostnameObservation.ip_id == ips[0].id))).scalars().all()
    assert [(o.source, o.hostname) for o in obs] == [("checkmk", "web01")]

    # Checkmk 那邊刪掉的主機，鏡像也刪
    hosts[:] = hosts[:1]
    out = await svc.sync_instance(db_session, inst)
    assert out["removed"] == 4


@pytest.mark.anyio
async def test_pending_or_disabled_checks_are_not_evidence(db_session, monkeypatch) -> None:
    inst, _a, _b, ips = await _fixture(db_session)

    async def fake(_inst: Any, **_k: Any) -> list[dict[str, Any]]:
        return [_h("p", "198.51.100.10", checked=0), _h("off", "198.51.100.11", checks=0)]
    monkeypatch.setattr(svc, "list_hosts", fake)
    await svc.sync_instance(db_session, inst)
    for ip in ips[:2]:
        await db_session.refresh(ip)
        assert ip.last_seen_checkmk is None
    rows = {r.host_name: r.state for r in (await db_session.execute(
        select(CheckmkHost).where(CheckmkHost.instance_id == inst.id))).scalars()}
    assert rows == {"p": "pending", "off": "up"}


@pytest.mark.anyio
async def test_coverage_gap_is_scoped(db_session) -> None:
    inst, a, b, ips = await _fixture(db_session)
    ips[0].hostname = "web01"
    db_session.add(CheckmkHost(instance_id=inst.id, host_name="web01", jt_ipam_address_id=ips[0].id))
    await db_session.flush()
    assert await svc.coverage_gap(db_session, instance_id=inst.id, subnet_ids=[a.id]) == []
    assert [r["hostname"] for r in await svc.coverage_gap(db_session, instance_id=inst.id, subnet_ids=[b.id])] == ["other"]
    assert await svc.coverage_gap(db_session, instance_id=inst.id, subnet_ids=[]) == []


def test_checkmk_is_an_aging_liveness_source_on_by_default() -> None:
    from app.services import evidence
    assert "checkmk" in evidence.LIVENESS_SOURCES
    assert evidence.is_aging("checkmk") is True
    assert "checkmk" in evidence.default_liveness_sources()
    assert "checkmk" in evidence.SOURCES_ADDED_LATER        # 升級的站台自動採信，不用再去勾


@pytest.mark.anyio
async def test_api_secret_is_write_only_and_url_needs_site(client, auth_headers, db_session) -> None:
    r = await client.post("/api/v1/checkmk", headers=auth_headers, json={
        "name": f"cmk-{uuid.uuid4().hex[:6]}", "api_url": "https://cmk.example.net",
        "api_user": "jt-ipam-ro", "api_secret": "s3cret-value"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "cmk_need_site"
    r = await client.post("/api/v1/checkmk", headers=auth_headers, json={
        "name": f"cmk-{uuid.uuid4().hex[:6]}", "api_url": "https://cmk.example.net/mysite/check_mk/",
        "api_user": "jt-ipam-ro", "api_secret": "s3cret-value"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_api_secret"] is True
    assert "s3cret-value" not in r.text
    inst = await db_session.get(CheckmkInstance, uuid.UUID(body["id"]))
    assert svc._secret(inst) == "s3cret-value"
    r = await client.patch(f"/api/v1/checkmk/{body['id']}", headers=auth_headers, json={"description": "x"})
    assert r.status_code == 200
    await db_session.refresh(inst)
    assert svc._secret(inst) == "s3cret-value", "留空＝不變更"
    assert (await client.delete(f"/api/v1/checkmk/{body['id']}", headers=auth_headers)).status_code == 204


@pytest.mark.anyio
async def test_errors_are_readable(monkeypatch) -> None:
    import httpx
    inst = CheckmkInstance(id=uuid.uuid4(), name="x", api_url="https://cmk.example.net/mysite", api_user="ro")
    inst.api_secret_enc, inst.api_secret_nonce = svc.encrypt_api_secret(inst.id, "s")

    def respond(status: int, body: str, ctype: str):
        async def fake(*_a: Any, **_k: Any) -> httpx.Response:
            return httpx.Response(status, text=body, headers={"content-type": ctype})
        monkeypatch.setattr(svc, "safe_request", fake)

    cases = [
        (401, '{"title": "Unauthorized", "status": 401, "detail": "Wrong credentials (Bearer header)"}',
         "application/problem+json", "cmk_auth"),
        (404, "<html><title>404 Not Found</title></html>", "text/html", "cmk_no_site"),
        (403, '{"title": "Forbidden", "status": 403, "detail": "nope"}', "application/problem+json", "cmk_http"),
        (200, "<html>login</html>", "text/html", "cmk_not_json"),
    ]
    for status, body, ctype, code in cases:
        respond(status, body, ctype)
        with pytest.raises(svc.CheckmkError) as e:
            await svc.list_hosts(inst)
        assert e.value.code == code, (status, code)
