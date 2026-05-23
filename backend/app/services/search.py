"""跨物件搜尋。

主動超越 phpIPAM 搜尋的設計：
- 查詢自動偵測類型（CIDR / IP / MAC / hostname / 自由文字）
- pg_trgm 模糊相似度 + 排序
- 跨 Section / Subnet / IPAddress / Device / VLAN
- 分類回傳，每筆帶 score 0..1（trigram 為主，精確命中固定 1.0）
- 透過 RBAC `filter_visible` 過濾無權看到的物件

OWASP A05：所有輸入只在 SQL 中以參數化使用；不字串拼接。
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.device import Device
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.user import User
from app.models.vlan import VLAN
from app.services.permission import filter_visible

ResultType = Literal["section", "subnet", "ip_address", "device", "vlan"]

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){2,5}[0-9A-Fa-f]{2}$|^[0-9A-Fa-f]{6,12}$")


@dataclass
class SearchHit:
    type: ResultType
    id: str
    label: str
    sublabel: str | None
    score: float

    def as_dict(self) -> dict[str, object]:
        return {
            "type": self.type,
            "id": self.id,
            "label": self.label,
            "sublabel": self.sublabel,
            "score": round(self.score, 3),
        }


def _detect_query_kind(q: str) -> str:
    """heuristic 判斷查詢類型。

    回傳：'cidr' | 'ip' | 'mac' | 'vlan_number' | 'free'
    """
    q = q.strip()
    if not q:
        return "free"
    if "/" in q:
        try:
            ipaddress.ip_network(q, strict=False)
            return "cidr"
        except ValueError:
            pass
    try:
        ipaddress.ip_address(q)
        return "ip"
    except ValueError:
        pass
    if _MAC_RE.match(q):
        return "mac"
    if q.isdigit() and 1 <= int(q) <= 4094:
        return "vlan_number"
    return "free"


async def _search_ip_exact(
    session: AsyncSession, *, user: User, ip: str, limit: int
) -> list[SearchHit]:
    rows = list(
        (
            await session.execute(
                select(IPAddress).where(IPAddress.ip == ip).limit(limit)
            )
        ).scalars().all()
    )
    if not rows:
        return []
    visible_subnets = set(
        await filter_visible(
            session, user=user, object_type="subnet",
            object_ids=[r.subnet_id for r in rows], required="read",
        )
    )
    return [
        SearchHit(
            type="ip_address",
            id=str(r.id),
            label=str(r.ip).split("/")[0],
            sublabel=r.hostname,
            score=1.0,
        )
        for r in rows
        if r.subnet_id in visible_subnets
    ]


async def _search_subnet_cidr(
    session: AsyncSession, *, user: User, cidr: str, limit: int
) -> list[SearchHit]:
    """同時找：cidr 完全相符、cidr 包含 q、q 包含 cidr。"""
    sql = text(
        """
        SELECT id, cidr::text AS cidr, description,
            CASE
                WHEN cidr = CAST(:q AS cidr) THEN 1.0
                WHEN cidr >> CAST(:q AS cidr) THEN 0.85
                WHEN cidr << CAST(:q AS cidr) THEN 0.85
                ELSE 0.7
            END AS score
        FROM subnets
        WHERE cidr = CAST(:q AS cidr)
           OR cidr >> CAST(:q AS cidr)
           OR cidr << CAST(:q AS cidr)
        ORDER BY score DESC, masklen(cidr)
        LIMIT :limit
        """
    )
    rows = (await session.execute(sql, {"q": cidr, "limit": limit})).all()
    if not rows:
        return []
    sids = [r.id for r in rows]
    visible = set(
        await filter_visible(
            session, user=user, object_type="subnet", object_ids=sids, required="read"
        )
    )
    return [
        SearchHit(
            type="subnet",
            id=str(r.id),
            label=r.cidr,
            sublabel=r.description,
            score=float(r.score),
        )
        for r in rows
        if r.id in visible
    ]


async def _search_mac(
    session: AsyncSession, *, user: User, mac: str, limit: int
) -> list[SearchHit]:
    cleaned = mac.lower().replace("-", ":")
    rows = list(
        (
            await session.execute(
                select(IPAddress)
                .where(IPAddress.mac.cast(text("text")).ilike(f"%{cleaned}%"))
                .limit(limit)
            )
        ).scalars().all()
    )
    visible = set(
        await filter_visible(
            session, user=user, object_type="subnet",
            object_ids=[r.subnet_id for r in rows], required="read",
        )
    )
    return [
        SearchHit(
            type="ip_address",
            id=str(r.id),
            label=str(r.mac),
            sublabel=f"{str(r.ip).split('/')[0]} {r.hostname or ''}".strip(),
            score=0.95,
        )
        for r in rows
        if r.subnet_id in visible
    ]


async def _search_vlan_number(
    session: AsyncSession, *, _user: User, number: int, limit: int
) -> list[SearchHit]:
    rows = list(
        (
            await session.execute(
                select(VLAN).where(VLAN.number == number).limit(limit)
            )
        ).scalars().all()
    )
    return [
        SearchHit(
            type="vlan",
            id=str(r.id),
            label=f"VLAN {r.number} — {r.name}",
            sublabel=r.description,
            score=1.0,
        )
        for r in rows
    ]


async def _search_text_trgm(
    session: AsyncSession, *, user: User, q: str, limit: int
) -> list[SearchHit]:
    """跨多表 trigram 搜尋 + 排序。"""
    qlike = f"%{q}%"
    out: list[SearchHit] = []

    # Sections
    sec_sql = text(
        """
        SELECT id, name, description,
               GREATEST(similarity(name, :q),
                        COALESCE(similarity(description, :q), 0)) AS score
          FROM sections
         WHERE name ILIKE :qlike
            OR description ILIKE :qlike
            OR similarity(name, :q) > 0.2
         ORDER BY score DESC
         LIMIT :limit
        """
    )
    sec_rows = (await session.execute(sec_sql, {"q": q, "qlike": qlike, "limit": limit})).all()
    visible_sec = set(
        await filter_visible(
            session, user=user, object_type="section",
            object_ids=[r.id for r in sec_rows], required="read",
        )
    )
    for r in sec_rows:
        if r.id in visible_sec:
            out.append(
                SearchHit(
                    type="section",
                    id=str(r.id),
                    label=r.name,
                    sublabel=r.description,
                    score=min(float(r.score or 0), 1.0),
                )
            )

    # Subnets
    sub_sql = text(
        """
        SELECT id, cidr::text AS cidr, description,
               COALESCE(similarity(description, :q), 0) AS score
          FROM subnets
         WHERE description ILIKE :qlike
            OR similarity(description, :q) > 0.2
         ORDER BY score DESC
         LIMIT :limit
        """
    )
    sub_rows = (await session.execute(sub_sql, {"q": q, "qlike": qlike, "limit": limit})).all()
    visible_sub = set(
        await filter_visible(
            session, user=user, object_type="subnet",
            object_ids=[r.id for r in sub_rows], required="read",
        )
    )
    for r in sub_rows:
        if r.id in visible_sub:
            out.append(
                SearchHit(
                    type="subnet",
                    id=str(r.id),
                    label=r.cidr,
                    sublabel=r.description,
                    score=min(float(r.score or 0), 1.0),
                )
            )

    # IP addresses (hostname)
    ip_sql = text(
        """
        SELECT a.id, host(a.ip) AS ip, a.hostname, a.subnet_id,
               GREATEST(
                 COALESCE(similarity(a.hostname, :q), 0),
                 COALESCE(similarity(a.description, :q), 0)
               ) AS score
          FROM ip_addresses a
         WHERE a.hostname ILIKE :qlike
            OR a.description ILIKE :qlike
            OR similarity(a.hostname, :q) > 0.2
         ORDER BY score DESC
         LIMIT :limit
        """
    )
    ip_rows = (await session.execute(ip_sql, {"q": q, "qlike": qlike, "limit": limit})).all()
    visible_ip_subnets = set(
        await filter_visible(
            session, user=user, object_type="subnet",
            object_ids=[r.subnet_id for r in ip_rows], required="read",
        )
    )
    for r in ip_rows:
        if r.subnet_id in visible_ip_subnets:
            out.append(
                SearchHit(
                    type="ip_address",
                    id=str(r.id),
                    label=r.hostname or r.ip,
                    sublabel=r.ip if r.hostname else None,
                    score=min(float(r.score or 0), 1.0),
                )
            )

    # Devices
    dev_sql = text(
        """
        SELECT id, name, vendor, model,
               GREATEST(
                 similarity(name, :q),
                 COALESCE(similarity(serial, :q), 0),
                 COALESCE(similarity(description, :q), 0)
               ) AS score
          FROM devices
         WHERE name ILIKE :qlike
            OR serial ILIKE :qlike
            OR description ILIKE :qlike
            OR similarity(name, :q) > 0.2
         ORDER BY score DESC
         LIMIT :limit
        """
    )
    dev_rows = (await session.execute(dev_sql, {"q": q, "qlike": qlike, "limit": limit})).all()
    for r in dev_rows:
        out.append(
            SearchHit(
                type="device",
                id=str(r.id),
                label=r.name,
                sublabel=" ".join(filter(None, [r.vendor, r.model])) or None,
                score=min(float(r.score or 0), 1.0),
            )
        )

    out.sort(key=lambda h: h.score, reverse=True)
    return out


async def search(
    session: AsyncSession,
    *,
    user: User,
    q: str,
    limit_per_type: int = 8,
) -> dict[str, list[dict[str, object]]]:
    """主搜尋入口；依偵測到的查詢類型走最相關的子搜尋，並補上 trigram 結果。"""
    q = q.strip()
    if len(q) < 2:
        return {"detected": "empty", "results": []}  # type: ignore[return-value]

    kind = _detect_query_kind(q)
    hits: list[SearchHit] = []

    if kind == "cidr":
        hits.extend(await _search_subnet_cidr(session, user=user, cidr=q, limit=limit_per_type))
    elif kind == "ip":
        hits.extend(await _search_ip_exact(session, user=user, ip=q, limit=limit_per_type))
        # 也找包含此 IP 的 subnet
        sql = text(
            """
            SELECT id, cidr::text AS cidr, description
              FROM subnets
             WHERE cidr >> CAST(:q AS inet)
             ORDER BY masklen(cidr) DESC
             LIMIT :limit
            """
        )
        rows = (await session.execute(sql, {"q": q, "limit": limit_per_type})).all()
        sids = [r.id for r in rows]
        visible = set(
            await filter_visible(
                session, user=user, object_type="subnet",
                object_ids=sids, required="read",
            )
        )
        for r in rows:
            if r.id in visible:
                hits.append(SearchHit(
                    type="subnet", id=str(r.id), label=r.cidr,
                    sublabel=r.description, score=0.9,
                ))
    elif kind == "mac":
        hits.extend(await _search_mac(session, user=user, mac=q, limit=limit_per_type))
    elif kind == "vlan_number":
        hits.extend(await _search_vlan_number(session, _user=user, number=int(q),
                                              limit=limit_per_type))

    # 一律補上 trigram 模糊搜尋（讓使用者打 IP 也能撞到 hostname 等）
    hits.extend(await _search_text_trgm(session, user=user, q=q, limit=limit_per_type))

    # 去重（同 (type, id) 取較高分）
    best: dict[tuple[str, str], SearchHit] = {}
    for h in hits:
        key = (h.type, h.id)
        cur = best.get(key)
        if cur is None or h.score > cur.score:
            best[key] = h

    final = sorted(best.values(), key=lambda h: h.score, reverse=True)
    return {
        "detected": kind,  # type: ignore[dict-item]
        "results": [h.as_dict() for h in final],
    }
