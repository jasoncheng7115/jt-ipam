"""憑證到期 / 飄移告警測試。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.models.certificate import CertAgent, Certificate, CertVersion
from app.models.notification import Notification
from app.services.cert_alert import check_cert_alerts
from sqlalchemy import func, select


async def _cert_version(db_session, *, name, days_to_expiry, fingerprint="f" * 64):
    c = Certificate(name=name)
    db_session.add(c)
    await db_session.flush()
    v = CertVersion(
        certificate_id=c.id, fingerprint_sha256=fingerprint,
        not_after=datetime.now(UTC) + timedelta(days=days_to_expiry),
        cert_pem="x", key_enc=b"x", key_nonce=b"x", is_current=True,
    )
    db_session.add(v)
    await db_session.flush()
    return c


async def _notif_count(db_session) -> int:
    return (await db_session.execute(select(func.count()).select_from(Notification))).scalar_one()


async def test_expiring_cert_notifies_admin_then_dedups(db_session, admin_user):
    await _cert_version(db_session, name=f"c-{uuid.uuid4().hex[:6]}", days_to_expiry=10)
    await db_session.commit()

    s1 = await check_cert_alerts(db_session)
    await db_session.commit()
    assert s1["expiry"] == 1
    assert await _notif_count(db_session) >= 1

    # 再跑一次:去重 → 不再新增
    before = await _notif_count(db_session)
    s2 = await check_cert_alerts(db_session)
    await db_session.commit()
    assert s2["expiry"] == 0
    assert await _notif_count(db_session) == before


async def test_healthy_cert_no_alert(db_session, admin_user):
    await _cert_version(db_session, name=f"ok-{uuid.uuid4().hex[:6]}", days_to_expiry=300)
    await db_session.commit()
    s = await check_cert_alerts(db_session)
    assert s["expiry"] == 0


async def test_drift_detected(db_session, admin_user):
    cur_fp = "a" * 64
    cert = await _cert_version(db_session, name=f"d-{uuid.uuid4().hex[:6]}",
                               days_to_expiry=300, fingerprint=cur_fp)
    # agent 回報的是舊指紋 → 飄移
    agent = CertAgent(name=f"ag-{uuid.uuid4().hex[:6]}", enabled=True,
                      reported=[{"cert": cert.name, "fingerprint": "b" * 64,
                                 "status": "ok", "dry_run": False}])
    db_session.add(agent)
    await db_session.commit()
    s = await check_cert_alerts(db_session)
    await db_session.commit()
    assert s["drift"] == 1


async def test_failed_deployment_counts_as_drift_with_the_agent_message(db_session, admin_user):
    """代理套用失敗時回報的是**新版**的指紋（它想換成的那張），以前因此比對不出來、不發告警；
    這正是「換了但沒成功」的那幾台（2026-11-03 CA 出事、全面換憑證的情境：要知道誰還沒換好）。"""
    cur_fp = "c" * 64
    cert = await _cert_version(db_session, name=f"f-{uuid.uuid4().hex[:6]}",
                               days_to_expiry=300, fingerprint=cur_fp)
    agent = CertAgent(name=f"ag-{uuid.uuid4().hex[:6]}", enabled=True,
                      reported=[{"cert": cert.name, "profile": "nginx", "fingerprint": cur_fp, "status": "failed",
                                 "message": "verify failed: www.example.net:443 presents 0123456789ab…; rolled back",
                                 "dry_run": False}])
    db_session.add(agent)
    await db_session.commit()
    s = await check_cert_alerts(db_session)
    await db_session.commit()
    assert s["drift"] == 1
    n = (await db_session.execute(select(Notification).where(Notification.object_id == cert.id))).scalars().first()
    assert n.title_key == "notif.cert_deploy_failed"
    assert "verify failed" in (n.params or {}).get("msg", "")


async def test_skipped_and_dry_run_reports_are_not_drift(db_session, admin_user):
    cur_fp = "d" * 64
    cert = await _cert_version(db_session, name=f"s-{uuid.uuid4().hex[:6]}",
                               days_to_expiry=300, fingerprint=cur_fp)
    db_session.add(CertAgent(name=f"ag-{uuid.uuid4().hex[:6]}", enabled=True, reported=[
        {"cert": cert.name, "fingerprint": "", "status": "skipped", "dry_run": False},
        {"cert": cert.name, "fingerprint": cur_fp, "status": "failed", "dry_run": True},
    ]))
    await db_session.commit()
    s = await check_cert_alerts(db_session)
    assert s["drift"] == 0
