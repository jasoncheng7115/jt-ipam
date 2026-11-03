"""戰情室「資訊展示模式」（2026-11-03 使用者：有的單位不允許看到紅字或問題）。

守的是：
1. 設定預設關、只有管理員能改、留稽核
2. 整頁都是問題的四個主題（異常與曝露、採集健康、可用性、AI 巡檢）連清單都不給，直接打 API 也是 404
3. 其餘主題在伺服器端就拿掉問題資料：標題列沒有問題類數字、總覽沒有優先事項、格子不分警告異常、
   服務的憑證只剩總數、統計沒有離線列、關係沒有待處理
4. AI 情報只收中性的事實（沒有異常、事件、資料完整性），模型寫了問題字眼要重寫，WARNING 一律關，
   而且跟一般模式是不同的播報流（切換後不會播到講問題的那一批）
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.models.anomaly_observation import AnomalyObservation
from app.services.warroom.info_mode import HIDDEN_PAGES, PROBLEM_METRICS
from tests.test_warroom import _hdr, _site, _user

pytestmark = pytest.mark.anyio

SHOWN = ("overview", "capacity", "topology", "services", "assets", "stats", "relations", "ai_intel")


@pytest.fixture(autouse=True)
def _no_cache():
    from app.api.v1.endpoints import warroom
    warroom._cache.clear()
    yield
    warroom._cache.clear()


@pytest.fixture(autouse=True)
def _inline(monkeypatch):  # type: ignore[no-untyped-def]
    from app.services.warroom import intel
    monkeypatch.setattr(intel, "SPAWN_IN_BACKGROUND", False)


async def _info(client, auth_headers, on: bool = True) -> None:  # type: ignore[no-untyped-def]
    from app.services.warroom import config as wcfg
    r = await client.put("/api/v1/system/warroom", headers=auth_headers,
                         json={"pages": list(wcfg.PAGES), "info_mode": on})
    assert r.status_code == 200, r.text
    assert r.json()["info_mode"] is on


async def _anomaly(db) -> None:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    db.add(AnomalyObservation(category="ip_conflicts", fingerprint="f" * 16, label="198.51.100.5",
                              ip="198.51.100.5", first_seen_at=now, last_seen_at=now))
    await db.commit()


async def test_setting_defaults_off_is_admin_only_and_audited(client, auth_headers, db_session) -> None:
    from sqlalchemy import select

    from app.models.audit import AuditLog
    assert (await client.get("/api/v1/system/warroom", headers=auth_headers)).json()["info_mode"] is False
    u = await _user(db_session)
    r = await client.put("/api/v1/system/warroom", headers=_hdr(u), json={"info_mode": True})
    assert r.status_code == 403
    await _info(client, auth_headers)
    logs = (await db_session.execute(select(AuditLog).where(AuditLog.object_type == "system_setting"))).scalars().all()
    assert any((x.diff or {}).get("info_mode") is True for x in logs)


async def test_problem_topics_are_not_offered_at_all(client, auth_headers, db_session) -> None:
    await _site(db_session)
    await _info(client, auth_headers)
    cfg = (await client.get("/api/v1/warroom/config", headers=auth_headers)).json()
    assert cfg["info_mode"] is True
    for k in HIDDEN_PAGES:
        assert k not in cfg["pages"] and k not in cfg["all_pages"]
        r = await client.get(f"/api/v1/warroom/pages/{k}", headers=auth_headers)
        assert r.status_code == 404, f"{k} 直接打 API 也拿不到"
    await _info(client, auth_headers, on=False)
    cfg = (await client.get("/api/v1/warroom/config", headers=auth_headers)).json()
    assert "anomalies" in cfg["all_pages"]
    assert (await client.get("/api/v1/warroom/pages/anomalies", headers=auth_headers)).status_code == 200


async def test_shown_topics_carry_no_problem_data(client, auth_headers, db_session) -> None:
    await _site(db_session)
    await _anomaly(db_session)
    await _info(client, auth_headers)
    for k in SHOWN:
        d = (await client.get(f"/api/v1/warroom/pages/{k}", headers=auth_headers)).json()
        assert d.get("info_mode") is True
        assert not set(d.get("metrics") or {}) & PROBLEM_METRICS, f"{k} 標題列還有問題類數字"
    ov = (await client.get("/api/v1/warroom/pages/overview", headers=auth_headers)).json()
    assert ov["priority"] == {"state": "hidden"}
    assert ov["metrics"]["subnets"] == 3 and ov["metrics"]["registered_ips"] == 340
    assert all(c == "ok" for g in ov["groups"] for c in g["cells"]), "有異常、使用率高的子網路也不分顏色"
    assert "198.51.100.5" not in json.dumps(ov), "異常的位址不可以出現在資料裡"
    cap = (await client.get("/api/v1/warroom/pages/capacity", headers=auth_headers)).json()
    assert all(i["level"] is None for i in cap["items"]) and "thresholds" not in cap
    assert cap["metrics"]["subnets"] == 3
    svc = (await client.get("/api/v1/warroom/pages/services", headers=auth_headers)).json()
    assert svc["certs"]["state"] in ("count", "forbidden") and "item" not in svc["certs"]
    assert set(svc["metrics"]) == {"dns_groups", "pools", "certs"}
    st = (await client.get("/api/v1/warroom/pages/stats", headers=auth_headers)).json()
    assert [r["status"] for r in st["status"]["rows"]] == ["online"], "不顯示離線、未回應"
    rel = (await client.get("/api/v1/warroom/pages/relations", headers=auth_headers)).json()
    assert rel["pending"] == {"state": "hidden"}
    assets = (await client.get("/api/v1/warroom/pages/assets", headers=auth_headers)).json()
    assert assets["coverage"] == {"state": "hidden"} and assets["change"] == {"state": "hidden"}


async def test_normal_mode_is_unchanged(client, auth_headers, db_session) -> None:
    await _site(db_session)
    await _anomaly(db_session)
    ov = (await client.get("/api/v1/warroom/pages/overview", headers=auth_headers)).json()
    assert "info_mode" not in ov and ov["metrics"]["open_anomalies"] == 1
    assert ov["priority"]["state"] == "ok"


async def test_intel_only_states_facts_and_runs_on_its_own_stream(client, auth_headers, db_session) -> None:
    from sqlalchemy import select

    from app.models.warroom_intel import WarroomIntelStream
    await _site(db_session)
    await _anomaly(db_session)
    await _info(client, auth_headers, on=False)
    await client.get("/api/v1/warroom/intel", headers=auth_headers)
    normal = (await client.get("/api/v1/warroom/intel", headers=auth_headers)).json()["batch"]
    assert "integrity" in [i["code"] for i in normal["items"]]

    await _info(client, auth_headers)
    await client.get("/api/v1/warroom/intel", headers=auth_headers)
    st = (await client.get("/api/v1/warroom/intel", headers=auth_headers)).json()
    b = st["batch"]
    assert b["id"] != normal["id"], "切到資訊展示模式不可以播一般模式那一批"
    codes = [i["code"] for i in b["items"]]
    assert codes[0] == "overview_info"
    assert not {"integrity", "events_changed", "baseline", "ongoing_anomaly", "anomalies_6h", "ai_audit",
                "collection"} & set(codes)
    assert b["pinned"] == [] and b["sources"] == []
    assert st["last_error_code"] is None and st["stale"] is False
    assert "198.51.100.5" not in json.dumps(b)
    ev = (await client.get("/api/v1/warroom/intel/events", headers=auth_headers)).json()
    assert ev["state"] == "off", "WARNING 一律關"
    m = (await client.get("/api/v1/warroom/pages/ai_intel", headers=auth_headers)).json()["metrics"]
    assert set(m) == {"registered_ips", "subnets"}
    keys = {s.scope_key for s in (await db_session.execute(select(WarroomIntelStream))).scalars()}
    assert len(keys) == 2


async def test_model_using_problem_words_is_rejected(client, auth_headers, db_session, monkeypatch) -> None:
    from tests.test_warroom_intel import _facts_in, _llm
    await _site(db_session)
    await _info(client, auth_headers)

    def reply(prompt, n):  # type: ignore[no-untyped-def]
        assert "資訊展示模式" in prompt
        ov = next(f for f in _facts_in(prompt) if f["category"] == "overview")
        return json.dumps({"items": [{"fact_ids": [ov["id"]], "text": "目前沒有異常，請持續注意告警。"}]})
    calls = _llm(monkeypatch, reply)
    await client.get("/api/v1/warroom/intel", headers=auth_headers)
    b = (await client.get("/api/v1/warroom/intel", headers=auth_headers)).json()["batch"]
    assert len(calls) == 2 and "not allowed on this display" in calls[1]
    assert b["origin"] == "template", "兩次都用了問題字眼：改用系統摘要"


def test_banned_words_cover_the_three_languages() -> None:
    from app.services.warroom.info_mode import banned_in
    assert banned_in("目前有 3 項異常") and banned_in("2 devices offline") and banned_in("障害が発生")
    assert banned_in("共登記 340 個 IP、3 個子網路。") is None
