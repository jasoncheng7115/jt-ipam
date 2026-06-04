"""stdio MCP transport — 給本機啟動的 client（Claude Desktop、mcp CLI…）。

執行：`python -m app.mcp.stdio_server`
認證：環境變數 `JT_IPAM_MCP_TOKEN`（或 `JT_IPAM_API_TOKEN`）帶 jt_ API token。
另需正確的 DB/環境變數（如 source /etc/jt-ipam/backend.env）才能連到 jt-ipam 資料庫。

協定：讀 stdin 的 newline-delimited JSON-RPC 2.0，dispatch 後把回應寫到 stdout
（與 Streamable HTTP 共用 `app.mcp.server.process_message`）。notification 不回應。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any


def _write(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


async def _amain() -> int:
    from app.mcp.server import process_message, resolve_token

    token = os.environ.get("JT_IPAM_MCP_TOKEN") or os.environ.get("JT_IPAM_API_TOKEN") or ""
    if not token:
        print("stdio MCP: set JT_IPAM_MCP_TOKEN (a jt_ API token)", file=sys.stderr, flush=True)
        return 1
    user = await resolve_token(token)
    if user is None:
        print("stdio MCP: invalid or expired token", file=sys.stderr, flush=True)
        return 1

    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if line == "":            # EOF → client 關閉
            break
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except json.JSONDecodeError:
            _write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        if isinstance(body, list):           # batch
            outs = [r for m in body if (r := await process_message(m, user)) is not None]
            if outs:
                _write(outs)
        else:
            resp = await process_message(body, user)
            if resp is not None:
                _write(resp)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
