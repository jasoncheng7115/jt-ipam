"""MCP (Model Context Protocol) Server — 暴露 IPAM 工具給本地 LLM。

設計：
- 工具實作直接呼叫 jt-ipam services（不繞回 REST），延遲低、權限直接受控
- 認證沿用 API token（jt_<env>_<random>）；每個工具 call 都會檢查 token 有效性
- HTTP transport 掛在 /mcp/，stdio transport 透過 `python -m app.mcp.stdio_server`

OWASP A01 / A07：
- 所有工具強制要求 X-Auth-Token header（API token）
- 寫入類工具（allocate_ip / approve_request）需要 token 對應 user 是 admin
"""

from app.mcp.server import build_mcp_app

__all__ = ["build_mcp_app"]
