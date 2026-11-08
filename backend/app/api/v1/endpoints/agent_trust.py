"""代理自動改成驗證 jt-ipam 用的信任資料（2026-11-07，見 services/agent_trust）。

公開、不收代理金鑰：代理在還不確定對面是不是真的 jt-ipam 的時候來拿，不能讓它先把金鑰交出去。
內容只有公開資訊（憑證指紋、身分公鑰），而且簽過章。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.ui_error import ui_detail

router = APIRouter(prefix="/agent-trust", tags=["agents"])


@router.get("", response_class=PlainTextResponse)
async def agent_trust(request: Request, session: Annotated[AsyncSession, Depends(get_session)]) -> PlainTextResponse:
    from app.services.agent_trust import bundle, parse_host

    where = parse_host(request.headers.get("host"))
    if where is None:
        raise HTTPException(404, detail=ui_detail("agent_trust_bad_host", "無法從這個網址取得 jt-ipam 的憑證"))
    text = await bundle(session, *where)
    if text is None:
        raise HTTPException(404, detail=ui_detail("agent_trust_unavailable", "這台 jt-ipam 沒有可用的 HTTPS 憑證資訊"))
    return PlainTextResponse(text, headers={"Cache-Control": "no-store"})
