"""RIPE / TWNIC whois 匯入端點。

行為：
- POST /api/v1/import/ripe/preview     — 上傳 whois 文字，回傳即將建立的 subnet 計畫
- POST /api/v1/import/ripe/commit      — 真正寫入指定 section（idempotent，CIDR 已存在則 skip）
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.ripe_twnic import planify
from app.services.subnet import (
    SubnetOverlap,
    assert_no_overlap,
    compute_master_subnet,
)

router = APIRouter(prefix="/import", tags=["import"])

_MAX_BYTES = 2 * 1024 * 1024  # 2MB


async def _read_text(file: UploadFile) -> str:
    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(413, detail="File too large (max 2 MB)")
    return raw.decode("utf-8-sig", errors="replace")


@router.post("/ripe/preview", dependencies=[Depends(require_admin)])
async def preview_ripe(
    file: Annotated[UploadFile, File()],
    _user: CurrentUser,
) -> dict[str, object]:
    """預覽：解析 whois 內容，列出即將建立的 subnet（不寫入）。"""
    text = await _read_text(file)
    plans = planify(text)
    return {
        "count": len(plans),
        "plans": [
            {
                "cidr": p.cidr,
                "description": p.description,
                "country": p.country,
                "netname": p.netname,
            }
            for p in plans
        ],
    }


@router.post("/ripe/commit", dependencies=[Depends(require_admin)])
async def commit_ripe(
    file: Annotated[UploadFile, File()],
    section_id: Annotated[uuid.UUID, Form()],
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """寫入：所有解析出的 CIDR 建為新 Subnet 於指定 section。

    idempotent：同 (vrf, cidr) 已存在則 skip。
    """
    section = await session.get(Section, section_id)
    if section is None:
        raise HTTPException(400, detail="Invalid section_id")

    text = await _read_text(file)
    plans = planify(text)

    inserted = 0
    skipped = 0
    errored: list[dict[str, str]] = []

    for plan in plans:
        # 重疊檢查 — 已存在就 skip（idempotent）
        try:
            await assert_no_overlap(session, cidr=plan.cidr, vrf_id=None)
        except SubnetOverlap:
            skipped += 1
            continue
        try:
            master_id = await compute_master_subnet(session, cidr=plan.cidr, vrf_id=None)
            obj = Subnet(
                section_id=section.id,
                cidr=plan.cidr,
                description=plan.description,
                master_subnet_id=master_id,
            )
            session.add(obj)
            await session.flush()
            inserted += 1
        except Exception as exc:
            errored.append({"cidr": plan.cidr, "error": str(exc)})

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="section",
        object_id=str(section.id),
        action="ripe_twnic_import",
        diff={"inserted": inserted, "skipped": skipped, "errored": len(errored)},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()

    return {
        "inserted": inserted,
        "skipped": skipped,
        "errored": errored[:50],
        "total_plans": len(plans),
    }
