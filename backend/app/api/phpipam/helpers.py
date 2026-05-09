"""phpIPAM 相容層共用 helper：response envelope + token 認證。"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import hash_api_token
from app.models.user import APIToken, User


def phpipam_response(
    *,
    data: Any = None,  # noqa: ANN401
    success: bool = True,
    code: int = 200,
    message: str = "",
    started: float | None = None,
) -> dict[str, Any]:
    elapsed = round(time.perf_counter() - started, 4) if started else 0.0
    return {
        "code": code,
        "success": success,
        "data": data,
        "message": message,
        "time": elapsed,
    }


async def phpipam_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """phpIPAM 風格 token：HTTP header `token: <jt_...>`。

    為了相容老腳本，也接 `phpipam-token`、`Authorization: Bearer ...`。
    """
    raw = (
        request.headers.get("token")
        or request.headers.get("phpipam-token")
        or _bearer(request)
    )
    if not raw:
        raise HTTPException(status_code=401, detail="Missing token")

    if not raw.startswith("jt_"):
        raise HTTPException(status_code=401, detail="Invalid token")

    digest = hash_api_token(raw)
    token = (
        await session.execute(select(APIToken).where(APIToken.token_hash == digest))
    ).scalar_one_or_none()
    if token is None or token.revoked_at is not None:
        raise HTTPException(status_code=401, detail="Invalid token")
    from datetime import UTC, datetime
    if token.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=401, detail="Token expired")

    user = await session.get(User, token.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account inactive")
    return user


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def section_to_phpipam(s) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "masterSection": str(s.parent_id) if s.parent_id else "0",
        "strictMode": "1" if s.strict_mode else "0",
        "subnetOrdering": "default",
        "order": s.display_order,
        "editDate": s.updated_at.isoformat() if s.updated_at else None,
        "showVLAN": "0",
        "showVRF": "0",
        "showSupernetOnly": "0",
        "DNS": None,
        "permissions": None,
    }


def subnet_to_phpipam(s) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    cidr = str(s.cidr)
    if "/" in cidr:
        net, mask = cidr.split("/", 1)
    else:
        net, mask = cidr, ""
    return {
        "id": str(s.id),
        "subnet": net,
        "mask": mask,
        "description": s.description,
        "sectionId": str(s.section_id),
        "linked_subnet": None,
        "firewallAddressObject": None,
        "vrfId": str(s.vrf_id) if s.vrf_id else None,
        "masterSubnetId": str(s.master_subnet_id) if s.master_subnet_id else "0",
        "allowRequests": "0",
        "vlanId": str(s.vlan_id) if s.vlan_id else None,
        "showName": "0",
        "device": None,
        "permissions": None,
        "pingSubnet": "1" if s.scan_enabled else "0",
        "discoverSubnet": "0",
        "DNSrecursive": "0",
        "DNSrecords": "0",
        "nameserverId": None,
        "scanAgent": None,
        "isFolder": "1" if s.is_pool else "0",
        "isFull": "1" if s.is_full else "0",
        "tag": None,
        "threshold": str(s.threshold_pct) if s.threshold_pct is not None else "0",
        "location": None,
        "editDate": s.updated_at.isoformat() if s.updated_at else None,
    }


def address_to_phpipam(a) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "id": str(a.id),
        "subnetId": str(a.subnet_id),
        "ip": str(a.ip).split("/")[0],
        "is_gateway": "0",
        "description": a.description,
        "hostname": a.hostname,
        "mac": str(a.mac) if a.mac else None,
        "owner": a.owner,
        "tag": a.state,
        "deviceId": str(a.device_id) if a.device_id else None,
        "port": a.switch_port,
        "note": a.note,
        "lastSeen": a.last_seen_scanner.isoformat() if a.last_seen_scanner else None,
        "excludePing": "1" if a.exclude_from_ping else "0",
        "PTRignore": "1" if a.ptr_ignore else "0",
        "editDate": a.updated_at.isoformat() if a.updated_at else None,
    }
