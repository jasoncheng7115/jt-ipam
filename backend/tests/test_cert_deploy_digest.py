"""憑證更換成功的通知要一批合併成一則（使用者 2026-11-17）。

以前每台代理、每個推送目標一換好就各發一則：憑證到期換新的那個半夜，通知頻道連續跳七、八則「憑證已更換成功」。
現在成功先記下來（cert_deploy_notices），同一張憑證的同一版安靜 QUIET 之後（或最早那筆等太久），
排程合併成一則，列出這次換好的每個目標。
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.models.certificate import CertAgent, CertDeployNotice
from app.services import cert_deploy_digest as digest


@pytest.fixture
def sent(monkeypatch) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    async def fake(_session: Any, **kw: Any) -> int:
        out.append(kw)
        return 1
    monkeypatch.setattr("app.services.notification.notify_admins_event", fake)
    return out


async def _agent(db_session, name: str) -> tuple[CertAgent, str]:
    key = secrets.token_urlsafe(24)
    a = CertAgent(name=f"{name}-{uuid.uuid4().hex[:4]}", enroll_key_hash=hashlib.sha256(key.encode()).hexdigest(),
                  enabled=True, reported=[{"cert": "example.com", "profile": "nginx", "status": "ok",
                                           "fingerprint": "OLD"}])
    db_session.add(a)
    await db_session.commit()
    return a, key


@pytest.mark.anyio
async def test_agent_success_is_recorded_not_sent_right_away(client, db_session, sent) -> None:
    from app.api.v1.endpoints import cert_agents
    assert cert_agents._key_hash("x") == hashlib.sha256(b"x").hexdigest()
    a, key = await _agent(db_session, "web-01")
    r = await client.post("/api/v1/cert-agents/report", headers={"X-Agent-Key": key},
                          json={"deployments": [{"cert": "example.com", "profile": "nginx", "status": "ok",
                                                 "fingerprint": "NEW"}]})
    assert r.status_code == 200, r.text
    assert sent == [], "成功不再一台一則"
    rows = (await db_session.execute(select(CertDeployNotice).where(CertDeployNotice.target == a.name))).scalars().all()
    assert [(n.certificate, n.fingerprint, n.service, n.source, n.notified_at) for n in rows] == [
        ("example.com", "NEW", "nginx", "agent", None)]


@pytest.mark.anyio
async def test_one_notice_per_certificate_after_the_batch_goes_quiet(db_session, sent) -> None:
    cert = f"c{uuid.uuid4().hex[:6]}.example.com"
    now = datetime.now(UTC)
    for i, (t, svc) in enumerate([("web-01", "pdm"), ("pve-01", "pve"), ("proxy-01", "nginx")]):
        await digest.record(db_session, cert=cert, fingerprint="F1", target=t, service=svc, source="agent",
                            at=now - timedelta(minutes=3) + timedelta(seconds=i))
    await db_session.commit()
    assert await digest.flush(db_session, now=now) == 0, "最後一筆還不到 QUIET，可能還有別台在換"
    assert sent == []

    later = now + digest.QUIET
    assert await digest.flush(db_session, now=later) == 1
    assert len(sent) == 1
    n = sent[0]
    assert n["event"] == "cert.deployed"
    assert n["title_key"] == "notif.cert_deployed_batch"
    assert n["params"]["cert"] == cert and n["params"]["n"] == 3
    assert n["params"]["targets"] == "web-01（pdm）、pve-01（pve）、proxy-01（nginx）"
    assert "web-01" in n["body"] and "proxy-01" in n["body"]
    # 送過的不再送
    assert await digest.flush(db_session, now=later + timedelta(hours=1)) == 0
    assert len(sent) == 1


@pytest.mark.anyio
async def test_separate_certificates_and_a_trickle_that_never_stops(db_session, sent) -> None:
    now = datetime.now(UTC)
    a, b = (f"{x}{uuid.uuid4().hex[:6]}.example.com" for x in "ab")
    await digest.record(db_session, cert=a, fingerprint="FA", target="ipam2", service="nginx", source="agent",
                        at=now - timedelta(minutes=30))
    await digest.record(db_session, cert=b, fingerprint="FB", target="fw1", service="opnsense", source="push",
                        at=now - timedelta(minutes=30))
    # c：每隔幾分鐘就有一台換好，一直不安靜 → 最早那筆等滿 MAX_WAIT 也要送
    c = f"c{uuid.uuid4().hex[:6]}.example.com"
    await digest.record(db_session, cert=c, fingerprint="FC", target="h1", service="pve", source="agent",
                        at=now - digest.MAX_WAIT - timedelta(minutes=1))
    await digest.record(db_session, cert=c, fingerprint="FC", target="h2", service="pve", source="agent",
                        at=now - timedelta(seconds=30))
    await db_session.commit()
    assert await digest.flush(db_session, now=now) == 3
    by = {s["params"]["cert"]: s for s in sent}
    assert set(by) == {a, b, c}
    assert by[c]["params"]["n"] == 2
    assert by[b]["params"]["targets"] == "fw1（opnsense）"


@pytest.mark.anyio
async def test_same_target_reported_twice_counts_once(db_session, sent) -> None:
    cert = f"d{uuid.uuid4().hex[:6]}.example.com"
    old = datetime.now(UTC) - timedelta(hours=1)
    for _ in range(2):
        await digest.record(db_session, cert=cert, fingerprint="F", target="log-01", service="nginx", source="agent", at=old)
    await db_session.commit()
    await digest.flush(db_session)
    assert [s["params"]["n"] for s in sent if s["params"]["cert"] == cert] == [1]


@pytest.mark.anyio
async def test_push_success_is_recorded_too(db_session, sent, monkeypatch) -> None:
    from app.models.certificate import Certificate
    from app.models.cert_push_target import CertPushTarget
    from app.services.cert_push import engine
    monkeypatch.setattr("app.core.audit.append_audit", lambda *a, **k: _noop())
    cert = Certificate(name=f"e{uuid.uuid4().hex[:6]}.example.com")
    target = CertPushTarget(name=f"fw-{uuid.uuid4().hex[:4]}", kind="opnsense", api_url="https://192.0.2.1")
    await engine._audit_and_notify(db_session, target, cert, "ok", "FP", None, None, None, "auto")
    assert sent == []
    rows = (await db_session.execute(select(CertDeployNotice).where(CertDeployNotice.target == target.name))).scalars().all()
    assert [(n.certificate, n.service, n.source) for n in rows] == [(cert.name, "opnsense", "push")]


async def _noop() -> None:
    return None
