"""憑證更換成功的通知：一批合併成一則（使用者 2026-11-17）。

以前每台代理、每個推送目標一換好就各發一則；憑證到期換新的那個半夜，通知頻道連續跳七、八則。
現在成功先記成 CertDeployNotice，jt-ipam-sync（每 5 分鐘）呼叫 `flush`：同一張憑證的同一版（指紋），
最後一筆已經安靜 QUIET（這一批大概換完了），或最早一筆已經等了 MAX_WAIT（一直有零星的在換），
就合併成一則，列出這次換好的每個目標。失敗（cert.push_failed）照舊立刻通知，不合併。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.certificate import CertDeployNotice

#: 這一批最後一筆之後安靜多久才送（代理多半在同一個整點前後幾分鐘內換好）
QUIET = timedelta(minutes=10)
#: 一直有零星的在換時，最早那筆最多等多久
MAX_WAIT = timedelta(hours=1)
#: 送過的記錄保留多久
KEEP = timedelta(days=30)


async def record(session: AsyncSession, *, cert: str, fingerprint: str, target: str, service: str | None,
                 source: str, at: datetime | None = None) -> None:
    """記下「這個目標換好了」；不 commit（交易由呼叫端決定）。"""
    session.add(CertDeployNotice(
        certificate=cert[:255], fingerprint=fingerprint[:128], target=target[:128],
        service=(service or "")[:64] or None, source=source, created_at=at or datetime.now(UTC)))


def _label(n: CertDeployNotice) -> str:
    return f"{n.target}（{n.service}）" if n.service else n.target


async def flush(session: AsyncSession, *, now: datetime | None = None) -> int:
    """送出已經可以送的批次，回傳送了幾則。會 commit。"""
    from app.services.notification import notify_admins_event

    now = now or datetime.now(UTC)
    pending = (await session.execute(
        select(CertDeployNotice).where(CertDeployNotice.notified_at.is_(None))
        .order_by(CertDeployNotice.created_at))).scalars().all()
    batches: dict[tuple[str, str], list[CertDeployNotice]] = defaultdict(list)
    for n in pending:
        batches[(n.certificate, n.fingerprint)].append(n)

    sent = 0
    for (cert, _fp), rows in batches.items():
        first, last = rows[0].created_at, rows[-1].created_at
        if now - last < QUIET and now - first < MAX_WAIT:
            continue
        labels = list(dict.fromkeys(_label(n) for n in rows))     # 同一個目標回報兩次只算一次
        await notify_admins_event(
            session, event="cert.deployed", severity="info", link="/certificates",
            title=f"憑證已更換成功：{cert}（{len(labels)} 個目標）",
            body=f"「{cert}」的新版憑證已套用到：\n" + "\n".join(f"- {x}" for x in labels),
            title_key="notif.cert_deployed_batch", body_key="notif.cert_deployed_batch_body",
            # 英文介面用半形標點的那份
            params={"cert": cert, "n": len(labels), "targets": "、".join(labels),
                    "targets_en": ", ".join(dict.fromkeys(f"{n.target} ({n.service})" if n.service else n.target
                                                          for n in rows))},
        )
        for n in rows:
            n.notified_at = now
        sent += 1
    await session.execute(delete(CertDeployNotice).where(CertDeployNotice.notified_at < now - KEEP))
    await session.commit()
    return sent
