"""憑證推送：推的過程看得到做到哪一步；連不到要寫出原因（使用者 2026-11-07）。

1. 推送是背景作業：引擎每完成一步、每換一個階段就回報，作業紀錄的 summary 即時帶 steps 與 stage，
   右下角「背景作業」面板邊推邊顯示（以前要等全部做完才看得到）
2. 確認位址連不到時寫出底層原因（DNS 解析不到、被拒絕、逾時…）：這次是 DNS 沒設，畫面只寫 unreachable
3. 推之前連不到不打勾（截圖：「✓ 推之前先連一次（… unreachable）」）
"""
from __future__ import annotations

import uuid

import pytest

from app.services.cert_push import engine
from app.services.cert_push.base import Steps
from tests.test_cert_push_engine import _cert_with_version, _probe, _target, fake_fg  # noqa: F401

pytestmark = pytest.mark.anyio


def test_steps_report_every_change() -> None:
    seen: list[tuple[list[str], str | None]] = []
    s = Steps(listener=lambda items, stage: seen.append(([i["step"] for i in items], stage)))
    s.stage("push")
    s.ok("upload")
    s.warn("verify", "certpush_verify_no_address")
    s.fail("bind", "certpush_device_error")
    assert seen == [([], "push"), (["upload"], "push"), (["upload", "verify"], "push"),
                    (["upload", "verify", "bind"], "push")]


def test_listener_errors_never_break_a_push() -> None:
    def boom(items, stage):  # type: ignore[no-untyped-def]
        raise RuntimeError("db down")
    s = Steps(listener=boom)
    s.ok("upload")
    assert s.items[0]["step"] == "upload"


async def test_push_reports_stages_in_order(client, auth_headers, db_session, fake_fg) -> None:  # noqa: F811
    cid, fp = await _cert_with_version(client, auth_headers)
    t = await _target(db_session, cid)
    snaps: list[tuple[int, str | None]] = []
    r = await engine.push_target(db_session, t, probe=_probe(["0" * 64, fp]),
                                 progress=lambda items, stage: snaps.append((len(items), stage)))
    assert r["status"] == "ok"
    stages = [st for _n, st in snaps]
    assert stages.index("probe_before") < stages.index("push") < stages.index("verify")
    assert snaps[-1][0] == len(r["steps"]), "最後一次回報就是完整的步驟"


async def test_unreachable_says_why(client, auth_headers, db_session, fake_fg) -> None:  # noqa: F811
    cid, _fp = await _cert_with_version(client, auth_headers)
    t = await _target(db_session, cid)

    async def probe(host, port, timeout=8.0):  # type: ignore[no-untyped-def]
        engine._PROBE_ERRORS[(host, port)] = "DNS resolution failed for router.example.net"
        return None
    r = await engine.push_target(db_session, t, probe=probe)
    assert r["status"] == "unverified"
    before = next(s for s in r["steps"] if s["step"] == "probe_before")
    assert before.get("warn") and before["code"] == "certpush_probe_unreachable", "連不到不打勾"
    assert "DNS resolution failed" in before["detail"]
    verify = next(s for s in r["steps"] if s["step"] == "verify")
    assert "DNS resolution failed" in verify["detail"]


async def test_real_probe_records_the_reason() -> None:
    host = f"no-such-{uuid.uuid4().hex[:8]}.invalid"
    assert await engine.presented_fingerprint(host, 443, timeout=3) is None
    assert "DNS" in engine.probe_error(host, 443)


async def test_task_progress_is_written_only_while_running(db_session) -> None:
    from app.models.background_task import BackgroundTask
    from app.services.background_tasks import update_task_progress
    task = BackgroundTask(kind="cert.push", status="running", trigger="manual", progress=0)
    db_session.add(task)
    await db_session.commit()
    await update_task_progress(task.id, {"steps": [{"step": "upload", "ok": True}], "stage": "push"})
    await db_session.refresh(task)
    assert task.summary["stage"] == "push"
    task.status, task.summary = "succeeded", {"status": "ok"}
    await db_session.commit()
    await update_task_progress(task.id, {"stage": "late"})
    await db_session.refresh(task)
    assert task.summary == {"status": "ok"}, "做完之後晚到的進度不可以蓋掉最終結果"
