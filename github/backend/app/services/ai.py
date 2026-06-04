"""Ollama 語意搜尋：本地推論不外送（規格 §11.1 / §11.3）。

設計：
- 透過 Ollama HTTP API 取得 embedding（POST /api/embeddings）
- 寫入時 / 排程時對 Subnet / IPAddress / Device 的 description 計算向量
- /api/v1/search/semantic?q=... 走 cosine 相似度（pgvector ivfflat）

OWASP A04 / A06：ollama_url 走 safe_request（私網允許）；任何回到 Ollama
之外的呼叫都會被擋住。
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.safe_http import UnsafeOutboundURL, safe_request, safe_stream


class AINotConfigured(RuntimeError):
    pass


class AIError(RuntimeError):
    pass


async def embed(session: AsyncSession, text_in: str) -> list[float]:
    """呼叫 Ollama 的 embedding endpoint。設定取自 system_settings (DB)，fallback 到 env。"""
    from app.services.system_config import get_llm_config
    cfg = await get_llm_config(session)
    if not cfg.enabled:
        raise AINotConfigured("Ollama is disabled")
    url = f"{cfg.url.rstrip('/')}/api/embeddings"
    body = {"model": cfg.embedding_model, "prompt": text_in}
    try:
        resp = await safe_request(
            "POST", url,
            headers={"Content-Type": "application/json"},
            json=body, timeout=cfg.timeout,
        )
    except UnsafeOutboundURL as exc:
        raise AIError(f"SSRF guard: {exc}") from exc
    except httpx.HTTPError as exc:
        raise AIError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise AIError(f"Ollama {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    vec = data.get("embedding")
    if not isinstance(vec, list) or not vec:
        raise AIError("Ollama returned no embedding")
    expected_dim = get_settings().embedding_dim
    if len(vec) != expected_dim:
        raise AIError(
            f"Embedding dim mismatch: got {len(vec)}, expected {expected_dim} "
            f"(adjust EMBEDDING_DIM or migration vector(N))"
        )
    return [float(x) for x in vec]


def _vector_literal(vec: list[float]) -> str:
    """pgvector 的字串字面值：'[0.1,0.2,...]'。"""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


# ─────────────────── 寫入：對單一物件描述產生向量 ───────────────────


async def index_subnet(session: AsyncSession, subnet_id: str, description: str | None) -> bool:
    """為一個 subnet 產 embedding 並寫入 vector 欄位；description 為空則清空。"""
    if not description:
        await session.execute(
            text("UPDATE subnets SET description_embedding = NULL WHERE id = :id"),
            {"id": subnet_id},
        )
        return True
    try:
        vec = await embed(session, description)
    except (AIError, AINotConfigured):
        return False
    await session.execute(
        text("UPDATE subnets SET description_embedding = (:v)::vector WHERE id = :id"),
        {"v": _vector_literal(vec), "id": subnet_id},
    )
    return True


async def index_ip(session: AsyncSession, ip_id: str, description: str | None) -> bool:
    if not description:
        await session.execute(
            text("UPDATE ip_addresses SET description_embedding = NULL WHERE id = :id"),
            {"id": ip_id},
        )
        return True
    try:
        vec = await embed(session, description)
    except (AIError, AINotConfigured):
        return False
    await session.execute(
        text("UPDATE ip_addresses SET description_embedding = (:v)::vector WHERE id = :id"),
        {"v": _vector_literal(vec), "id": ip_id},
    )
    return True


async def index_device(session: AsyncSession, device_id: str, description: str | None) -> bool:
    if not description:
        await session.execute(
            text("UPDATE devices SET description_embedding = NULL WHERE id = :id"),
            {"id": device_id},
        )
        return True
    try:
        vec = await embed(session, description)
    except (AIError, AINotConfigured):
        return False
    await session.execute(
        text("UPDATE devices SET description_embedding = (:v)::vector WHERE id = :id"),
        {"v": _vector_literal(vec), "id": device_id},
    )
    return True


# ─────────────────── 查詢：跨表 cosine 最相近 ───────────────────


async def semantic_search(
    session: AsyncSession,
    *,
    query: str,
    limit: int = 20,
) -> dict[str, Any]:
    """跨 subnets / ip_addresses / devices 的語意搜尋（cosine 距離，越小越像）。"""
    vec = await embed(session, query)
    vlit = _vector_literal(vec)

    sub_rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, cidr::text AS label, description,
                       (description_embedding <=> (:v)::vector) AS distance
                  FROM subnets
                 WHERE description_embedding IS NOT NULL
                 ORDER BY description_embedding <=> (:v)::vector
                 LIMIT :limit
                """
            ),
            {"v": vlit, "limit": limit},
        )
    ).all()

    ip_rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, host(ip)::text AS label,
                       hostname, description,
                       (description_embedding <=> (:v)::vector) AS distance
                  FROM ip_addresses
                 WHERE description_embedding IS NOT NULL
                 ORDER BY description_embedding <=> (:v)::vector
                 LIMIT :limit
                """
            ),
            {"v": vlit, "limit": limit},
        )
    ).all()

    dev_rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, name AS label, description,
                       (description_embedding <=> (:v)::vector) AS distance
                  FROM devices
                 WHERE description_embedding IS NOT NULL
                 ORDER BY description_embedding <=> (:v)::vector
                 LIMIT :limit
                """
            ),
            {"v": vlit, "limit": limit},
        )
    ).all()

    return {
        "query": query,
        "subnets": [
            {"id": r.id, "label": r.label, "description": r.description,
             "score": round(1 - float(r.distance), 4)}
            for r in sub_rows
        ],
        "ip_addresses": [
            {"id": r.id, "label": r.label, "hostname": r.hostname,
             "description": r.description, "score": round(1 - float(r.distance), 4)}
            for r in ip_rows
        ],
        "devices": [
            {"id": r.id, "label": r.label, "description": r.description,
             "score": round(1 - float(r.distance), 4)}
            for r in dev_rows
        ],
    }


# ─────────────────── Chat：自然語言 + tool use（Phase 4）───────────────────


_LANG_MAP = {
    "zh-TW": "Traditional Chinese (繁體中文，使用台灣用語)",
    "zh-CN": "Simplified Chinese (简体中文)",
    "en-US": "English",
    "en": "English",
    "ja": "Japanese (日本語)",
}


def _lang_instruction(locale: str | None) -> str:
    """根據使用者 UI locale 給出要求 LLM 用何語言回應的指令。"""
    name = _LANG_MAP.get(locale or "", None)
    if not name:
        # 未知 locale → 不強制；讓 LLM 跟著使用者輸入的語言
        return "Respond in the same language as the user's most recent message."
    return f"Always respond to the user in {name}, regardless of the language of tool outputs."


async def chat(
    session: AsyncSession,
    *,
    user: Any,
    messages: list[dict[str, Any]],
    locale: str | None = None,
    max_iterations: int = 4,
    page_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """以 Ollama chat 模型 + jt-ipam tools 處理自然語言。

    流程：
      1. 把 IPAM tools 註冊表轉成 Ollama tools schema
      2. 用 system prompt 框定身份（jt-ipam 助手）
      3. 呼叫 Ollama；若回 tool_calls，執行對應 jt-ipam 工具
      4. 把 tool 結果 append 回 messages，再呼叫一次（最多 max_iterations 輪）

    OWASP A04 / A06：
      - chat 對外 URL 走 safe_request
      - tool 執行時的 user 與 session 都從本端拿，不從 LLM 輸入信任
    """
    from app.services.system_config import get_llm_config
    cfg = await get_llm_config(session)
    if not cfg.enabled:
        raise AINotConfigured("Ollama is disabled")

    ollama_tools, convo = _build_chat_context(messages, locale, page_context)
    url = f"{cfg.url.rstrip('/')}/api/chat"
    started = time.monotonic()

    def _meta() -> dict[str, Any]:
        return {"model": cfg.chat_model, "elapsed_ms": int((time.monotonic() - started) * 1000)}

    for _ in range(max_iterations):
        body = {
            "model": cfg.chat_model,
            "messages": convo,
            "tools": ollama_tools,
            "stream": False,
            # 低溫度：減少模型亂插字（如把 192.168 寫成「19 kiếm 168」之類的跨語言錯字）
            "options": {"temperature": 0.2},
        }
        try:
            resp = await safe_request(
                "POST", url,
                headers={"Content-Type": "application/json"},
                json=body, timeout=cfg.timeout,
            )
        except UnsafeOutboundURL as exc:
            raise AIError(f"SSRF guard: {exc}") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"transport: {exc.__class__.__name__}") from exc
        if resp.status_code != 200:
            raise AIError(f"Ollama chat {resp.status_code}: {resp.text[:200]}")
        data = resp.json()

        msg = data.get("message") or {}
        convo.append(msg)

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return {"answer": msg.get("content") or "", "messages": convo, **_meta()}

        convo.extend(await _run_tool_calls(session, user, tool_calls))

    # 用完 max_iterations 還在叫工具 → 最後不給工具再叫一次，逼它用現有資訊作答
    answer = await _force_final_answer(cfg, convo)
    return {"answer": answer, "messages": convo, **_meta()}


async def _force_final_answer(cfg: Any, convo: list[dict[str, Any]]) -> str:
    """max_iterations 用完時的收尾：不帶 tools 再呼叫一次，要 LLM 直接作答。"""
    url = f"{cfg.url.rstrip('/')}/api/chat"
    body = {"model": cfg.chat_model, "messages": convo, "stream": False, "options": {"temperature": 0.2}}
    try:
        resp = await safe_request(
            "POST", url, headers={"Content-Type": "application/json"},
            json=body, timeout=cfg.timeout,
        )
        if resp.status_code == 200:
            msg = (resp.json().get("message") or {})
            content = msg.get("content")
            if content:
                convo.append({"role": "assistant", "content": content})
                return content  # type: ignore[no-any-return]
    except (UnsafeOutboundURL, httpx.HTTPError):
        pass
    return "（查詢步驟過多仍未完成，請把問題拆小一點再試一次）"


def _page_context_line(context: dict[str, Any] | None) -> str:
    """把前端帶來的「目前所在頁面」資訊轉成 system prompt 提示。"""
    if not context:
        return ""
    parts: list[str] = []
    cidr = context.get("subnet_cidr")
    sid = context.get("subnet_id")
    if sid or cidr:
        ref = cidr or sid
        parts.append(
            f" The user is currently viewing subnet {ref}"
            + (f" (subnet_id={sid})" if sid else "")
            + ". If they ask for free IPs, usage, or allocation without naming a "
            "subnet, default to THIS subnet."
        )
    if context.get("device_id"):
        parts.append(f" They are viewing device_id={context['device_id']}.")
    if context.get("section_id"):
        parts.append(f" They are viewing section_id={context['section_id']}.")
    return "".join(parts)


def _build_chat_context(
    messages: list[dict[str, Any]], locale: str | None,
    page_context: dict[str, Any] | None = None,
) -> Any:
    """共用：把 IPAM tools 轉 Ollama schema + 組 system prompt + 接上對話。

    回傳 (ollama_tools, convo)。chat / chat_stream 共用，避免兩份 prompt 漂移。
    """
    from app.mcp.tools import TOOLS

    ollama_tools = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": meta["description"],
                "parameters": meta["parameters"],
            },
        }
        for name, meta in TOOLS.items()
    ]
    convo: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are jt-ipam's network operations assistant. Use the provided "
                "tools to answer questions about subnets, IPs (get_ip_detail / "
                "get_subnet_detail for full records), devices, DNS (servers/zones/"
                "consistency), VPN tunnels (WireGuard / IPsec / OpenVPN, incl. site-to-"
                "site), NAT, VLANs, VRFs, racks, sections, firewalls and their rules/"
                "aliases, network topology (get_topology), ARP/FDB, IP-allocation "
                "requests, scan agents, virtual machines, wireless links, customers, and "
                "Wazuh security-coverage gaps (wazuh_missing_agents). Before saying you "
                "cannot determine something, check whether a relevant tool exists and "
                "call it (e.g. list_vpn_tunnels for site-to-site VPN, get_topology for "
                "how things connect, list_firewall_rules for firewall policy). "
                "Tools whose description says 'ADMIN ONLY' only work for admin users; "
                "do not attempt writes unless the user clearly asks to change data. "
                "Always cite the IPs / CIDRs / device names returned by tools, and copy "
                "every IP, CIDR, MAC, hostname and identifier VERBATIM, character for "
                "character — never translate, localise, reformat or alter a single digit. "
                "If a tool errors, explain it briefly. "
                "Stay strictly on-topic: only answer questions about THIS jt-ipam system, "
                "its data (network / IPAM / devices / firewalls / DNS …) or how to use it. "
                "For unrelated questions (general coding, world knowledge, chit-chat), "
                "politely decline and suggest a dedicated general-purpose LLM platform "
                "such as Open WebUI or opencode instead. "
                "NEVER invent, guess, or extend IP data — only report exactly what "
                "tools return. When the user wants several or consecutive free IPs, "
                "call find_free_ips with the right count/consecutive ONCE; report only "
                "the IPs it returns, and if it returns fewer than requested, say so "
                "instead of making up more. "
                + _lang_instruction(locale)
                + _page_context_line(page_context)
            ),
        }
    ]
    convo.extend(messages)
    return ollama_tools, convo


async def _run_tool_calls(session: AsyncSession, user: Any, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """執行 LLM 要求的 tool_calls，回傳要 append 進 convo 的 tool 訊息（含 name）。"""
    from app.mcp.tools import TOOLS, IPAMToolError

    out: list[dict[str, Any]] = []
    for call in tool_calls:
        fn = call.get("function") or {}
        name = fn.get("name")
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if name not in TOOLS:
            tool_result: Any = {"error": f"unknown tool {name!r}"}
        else:
            try:
                tool_result = await TOOLS[name]["fn"](session, user=user, **args)
            except IPAMToolError as exc:
                tool_result = {"error": str(exc)}
            except Exception as exc:
                tool_result = {"error": f"tool failed: {exc.__class__.__name__}"}
        out.append({
            "role": "tool",
            "name": name,
            "content": json.dumps(tool_result, ensure_ascii=False, default=str),
        })
    return out


async def chat_stream(
    session: AsyncSession,
    *,
    user: Any,
    messages: list[dict[str, Any]],
    locale: str | None = None,
    max_iterations: int = 4,
    page_context: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """chat 的 streaming 版：逐 token 把最終回答吐出來（規格 §11.1，本地推論不外送）。

    yield 的事件（給 SSE endpoint 包成 data: ...）：
      {"type": "token",      "text": ...}    最終回答的增量片段
      {"type": "tool",       "name": ...}    正在執行某個工具
      {"type": "tool_round"}                 該輪是 tool round，前端應清掉已收到的暫存 token
      {"type": "done",       "answer": ..., "trace_messages": convo}
      {"type": "error",      "detail": ...}

    串流時無法在中途改 HTTP status，故所有錯誤都以 error 事件回報；config 未開的
    503 由 endpoint 在開串流前先擋掉。
    """
    from app.services.system_config import get_llm_config
    cfg = await get_llm_config(session)
    if not cfg.enabled:
        yield {"type": "error", "detail": "Ollama is disabled"}
        return

    ollama_tools, convo = _build_chat_context(messages, locale, page_context)
    url = f"{cfg.url.rstrip('/')}/api/chat"
    started = time.monotonic()

    def _meta() -> dict[str, Any]:
        return {"model": cfg.chat_model, "elapsed_ms": int((time.monotonic() - started) * 1000)}

    for _ in range(max_iterations):
        body = {
            "model": cfg.chat_model,
            "messages": convo,
            "tools": ollama_tools,
            "stream": True,
            "options": {"temperature": 0.2},
        }
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        try:
            async with safe_stream(
                "POST", url,
                headers={"Content-Type": "application/json"},
                json=body, timeout=cfg.timeout,
            ) as resp:
                if resp.status_code != 200:
                    detail = (await resp.aread()).decode("utf-8", "replace")[:200]
                    yield {"type": "error", "detail": f"Ollama chat {resp.status_code}: {detail}"}
                    return
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    m = chunk.get("message") or {}
                    piece = m.get("content")
                    if piece:
                        content_parts.append(piece)
                        yield {"type": "token", "text": piece}
                    tcs = m.get("tool_calls")
                    if tcs:
                        tool_calls.extend(tcs)
                    if chunk.get("done"):
                        break
        except UnsafeOutboundURL as exc:
            yield {"type": "error", "detail": f"SSRF guard: {exc}"}
            return
        except httpx.HTTPError as exc:
            yield {"type": "error", "detail": f"transport: {exc.__class__.__name__}"}
            return

        full_content = "".join(content_parts)
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": full_content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        convo.append(assistant_msg)

        if not tool_calls:
            yield {"type": "done", "answer": full_content, "trace_messages": convo, **_meta()}
            return

        # 這輪是 tool round：剛吐的 token（若有，多半是 thinking）不是最終答案，叫前端清掉
        yield {"type": "tool_round"}
        for call in tool_calls:
            yield {"type": "tool", "name": (call.get("function") or {}).get("name")}
        convo.extend(await _run_tool_calls(session, user, tool_calls))

    # max_iterations 用完 → 不給工具，串流最後一次強制作答
    yield {"type": "tool_round"}
    final_parts: list[str] = []
    body = {"model": cfg.chat_model, "messages": convo, "stream": True, "options": {"temperature": 0.2}}
    try:
        async with safe_stream(
            "POST", url, headers={"Content-Type": "application/json"},
            json=body, timeout=cfg.timeout,
        ) as resp:
            if resp.status_code == 200:
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    piece = (chunk.get("message") or {}).get("content")
                    if piece:
                        final_parts.append(piece)
                        yield {"type": "token", "text": piece}
                    if chunk.get("done"):
                        break
    except (UnsafeOutboundURL, httpx.HTTPError):
        pass
    answer = "".join(final_parts) or "（查詢步驟過多仍未完成，請把問題拆小一點再試一次）"
    if final_parts:
        convo.append({"role": "assistant", "content": answer})
    yield {"type": "done", "answer": answer, "trace_messages": convo, **_meta()}


# ─────────────────── 全表 reindex（admin 一次性） ───────────────────


async def reindex_all(session: AsyncSession) -> dict[str, int]:
    """重新計算所有有 description 的物件的 embedding。慢；只在初始化或換 model 時跑。"""
    from app.models.address import IPAddress
    from app.models.device import Device
    from app.models.subnet import Subnet

    stats = {"subnets": 0, "ip_addresses": 0, "devices": 0}

    sub_rows = (
        await session.execute(
            select(Subnet.id, Subnet.description).where(Subnet.description.isnot(None))
        )
    ).all()
    for sid, desc in sub_rows:
        if await index_subnet(session, str(sid), desc):
            stats["subnets"] += 1
    await session.commit()

    ip_rows = (
        await session.execute(
            select(IPAddress.id, IPAddress.description).where(
                IPAddress.description.isnot(None)
            )
        )
    ).all()
    for iid, desc in ip_rows:
        if await index_ip(session, str(iid), desc):
            stats["ip_addresses"] += 1
    await session.commit()

    dev_rows = (
        await session.execute(
            select(Device.id, Device.description).where(Device.description.isnot(None))
        )
    ).all()
    for did, desc in dev_rows:
        if await index_device(session, str(did), desc):
            stats["devices"] += 1
    await session.commit()

    return stats
