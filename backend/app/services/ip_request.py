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

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
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
) -> None:
    await push_notification(
        session,
        user_id=request.requester_user_id,
        severity=severity,
        title=title,
        body=body,
        link=f"/requests/{request.id}",
        object_type="ip_request",
        object_id=request.id,
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
    return req


async def approve_request(
    session: AsyncSession,
    *,
    request: IPRequest,
    subnet: Subnet,
    approver: User,
) -> IPRequest:
    """approve + 原子配發 IP；失敗回滾整個 transaction。

    若 requested_ip 指定且仍可用則用之；否則挑第一個 free。
    """
    if request.status != "pending":
        raise InvalidStateTransition(f"Cannot approve request in status={request.status}")

    try:
        if request.requested_ip:
            ip_obj = await create_ip(
                session,
                subnet=subnet,
                ip=str(request.requested_ip).split("/")[0],
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
    await _notify_requester(
        session,
        request=request,
        severity="success",
        title=f"IP request approved",
        body=f"Allocated {str(ip_obj.ip).split('/')[0]} for {request.hostname or '(no hostname)'}",
    )
    return request


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
        title="IP request rejected",
        body=reason,
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
