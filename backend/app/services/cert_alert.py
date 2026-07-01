"""憑證到期 / 飄移告警。

- 到期:current 版本 not_after 在 expiry_days 內(或已過期)→ 通知管理員。
- 飄移:某 cert-agent 回報的 fingerprint ≠ 該憑證目前版本 → 代表那台沒套到最新 → 通知。
  這直接守住原始痛點「站台多容易漏掉某一台沒換」。

去重:同一憑證在 dedup_hours 內只發一次(查既有 Notification),所以可安全地每次 sync 都呼叫,不會洗版。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.certificate import CertAgent, Certificate, CertVersion
from app.models.notification import Notification
from app.models.user import User
from app.services.notification import push_notification


async def _recently_notified(session: AsyncSession, cert_id: Any, since: datetime) -> bool:
    row = (await session.execute(
        select(Notification.id).where(
            Notification.object_type == "certificate",
            Notification.object_id == cert_id,
            Notification.created_at >= since,
        ).limit(1)
    )).first()
    return row is not None


async def check_cert_alerts(
    session: AsyncSession, *, expiry_days: int = 21, dedup_hours: int = 20,
) -> dict[str, int]:
    """檢查到期與飄移,對管理員發站內通知。回傳統計。"""
    admins = (await session.execute(
        select(User).where(User.is_admin.is_(True), User.is_active.is_(True))
    )).scalars().all()
    if not admins:
        return {"expiry": 0, "drift": 0}

    from app.services.notification import email_users
    from app.services.system_config import get_notification_matrix
    matrix = await get_notification_matrix(session)
    exp_ch = matrix.get("cert.expiring", {"in_app": True, "email": False})
    drift_ch = matrix.get("cert.drift", {"in_app": True, "email": False})
    admin_emails = [a.email for a in admins]

    now = datetime.now(UTC)
    since = now - timedelta(hours=dedup_hours)
    rows = (await session.execute(
        select(Certificate, CertVersion)
        .join(CertVersion, CertVersion.certificate_id == Certificate.id)
        .where(CertVersion.is_current.is_(True))
    )).all()

    name_fp = {cert.name: ver.fingerprint_sha256 for cert, ver in rows}
    name_id = {cert.name: cert.id for cert, ver in rows}

    expiry = 0
    for cert, ver in rows:
        days = (ver.not_after - now).days
        if days > expiry_days:
            continue
        if not (exp_ch.get("in_app") or exp_ch.get("email")):
            continue
        if await _recently_notified(session, cert.id, since):
            continue
        expired = ver.not_after <= now
        title = f"憑證已過期:{cert.name}" if expired else f"憑證即將到期:{cert.name}"
        body = (f"{cert.name} 於 {ver.not_after.date()} "
                f"{'已過期' if expired else f'到期(剩 {days} 天)'};請上傳新版並讓代理派送。")
        if exp_ch.get("in_app"):
            for a in admins:
                await push_notification(
                    session, user_id=a.id, title=title, body=body,
                    severity="error" if expired else "warning",
                    link="/certificates", object_type="certificate", object_id=cert.id,
                )
        if exp_ch.get("email"):
            await email_users(session, admin_emails, f"[jt-ipam] {title}", body)
        expiry += 1

    drift = 0
    agents = (await session.execute(select(CertAgent))).scalars().all()
    for agent in agents:
        for d in (agent.reported or []):
            if not isinstance(d, dict) or d.get("dry_run"):
                continue
            cname, fp = d.get("cert"), d.get("fingerprint")
            want = name_fp.get(cname)
            cid = name_id.get(cname)
            if not (want and fp and cid) or fp == want:
                continue
            if not (drift_ch.get("in_app") or drift_ch.get("email")):
                continue
            if await _recently_notified(session, cid, since):
                continue
            dtitle = f"憑證飄移:{agent.name} 未套用最新版"
            dbody = (f"代理「{agent.name}」上的「{cname}」指紋為 {str(fp)[:12]}…,"
                     f"並非目前版本 {want[:12]}…;該站台可能沒換成功,請查代理紀錄。")
            if drift_ch.get("in_app"):
                for a in admins:
                    await push_notification(
                        session, user_id=a.id, title=dtitle, body=dbody,
                        severity="warning", link="/certificates",
                        object_type="certificate", object_id=cid,
                    )
            if drift_ch.get("email"):
                await email_users(session, admin_emails, f"[jt-ipam] {dtitle}", dbody)
            drift += 1

    return {"expiry": expiry, "drift": drift}
