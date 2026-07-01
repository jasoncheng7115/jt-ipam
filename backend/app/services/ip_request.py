"""IP 申請工作流的業務邏輯。

狀態機：
  pending → approve  →  fulfilled (atomic：approve 同時 allocate IP)
  pending → reject   →  rejected
  pending → cancel   →  cancelled (僅 requester 自己)

每次轉換寫一筆 IPRequestEvent + 觸發 webhook event + 站內通知 requester。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ip_request import IPRequest, IPRequestEvent
from app.models.subnet import Subnet
from app.models.user import User
from app.services.address import (
    IPAlreadyExists,
    IPNotInSubnet,
    SubnetFull,
    allocate_first_free,
    create_ip,
)
from app.services.notification import deliver_event, push_notification


class IPRequestError(ValueError):
    pass


class InvalidStateTransition(IPRequestError):
    pass


def _add_event(
    session: AsyncSession,
    *,
    request: IPRequest,
    actor_user_id: uuid.UUID | None,
    event_type: str,
    message: str | None = None,
) -> IPRequestEvent:
    ev = IPRequestEvent(
        request_id=request.id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        message=message,
    )
    session.add(ev)
    return ev


async def _notify_requester(
    session: AsyncSession,
    *,
    request: IPRequest,
    severity: str,
    title: str,
    body: str | None = None,
    event: str = "ip_request.approved",
    title_key: str | None = None,
    body_key: str | None = None,
    params: dict | None = None,
) -> None:
    from app.services.system_config import get_notification_matrix
    ch = (await get_notification_matrix(session)).get(event, {"in_app": True, "email": True})
    if ch.get("in_app"):
        await push_notification(
            session,
            user_id=request.requester_user_id,
            severity=severity,
            title=title,
            body=body,
            title_key=title_key,
            body_key=body_key,
            params=params,
            link=f"/requests/{request.id}",
            object_type="ip_request",
            object_id=request.id,
        )
    if ch.get("email"):
        from app.models.user import User as _U
        from app.services.notification import email_users
        u = await session.get(_U, request.requester_user_id)
        if u and u.email:
            await email_users(session, [u.email], f"[jt-ipam] {title}", body or title)
    from app.services.notify_channels import broadcast_channels
    await broadcast_channels(session, subject=title, text=body)


async def _deliver_to_approvers(
    session: AsyncSession, approvers: list[User], *,
    request: IPRequest, subnet: Subnet, title: str, body: str,
    title_key: str | None = None, body_key: str | None = None, params: dict | None = None,
) -> None:
    """把一則通知送給一組審核人：站內鈴鐺 + （若 Email 管道啟用）寄信。Best-effort。"""
    import logging
    from html import escape as _esc

    from app.core.config import get_settings
    from app.services.email import EmailNotConfigured, send_email_via_config
    from app.services.system_config import get_notification_channels

    log = logging.getLogger("ip_request")
    if not approvers:
        return
    from app.services.system_config import get_notification_matrix
    mx = (await get_notification_matrix(session)).get(
        "ip_request.created", {"in_app": True, "email": True})
    if not (mx.get("in_app") or mx.get("email")):
        return
    link = f"/requests/{request.id}"  # 站內鈴鐺用相對路徑（前端 router 解析）
    # Email 用絕對網址：未登入點了會先被導去登入頁，登入成功再回到此審核頁（router next 機制）
    abs_link = str(get_settings().app_public_url).rstrip("/") + link
    if mx.get("in_app"):
        for u in approvers:
            await push_notification(
                session, user_id=u.id, severity="info", title=title, body=body,
                title_key=title_key, body_key=body_key, params=params,
                link=link, object_type="ip_request", object_id=request.id,
            )
    from app.services.notify_channels import broadcast_channels
    await broadcast_channels(session, subject=title, text=body)
    if not mx.get("email"):
        return
    try:
        ch = await get_notification_channels(session)
    except Exception:
        ch = {}
    if not ch.get("email_enabled"):
        return
    purpose = (request.purpose or "").strip() or "(未填)"
    text = (
        f"{body}\n\n用途：{purpose}\n\n"
        f"點此前往審核（若尚未登入會先導向登入頁，登入後自動返回）：\n{abs_link}\n"
    )
    body_html = (
        f"<div style=\"font-family:system-ui,-apple-system,'Segoe UI',sans-serif;font-size:14px;color:#1f2328\">"
        f"<p>{_esc(body)}</p>"
        f"<p style=\"color:#57606a\">用途：{_esc(purpose)}</p>"
        f"<p style=\"margin:20px 0\">"
        f"<a href=\"{_esc(abs_link)}\" "
        f"style=\"display:inline-block;background:#2f7d4f;color:#fff;text-decoration:none;"
        f"padding:9px 18px;border-radius:6px;font-weight:600\">前往審核</a></p>"
        f"<p style=\"color:#8b949e;font-size:12px\">若尚未登入會先導向登入頁，登入成功後自動返回此審核頁。"
        f"按鈕無法點選時，請複製此連結：<br>{_esc(abs_link)}</p></div>"
    )
    for u in approvers:
        if not u.email:
            continue
        try:
            await send_email_via_config(
                ch, to=u.email, subject=f"[jt-ipam] {title}：{subnet.cidr}",
                body_text=text, body_html=body_html,
            )
        except (EmailNotConfigured, Exception) as exc:
            log.warning("approver email to %s failed: %s", u.email, exc)


async def notify_approvers_new_request(
    session: AsyncSession, *, request: IPRequest, subnet: Subnet, requester: User,
) -> None:
    """新申請送出 → 通知目前該審核的人（單關卡=全部審核人；多關卡 stages=第一關；parallel=全部關卡）。"""
    from app.services.ip_request_policy import approver_users
    approvers = [u for u in await approver_users(session) if u.id != requester.id]
    _who = requester.display_name or requester.username
    _cidr = str(subnet.cidr)
    body = f"{_who} 申請 {_cidr} 的 IP" + (
        f"（{request.hostname}）" if request.hostname else ""
    )
    await _deliver_to_approvers(
        session, approvers, request=request, subnet=subnet, title="IP 申請待審核", body=body,
        title_key="notif.ipreq_pending",
        body_key="notif.ipreq_pending_body" if request.hostname else "notif.ipreq_pending_body_nohost",
        params=({"who": _who, "cidr": _cidr, "host": request.hostname} if request.hostname
                else {"who": _who, "cidr": _cidr}),
    )


async def _notify_stage(
    session: AsyncSession, *, request: IPRequest, subnet: Subnet, step_index: int,
) -> None:
    """依序多關卡：通知「目前這一關」的審核人輪到他們了。"""
    import uuid as _uuid

    from app.models.user import User as _User
    from app.models.user import UserGroupMember
    from app.services.ip_request_policy import _is_uuid, get_policy
    pol = await get_policy(session)
    steps = pol.get("stages") or []
    if step_index >= len(steps):
        return
    step = steps[step_index]
    uids = {_uuid.UUID(x) for x in step["user_ids"] if _is_uuid(x)}
    gids = {_uuid.UUID(x) for x in step["group_ids"] if _is_uuid(x)}
    if gids:
        rows = (await session.execute(
            select(UserGroupMember.user_id).where(UserGroupMember.group_id.in_(gids))
        )).all()
        uids.update(r[0] for r in rows)
    if not uids:
        return
    users = list((await session.execute(
        select(_User).where(_User.id.in_(uids), _User.is_active.is_(True))
    )).scalars().all())
    body = f"申請 {subnet.cidr} 的 IP 已進入「{step['name']}」關卡，待你審核。"
    await _deliver_to_approvers(
        session, users, request=request, subnet=subnet,
        title=f"IP 申請待審核（{step['name']}）", body=body,
        title_key="notif.ipreq_pending_stage",
        body_key="notif.ipreq_pending_stage_body",
        params={"stage": step["name"], "cidr": str(subnet.cidr)},
    )


async def create_request(
    session: AsyncSession,
    *,
    requester: User,
    subnet: Subnet,
    purpose: str,
    hostname: str | None,
    description: str | None,
    requested_ip: str | None,
    expires_at: datetime | None,
) -> IPRequest:
    if not purpose.strip():
        raise IPRequestError("purpose is required")

    req = IPRequest(
        status="pending",
        requester_user_id=requester.id,
        subnet_id=subnet.id,
        requested_ip=requested_ip,
        hostname=hostname,
        description=description,
        purpose=purpose,
        expires_at=expires_at,
    )
    session.add(req)
    await session.flush()
    _add_event(
        session,
        request=req,
        actor_user_id=requester.id,
        event_type="created",
        message=f"Request for {subnet.cidr}",
    )
    await deliver_event(
        session,
        event="ip_request.created",
        payload={
            "id": str(req.id),
            "subnet_id": str(subnet.id),
            "requester": str(requester.id),
            "hostname": hostname,
        },
    )
    # 通知審核人（站內 + Email），best-effort
    await notify_approvers_new_request(session, request=req, subnet=subnet, requester=requester)
    return req


async def approve_request(
    session: AsyncSession,
    *,
    request: IPRequest,
    subnet: Subnet,
    approver: User,
    override_ip: str | None = None,
) -> IPRequest:
    """approve + 原子配發 IP；失敗回滾整個 transaction。

    配發的 IP 優先序：審核人指定的 override_ip > 申請人 requested_ip > 第一個 free。
    審核人可在核准時改成別的 IP（override_ip）。
    """
    if request.status != "pending":
        raise InvalidStateTransition(f"Cannot approve request in status={request.status}")

    chosen_ip = (override_ip or request.requested_ip or "").strip() or None
    try:
        if chosen_ip:
            ip_obj = await create_ip(
                session,
                subnet=subnet,
                ip=str(chosen_ip).split("/")[0],
                hostname=request.hostname,
                description=request.description,
                state="active",
                discovery_source="manual",
            )
        else:
            ip_obj = await allocate_first_free(
                session,
                subnet=subnet,
                hostname=request.hostname,
                description=request.description,
                mac=None,
                state="active",
            )
    except (IPAlreadyExists, IPNotInSubnet, SubnetFull) as exc:
        raise IPRequestError(f"Allocation failed: {exc}") from exc

    # 把申請填的資料寫進配發的 IP：
    #  - 主機名稱走 manual 觀測（最高優先序），否則 create_ip 直接設的欄位會被下次同步 recompute 蓋掉
    #  - 用途（IPAddress 無對應欄）寫進備註，保留申請脈絡
    if request.hostname and request.hostname.strip():
        from app.services.hostname import apply_observation
        await apply_observation(
            session, ip=ip_obj, source="manual", hostname=request.hostname.strip(),
        )
    if request.purpose and request.purpose.strip():
        ip_obj.note = f"用途：{request.purpose.strip()}"

    now = datetime.now(UTC)
    request.status = "fulfilled"
    request.approver_user_id = approver.id
    request.approved_at = now
    request.fulfilled_at = now
    request.allocated_ip_id = ip_obj.id

    _add_event(
        session,
        request=request,
        actor_user_id=approver.id,
        event_type="approved_and_fulfilled",
        message=f"Allocated {str(ip_obj.ip).split('/')[0]}",
    )
    await deliver_event(
        session,
        event="ip_request.fulfilled",
        payload={
            "id": str(request.id),
            "approver": str(approver.id),
            "ip": str(ip_obj.ip).split("/")[0],
            "subnet_id": str(subnet.id),
        },
    )
    _ip_str = str(ip_obj.ip).split("/")[0]
    await _notify_requester(
        session,
        request=request,
        severity="success",
        title="IP 申請已核准",
        body=f"已配發 {_ip_str} 給 {request.hostname or '（無主機名稱）'}",
        event="ip_request.approved",
        title_key="notif.ipreq_approved",
        body_key="notif.ipreq_approved_body" if request.hostname else "notif.ipreq_approved_body_nohost",
        params={"ip": _ip_str, "host": request.hostname} if request.hostname else {"ip": _ip_str},
    )
    return request


async def record_step_approval(
    session: AsyncSession, *, request: IPRequest, subnet: Subnet, approver: User,
    step_index: int, override_ip: str | None = None,
) -> bool:
    """多關卡（parallel / stages）：記錄某一關核准。全部關卡通過則配發 IP 並完成。

    回傳 True=已全數通過並配發；False=尚有關卡待審（已通知下一關）。
    """
    from app.models.ip_request import IPRequestStageApproval
    from app.services.ip_request_policy import (
        approved_step_indices,
        get_policy,
    )

    if request.status != "pending":
        raise InvalidStateTransition(f"Cannot approve request in status={request.status}")

    pol = await get_policy(session)
    steps = pol.get("stages") or []
    if step_index < 0 or step_index >= len(steps):
        raise IPRequestError("invalid approval step")

    session.add(IPRequestStageApproval(
        request_id=request.id, step_index=step_index, approver_user_id=approver.id,
    ))
    await session.flush()
    _add_event(
        session, request=request, actor_user_id=approver.id,
        event_type="stage_approved",
        message=f"關卡「{steps[step_index]['name']}」已核准",
    )

    approved = await approved_step_indices(session, request)
    if approved >= set(range(len(steps))):
        # 全數通過 → 真正配發 IP + 完成
        await approve_request(
            session, request=request, subnet=subnet, approver=approver, override_ip=override_ip,
        )
        return True

    # 還有關卡：依序模式通知下一關（parallel 模式建立時已通知全部關卡）
    if pol["approver_mode"] == "stages":
        pending = sorted(set(range(len(steps))) - approved)
        if pending:
            await _notify_stage(session, request=request, subnet=subnet, step_index=pending[0])
    return False


async def reject_request(
    session: AsyncSession,
    *,
    request: IPRequest,
    approver: User,
    reason: str,
) -> IPRequest:
    if request.status != "pending":
        raise InvalidStateTransition(f"Cannot reject request in status={request.status}")
    if not reason.strip():
        raise IPRequestError("reason is required when rejecting")

    request.status = "rejected"
    request.approver_user_id = approver.id
    request.rejected_at = datetime.now(UTC)
    request.rejected_reason = reason

    _add_event(
        session,
        request=request,
        actor_user_id=approver.id,
        event_type="rejected",
        message=reason,
    )
    await deliver_event(
        session,
        event="ip_request.rejected",
        payload={
            "id": str(request.id),
            "approver": str(approver.id),
            "reason": reason,
        },
    )
    await _notify_requester(
        session,
        request=request,
        severity="warning",
        title="IP 申請已拒絕",
        body=reason,
        event="ip_request.rejected",
        title_key="notif.ipreq_rejected",
        params={},
    )
    return request


async def cancel_request(
    session: AsyncSession,
    *,
    request: IPRequest,
    actor: User,
) -> IPRequest:
    if request.status != "pending":
        raise InvalidStateTransition(f"Cannot cancel request in status={request.status}")
    if request.requester_user_id != actor.id and not actor.is_admin:
        raise IPRequestError("Only the requester (or admin) can cancel")

    request.status = "cancelled"
    request.cancelled_at = datetime.now(UTC)

    _add_event(
        session,
        request=request,
        actor_user_id=actor.id,
        event_type="cancelled",
    )
    return request
