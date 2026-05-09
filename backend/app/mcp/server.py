"""MCP HTTP server — 透過 FastMCP 暴露工具給 LLM。

掛載在 /mcp/。認證透過 X-Auth-Token header（jt_ API token），與 REST 相同。

Flow：
  client → POST /mcp/messages (MCP JSON-RPC 2.0)
        → FastMCP dispatcher → tool function
        → tool function uses jt-ipam services with the resolved User
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request

from app.core.db import SessionLocal
from app.core.security import hash_api_token
from app.mcp.tools import IPAMToolError, TOOLS


def _build_tool_list() -> list[dict[str, Any]]:
    """產生 MCP `tools/list` 回應。"""
    return [
        {
            "name": name,
            "description": meta["description"],
            "inputSchema": meta["parameters"],
        }
        for name, meta in TOOLS.items()
    ]


async def _resolve_token(token: str):  # type: ignore[no-untyped-def]
    """X-Auth-Token → User；沿用 REST API token 機制。"""
    from datetime import UTC, datetime
    from sqlalchemy import select
    from app.models.user import APIToken, User

    async with SessionLocal() as session:
        digest = hash_api_token(token)
        api_token = (
            await session.execute(select(APIToken).where(APIToken.token_hash == digest))
        ).scalar_one_or_none()
        if api_token is None or api_token.revoked_at is not None:
            return None, None
        if api_token.expires_at <= datetime.now(UTC):
            return None, None
        user = await session.get(User, api_token.user_id)
        if user is None or not user.is_active:
            return None, None
        return user, session


async def _dispatch_call(name: str, arguments: dict[str, Any], user, session):  # type: ignore[no-untyped-def]
    if name not in TOOLS:
        raise IPAMToolError(f"unknown tool: {name}")
    fn = TOOLS[name]["fn"]
    return await fn(session, user=user, **arguments)


def build_mcp_app() -> FastAPI:
    """回傳掛在 /mcp 的 sub-FastAPI。

    使用 FastAPI 而非完整 mcp SDK 是因為 mcp HTTP transport 在 SDK 還是
    streaming SSE / WebSocket；對 jt-ipam 內嵌呼叫情境太重。我們提供
    JSON-RPC 2.0 over POST 的最小子集，相容 MCP 協定。
    """
    sub = FastAPI(title="jt-ipam MCP", description="Model Context Protocol server")

    @sub.post("/")
    @sub.post("/messages")
    async def jsonrpc(request: Request) -> dict[str, Any]:
        token = request.headers.get("x-auth-token") or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not token:
            raise HTTPException(401, detail="X-Auth-Token required")
        user, session_open = await _resolve_token(token)
        if user is None:
            raise HTTPException(401, detail="invalid or expired token")

        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, detail="invalid JSON") from exc

        method = body.get("method")
        rid = body.get("id")
        params = body.get("params") or {}

        try:
            if method == "tools/list":
                result = {"tools": _build_tool_list()}
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not isinstance(name, str):
                    raise IPAMToolError("name is required")
                async with SessionLocal() as s:
                    result = await _dispatch_call(name, arguments, user, s)
                # MCP 慣例：tools/call 結果包成 content list
                result = {
                    "content": [{"type": "text", "text": _safe_json(result)}],
                    "isError": False,
                }
            elif method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "jt-ipam", "version": "0.3.0"},
                }
            else:
                return {
                    "jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
        except IPAMToolError as exc:
            return {
                "jsonrpc": "2.0", "id": rid,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }
        finally:
            if session_open is not None:
                try:
                    await session_open.close()
                except Exception:  # noqa: BLE001
                    pass

        return {"jsonrpc": "2.0", "id": rid, "result": result}

    return sub


def _safe_json(obj: Any) -> str:  # noqa: ANN401
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)
