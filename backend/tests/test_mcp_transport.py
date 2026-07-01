"""MCP transport：Streamable HTTP / stdio 共用的 dispatch 核心 + HTTP 邊界行為。"""

from __future__ import annotations

from app.mcp.server import build_mcp_app, process_message
from app.version import __version__


async def test_initialize_echoes_version_and_protocol(admin_user):
    r = await process_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}}, admin_user)
    assert r["result"]["protocolVersion"] == "2024-11-05"   # 回 client 要的版本
    assert r["result"]["serverInfo"]["version"] == __version__
    assert r["result"]["serverInfo"]["version"] != "0.3.0"


async def test_initialize_unknown_protocol_falls_back(admin_user):
    r = await process_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999"}},
        admin_user)
    assert r["result"]["protocolVersion"] in {"2025-03-26", "2024-11-05", "2025-06-18"}


async def test_notification_returns_none(admin_user):
    assert await process_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}, admin_user) is None


async def test_ping(admin_user):
    r = await process_message({"jsonrpc": "2.0", "id": 2, "method": "ping"}, admin_user)
    assert r["result"] == {}


async def test_tools_list_has_new_tools(admin_user):
    r = await process_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, admin_user)
    names = {t["name"] for t in r["result"]["tools"]}
    assert {"search_ip", "get_topology", "list_firewalls"} <= names
    # 每個 tool 都有 inputSchema
    assert all("inputSchema" in t for t in r["result"]["tools"])


async def test_tools_call_readonly(admin_user, db_session):
    r = await process_message(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "stats_overview", "arguments": {}}}, admin_user)
    assert r["result"]["isError"] is False


async def test_method_not_found(admin_user):
    r = await process_message({"jsonrpc": "2.0", "id": 5, "method": "bogus/method"}, admin_user)
    assert r["error"]["code"] == -32601


async def test_http_get_405_and_unauth_post():
    from httpx import ASGITransport, AsyncClient
    app = build_mcp_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        g = await c.get("/")
        assert g.status_code == 405 and "POST" in g.headers.get("allow", "")
        p = await c.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        # 外部 MCP 預設關閉 → 403（在檢查 token 之前就擋下；打開後沒帶 token 才是 401）
        assert p.status_code == 403
        d = await c.delete("/")
        assert d.status_code == 204


def test_stdio_module_imports():
    # 確保 stdio entrypoint 可被載入（python -m app.mcp.stdio_server）
    import importlib
    m = importlib.import_module("app.mcp.stdio_server")
    assert hasattr(m, "main")
