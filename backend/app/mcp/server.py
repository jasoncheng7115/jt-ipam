"""MCP server — 把 jt-ipam 工具暴露給外部 LLM client。

兩種 transport（共用同一套工具與 dispatch 核心 `process_message`）：

1. **Streamable HTTP**（掛在 /mcp）— MCP 2025 標準的單一端點：
   - `POST /mcp`：送 JSON-RPC 2.0（單筆或 batch）；request 回 application/json，
     純 notification 回 202。`initialize` 回應帶 `Mcp-Session-Id` header。
   - `GET /mcp`：本服務不主動推送 server→client，回 405（符合規範：server MAY）。
   - `DELETE /mcp`：結束 session，回 204。
   - 認證：`X-Auth-Token: jt_...`（或 `Authorization: Bearer jt_...`）。
2. **stdio**（`python -m app.mcp.stdio_server`）— 給本機啟動的 client（Claude Desktop 等）。

支援方法：`initialize` / `notifications/*`（忽略）/ `ping` / `tools/list` / `tools/call`。
寫入類工具成功後會 commit（修掉舊版 tools/call 不 commit 導致寫入被回滾的問題）。
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.db import SessionLocal
from app.core.security import hash_api_token
from app.mcp.tools import TOOLS, IPAMToolError
from app.version import __version__

# 我們支援的 MCP 協定版本（會盡量回 client 要的版本以利相容）
DEFAULT_PROTOCOL_VERSION = "2025-03-26"
_SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}


def _build_tool_list(allowed: set[str] | None = None) -> list[dict[str, Any]]:
    """產生 MCP `tools/list` 回應；allowed 給定時依 RBAC 過濾。"""
    return [
        {
            "name": name,
            "description": meta["description"],
            "inputSchema": meta["parameters"],
        }
        for name, meta in TOOLS.items()
        if allowed is None or name in allowed
    ]


async def resolve_token(token: str):  # type: ignore[no-untyped-def]
    """API token → User（沿用 REST 的 jt_ token 機制）。回 None 表示無效。"""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.models.user import APIToken, User

    async with SessionLocal() as session:
        digest = hash_api_token(token)
        api_token = (
            await session.execute(select(APIToken).where(APIToken.token_hash == digest))
        ).scalar_one_or_none()
        if api_token is None or api_token.revoked_at is not None:
            return None
        if api_token.expires_at <= datetime.now(UTC):
            return None
        user = await session.get(User, api_token.user_id)
        if user is None or not user.is_active:
            return None
        return user


async def _dispatch_call(name: str, arguments: dict[str, Any], user, session, *, readonly: bool = False):  # type: ignore[no-untyped-def]
    if name not in TOOLS:
        raise IPAMToolError(f"unknown tool: {name}")
    # 對外唯讀 MCP 金鑰：一律擋下會異動資料的工具（外部呼叫沒有「異動前確認」這道關）
    if readonly:
        from app.mcp.tools import MUTATING_TOOLS
        if name in MUTATING_TOOLS:
            raise IPAMToolError(f"read-only MCP key: tool '{name}' changes data and is disabled")
    # RBAC 閘（與 NL chat 共用）：零權限擋全部、全域基礎設施需萬用讀取、異動需 admin
    from app.mcp.tools import authorize_tool
    denied = await authorize_tool(session, user, name)
    if denied is not None:
        raise IPAMToolError(denied)
    fn = TOOLS[name]["fn"]
    return await fn(session, user=user, **arguments)


def _is_notification(body: dict[str, Any]) -> bool:
    method = body.get("method") or ""
    return "id" not in body or method.startswith("notifications/")


async def process_message(body: dict[str, Any], user, *, readonly: bool = False) -> dict[str, Any] | None:  # type: ignore[no-untyped-def]
    """處理單筆 JSON-RPC 訊息；notification 回 None（不回應）。HTTP 與 stdio 共用。
    readonly=True（對外 MCP 金鑰）時，工具清單隱藏、且呼叫一律擋下會異動資料的工具。"""
    if not isinstance(body, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    if _is_notification(body):
        return None  # notifications/initialized、notifications/cancelled… 一律忽略

    rid = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    try:
        if method == "initialize":
            ver = params.get("protocolVersion")
            result = {
                "protocolVersion": ver if ver in _SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "jt-ipam", "version": __version__},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            from app.mcp.tools import MUTATING_TOOLS, allowed_tool_names
            async with SessionLocal() as s:
                allowed = await allowed_tool_names(s, user)
            if readonly:
                # 唯讀金鑰：把異動工具從清單拿掉（None＝全部 → 全部扣掉異動）
                base = set(TOOLS) if allowed is None else set(allowed)
                allowed = base - MUTATING_TOOLS
            result = {"tools": _build_tool_list(allowed)}
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str):
                raise IPAMToolError("params.name is required")
            async with SessionLocal() as s:
                tool_result = await _dispatch_call(name, arguments, user, s, readonly=readonly)
                await s.commit()   # 寫入類工具要 commit，否則 async with 結束會回滾
            result = {
                "content": [{"type": "text", "text": _safe_json(tool_result)}],
                "isError": False,
            }
        else:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}}
    except IPAMToolError as exc:
        # 工具層錯誤：照 MCP 慣例包成 isError result（不是 protocol error）
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True}}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32603, "message": f"Internal error: {exc.__class__.__name__}"}}

    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _extract_token(request: Request) -> str:
    return (
        request.headers.get("x-auth-token")
        or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    )


async def _load_principal(user_id: str):  # type: ignore[no-untyped-def]
    """對外 MCP 金鑰所代表的身份（須仍為啟用中的帳號）。"""
    from app.models.user import User
    try:
        uid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        return None
    async with SessionLocal() as session:
        user = await session.get(User, uid)
        if user is None or not user.is_active:
            return None
        return user


def build_mcp_app() -> FastAPI:
    """掛在 /mcp 的 Streamable-HTTP 子應用。

    MCP 用 JSON-RPC（initialize → tools/list → tools/call）探索工具，**不是** OpenAPI；
    FastAPI 自動產生的 /openapi.json、/docs 對 MCP client 無意義且未經認證，故一律關閉。
    """
    sub = FastAPI(
        title="jt-ipam MCP", description="Model Context Protocol server", version=__version__,
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    @sub.post("/")
    @sub.post("/messages")   # 舊路徑相容
    async def streamable_post(request: Request) -> Response:
        # 對外提供 MCP 是 opt-in：管理員未在「管理 → LLM / AI」打開就一律拒絕（deny by default）
        from app.services.system_config import get_llm_config
        async with SessionLocal() as _s:
            mcfg = await get_llm_config(_s)
        if not mcfg.mcp_external_enabled:
            return JSONResponse({"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32001, "message": "MCP external access is disabled"}},
                                status_code=403)
        token = _extract_token(request)
        if not token:
            return JSONResponse({"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32001, "message": "X-Auth-Token required"}},
                                status_code=401)
        # 兩種認證：① 對外唯讀 MCP 金鑰（jtmcp_…，擋異動工具）② 既有 API 權杖（依該使用者權限）
        user = None
        readonly = False
        if (mcfg.mcp_api_key and mcfg.mcp_principal_user_id
                and secrets.compare_digest(token, mcfg.mcp_api_key)):
            user = await _load_principal(mcfg.mcp_principal_user_id)
            readonly = True
        if user is None:
            user = await resolve_token(token)
            readonly = False
        if user is None:
            return JSONResponse({"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32001, "message": "invalid or expired token"}},
                                status_code=401)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32700, "message": "Parse error"}},
                                status_code=400)

        batch = isinstance(payload, list)
        messages = payload if batch else [payload]
        has_initialize = any(isinstance(m, dict) and m.get("method") == "initialize" for m in messages)

        responses: list[dict[str, Any]] = []
        for m in messages:
            resp = await process_message(m, user, readonly=readonly)
            if resp is not None:
                responses.append(resp)

        headers = {}
        if has_initialize:
            headers["Mcp-Session-Id"] = uuid.uuid4().hex

        if not responses:
            # 全是 notification → 202 Accepted，無 body
            return Response(status_code=202, headers=headers)
        out: Any = responses if batch else responses[0]
        return JSONResponse(out, headers=headers)

    @sub.get("/")
    async def streamable_get() -> Response:
        # 本服務不提供 server→client SSE 串流（規範允許 server 回 405）
        return Response(status_code=405, headers={"Allow": "POST, DELETE"})

    @sub.delete("/")
    async def streamable_delete() -> Response:
        # 無狀態：沒有要清的 session，直接回 204
        return Response(status_code=204)

    return sub


def _safe_json(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)
