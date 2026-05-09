"""Ollama 語意搜尋：本地推論不外送（規格 §11.1 / §11.3）。

設計：
- 透過 Ollama HTTP API 取得 embedding（POST /api/embeddings）
- 寫入時 / 排程時對 Subnet / IPAddress / Device 的 description 計算向量
- /api/v1/search/semantic?q=... 走 cosine 相似度（pgvector ivfflat）

OWASP A02 / A10：ollama_url 走 safe_request（私網允許）；任何回到 Ollama
之外的呼叫都會被擋住。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.safe_http import UnsafeOutboundURL, safe_request


class AINotConfigured(RuntimeError):
    pass


class AIError(RuntimeError):
    pass


async def embed(text_in: str) -> list[float]:
    """呼叫 Ollama 的 embedding endpoint。"""
    settings = get_settings()
    if not settings.ollama_enabled:
        raise AINotConfigured("Ollama is disabled")
    url = f"{settings.ollama_url.rstrip('/')}/api/embeddings"
    body = {"model": settings.ollama_embedding_model, "prompt": text_in}
    try:
        resp = await safe_request(
            "POST", url,
            headers={"Content-Type": "application/json"},
            json=body, timeout=settings.ollama_timeout,
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
    if len(vec) != settings.embedding_dim:
        raise AIError(
            f"Embedding dim mismatch: got {len(vec)}, expected {settings.embedding_dim} "
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
        vec = await embed(description)
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
        vec = await embed(description)
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
        vec = await embed(description)
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
    vec = await embed(query)
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
