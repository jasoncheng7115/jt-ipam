"""憑證推送目標 API（0227）：管理員限定、帳密只寫不讀、輸入驗證、留稽核、立即推送走背景作業。"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.encrypted_secret import EncryptedSecret
from tests.test_cert_push_engine import _cert_with_version

pytestmark = pytest.mark.anyio


def _body(cid: str, **kw) -> dict:
    return {"name": f"fg-{uuid.uuid4().hex[:6]}", "certificate_id": cid, "kind": "fortigate",
            "api_url": "https://192.0.2.1:8443", "options": {"bind": ["admin"]},
            "secret": {"api_key": "super-secret-token"}, **kw}


async def test_create_list_never_returns_the_secret(client, auth_headers) -> None:
    cid, fp = await _cert_with_version(client, auth_headers)
    r = await client.post("/api/v1/cert-push-targets", headers=auth_headers, json=_body(cid))
    assert r.status_code == 201, r.text
    assert r.json()["has_secret"] is True and "super-secret-token" not in r.text
    lst = await client.get("/api/v1/cert-push-targets", headers=auth_headers)
    assert "super-secret-token" not in lst.text
    row = next(i for i in lst.json()["items"] if i["id"] == r.json()["id"])
    assert row["current_fingerprint"] == fp and row["up_to_date"] is False


async def test_validation(client, auth_headers) -> None:
    cid, _ = await _cert_with_version(client, auth_headers)
    for bad, code in [({"kind": "cisco"}, "certpush_unknown_kind"),
                      ({"api_url": "http://192.0.2.1"}, "certpush_https_required"),
                      ({"options": {"bind": ["telnet"]}}, "certpush_bad_option"),
                      ({"options": {"evil": 1}}, "certpush_bad_option"),
                      ({"certificate_id": str(uuid.uuid4())}, "certpush_cert_not_found")]:
        r = await client.post("/api/v1/cert-push-targets", headers=auth_headers, json=_body(cid, **bad))
        assert r.status_code == 422, (bad, r.text)
        assert r.json()["detail"]["code"] == code


async def test_patch_keeps_secret_when_blank_and_delete_removes_it(client, auth_headers, db_session) -> None:
    cid, _ = await _cert_with_version(client, auth_headers)
    tid = (await client.post("/api/v1/cert-push-targets", headers=auth_headers, json=_body(cid))).json()["id"]
    r = await client.patch(f"/api/v1/cert-push-targets/{tid}", headers=auth_headers,
                           json={"auto_push": True, "secret": {"api_key": ""}})
    assert r.status_code == 200 and r.json()["auto_push"] is True and r.json()["has_secret"] is True
    assert (await client.delete(f"/api/v1/cert-push-targets/{tid}", headers=auth_headers)).status_code == 204
    left = (await db_session.execute(select(EncryptedSecret).where(
        EncryptedSecret.object_type == "cert_push_target", EncryptedSecret.object_id == uuid.UUID(tid)))).first()
    assert left is None
    actions = set((await db_session.execute(select(AuditLog.action).where(
        AuditLog.object_id == uuid.UUID(tid)))).scalars())
    assert {"cert_push_target_create", "cert_push_target_update", "cert_push_target_delete"} <= actions


async def test_admin_only(client, auth_headers, db_session) -> None:
    from tests.test_warroom import _hdr, _user
    u = await _user(db_session)
    assert (await client.get("/api/v1/cert-push-targets", headers=_hdr(u))).status_code == 403


async def test_push_now_returns_a_task(client, auth_headers, monkeypatch) -> None:
    # 推送本身換成立刻結束的假推送：以前真的去連測試位址、要等逾時，背景作業拖進下一支測試還握著
    # 資料庫鎖，跟下一支建資料撞成死結（CI v1.0.15：test_device_certs_only_for_opnsense setup deadlock）
    from app.services.cert_push import engine

    async def fake_push(session, target, **_kw):
        return {"status": "ok", "error": None}

    monkeypatch.setattr(engine, "push_target", fake_push)
    cid, _ = await _cert_with_version(client, auth_headers)
    tid = (await client.post("/api/v1/cert-push-targets", headers=auth_headers, json=_body(cid))).json()["id"]
    r = await client.post(f"/api/v1/cert-push-targets/{tid}/push", headers=auth_headers)
    assert r.status_code == 200 and r.json()["task_id"]
    # 等背景作業結束再離開，不留給下一支測試
    import asyncio

    from app.services.background_tasks import _BG_TASKS
    if _BG_TASKS:
        await asyncio.wait_for(asyncio.gather(*list(_BG_TASKS), return_exceptions=True), timeout=10)


async def test_device_certs_only_for_opnsense(client, auth_headers) -> None:
    cid, _ = await _cert_with_version(client, auth_headers)
    tid = (await client.post("/api/v1/cert-push-targets", headers=auth_headers, json=_body(cid))).json()["id"]
    r = await client.get(f"/api/v1/cert-push-targets/{tid}/device-certs", headers=auth_headers)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "certpush_list_unsupported"


async def test_device_certs_preview_before_saving(client, auth_headers, monkeypatch) -> None:
    """新增 OPNsense 目標時就能讀設備上的憑證（使用者 10-22：「替換設備上的哪一張不能選，正常嗎？」——
    以前要先存檔才讀得到，但這欄又是必填）。用表單上的帳密連線；編輯時帳密留空就用已存的。不留任何資料。"""
    from app.services.cert_push import drivers

    seen: list[dict] = []

    async def fake_list(self):
        seen.append({"called": True})
        return [{"id": "u-1", "name": "Web GUI", "subject": "fw.example.net"}]

    monkeypatch.setattr(drivers.OPNsenseDriver, "list_device_certs", fake_list)
    body = {"kind": "opnsense", "api_url": "https://192.0.2.10", "verify_tls": False,
            "secret": {"api_key": "k", "api_secret": "s"}}
    r = await client.post("/api/v1/cert-push-targets/device-certs/preview", headers=auth_headers, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["id"] == "u-1" and seen

    # 沒有帳密、也沒有指定已存的目標 → 422
    r = await client.post("/api/v1/cert-push-targets/device-certs/preview", headers=auth_headers,
                          json={**body, "secret": None})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "certpush_secret_required"
    # 只支援 OPNsense、要 https
    r = await client.post("/api/v1/cert-push-targets/device-certs/preview", headers=auth_headers,
                          json={**body, "kind": "fortigate"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "certpush_list_unsupported"
    r = await client.post("/api/v1/cert-push-targets/device-certs/preview", headers=auth_headers,
                          json={**body, "api_url": "http://192.0.2.10"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "certpush_https_required"


async def test_push_progress_is_visible_while_it_runs(client, auth_headers, monkeypatch) -> None:
    """推送中的作業紀錄即時帶已完成的步驟與目前階段（使用者 11-07：「推送憑證要顯示目前做到那個階段」）；
    做完的結果也帶完整步驟。"""
    import asyncio

    from app.services.background_tasks import _BG_TASKS
    from app.services.cert_push import engine
    go = asyncio.Event()

    async def slow_push(session, target, *, progress=None, **_kw):
        progress([{"step": "probe_before", "ok": True, "detail": "service reachable"}], "push")
        await go.wait()
        steps = [{"step": "probe_before", "ok": True}, {"step": "import", "ok": True}]
        progress(steps, "verify")
        return {"status": "ok", "error": None, "steps": steps}

    monkeypatch.setattr(engine, "push_target", slow_push)
    cid, _ = await _cert_with_version(client, auth_headers)
    tid = (await client.post("/api/v1/cert-push-targets", headers=auth_headers, json=_body(cid))).json()["id"]
    task_id = (await client.post(f"/api/v1/cert-push-targets/{tid}/push", headers=auth_headers)).json()["task_id"]
    running = None
    for _ in range(50):
        running = (await client.get(f"/api/v1/tasks/{task_id}", headers=auth_headers)).json()
        if (running.get("summary") or {}).get("stage"):
            break
        await asyncio.sleep(0.1)
    assert running and running["status"] == "running"
    assert running["summary"]["stage"] == "push" and running["summary"]["steps"][0]["step"] == "probe_before"
    go.set()
    await asyncio.wait_for(asyncio.gather(*list(_BG_TASKS), return_exceptions=True), timeout=10)
    done = (await client.get(f"/api/v1/tasks/{task_id}", headers=auth_headers)).json()
    assert done["status"] == "succeeded"
    assert [s["step"] for s in done["summary"]["steps"]] == ["probe_before", "import"]
