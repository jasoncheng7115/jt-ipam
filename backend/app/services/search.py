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
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import String, cast, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.subnet import Subnet
from app.models.user import User
from app.models.vlan import VLAN
from app.services.permission import filter_visible

ResultType = Literal[
    "section", "subnet", "ip_address", "device", "vlan",
    "vpn", "customer", "rack", "location",
    "nat", "dns_record", "firewall", "ip_request",
]

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){2,5}[0-9A-Fa-f]{2}$|^[0-9A-Fa-f]{6,12}$")
# MAC 片段（前綴）：含分隔符的 hex 片段，如 "bc:24" / "bc:24:11" / "bc-24"。
# 至少一組分隔，每段 1~2 個 hex；用來讓使用者打部分 MAC 也能搜到。
_MAC_FRAGMENT_RE = re.compile(r"^[0-9A-Fa-f]{2}([:\-][0-9A-Fa-f]{1,2}){1,5}$")


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
    if _MAC_RE.match(q) or _MAC_FRAGMENT_RE.match(q):
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
                select(IPAddress).where(IPAddress.ip == ip, IPAddress.subnet_id.in_(select(Subnet.id).where(Subnet.archived_at.is_(None)))).limit(limit)
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
        WHERE (cidr = CAST(:q AS cidr)
           OR cidr >> CAST(:q AS cidr)
           OR cidr << CAST(:q AS cidr))
          AND archived_at IS NULL
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
                .where(cast(IPAddress.mac, String).ilike(f"%{cleaned}%"),
                       IPAddress.subnet_id.in_(select(Subnet.id).where(Subnet.archived_at.is_(None))))
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
    # 看起來像「IP 片段」：只含數字與點且至少有一個點
    #   e.g. "192.168"（首碼）/ ".1.189"（結尾）/ "1.189"（中段）/ "10.1."
    looks_like_ip_fragment = bool(re.match(r"^[0-9.]*\.[0-9.]*$", q)) and any(ch.isdigit() for ch in q)
    ipfrag = f"%{q}%"  # IP 片段一律走子字串比對（首碼/結尾/中段都能撞到）
    # IP 片段 / IP / CIDR / MAC 查詢時不要用 trigram 模糊比對（否則 .200 會誤中 .201/.208）
    fuzzy = not (looks_like_ip_fragment or _detect_query_kind(q) in ("ip", "cidr", "mac"))
    out: list[SearchHit] = []

    # IP 片段比對（子字串）：打 "192.168" 找 192.168.*；打 ".1.189" 找 *.1.189
    if looks_like_ip_fragment:
        # subnets whose cidr text contains the fragment
        sub_pref_sql = text(
            """
            SELECT id, cidr::text AS cidr, description
              FROM subnets
             WHERE host(cidr) LIKE :ipfrag OR cidr::text LIKE :ipfrag
             ORDER BY masklen(cidr)
             LIMIT :limit
            """
        )
        sub_pref_rows = (
            await session.execute(sub_pref_sql, {"ipfrag": ipfrag, "limit": limit})
        ).all()
        if sub_pref_rows:
            visible = set(
                await filter_visible(
                    session, user=user, object_type="subnet",
                    object_ids=[r.id for r in sub_pref_rows], required="read",
                )
            )
            for r in sub_pref_rows:
                if r.id in visible:
                    out.append(SearchHit(
                        type="subnet", id=str(r.id), label=r.cidr,
                        sublabel=r.description, score=0.9,
                    ))

        # ip_addresses whose textual form contains the fragment
        ip_pref_sql = text(
            """
            SELECT a.id, host(a.ip) AS ip, a.hostname, a.subnet_id
              FROM ip_addresses a
             WHERE host(a.ip) LIKE :ipfrag
             ORDER BY a.ip
             LIMIT :limit
            """
        )
        ip_pref_rows = (
            await session.execute(ip_pref_sql, {"ipfrag": ipfrag, "limit": limit})
        ).all()
        if ip_pref_rows:
            visible_ip_subs = set(
                await filter_visible(
                    session, user=user, object_type="subnet",
                    object_ids=[r.subnet_id for r in ip_pref_rows], required="read",
                )
            )
            for r in ip_pref_rows:
                if r.subnet_id in visible_ip_subs:
                    out.append(SearchHit(
                        type="ip_address", id=str(r.id),
                        label=r.ip, sublabel=r.hostname, score=0.92,
                    ))

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
         WHERE (description ILIKE :qlike
            OR similarity(description, :q) > 0.2)
           AND archived_at IS NULL
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
         WHERE (a.hostname ILIKE :qlike
            OR a.description ILIKE :qlike
            OR similarity(a.hostname, :q) > 0.2)
           AND a.subnet_id IN (SELECT id FROM subnets WHERE archived_at IS NULL)
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
            OR (:fuzzy AND similarity(name, :q) > 0.3)
         ORDER BY score DESC
         LIMIT :limit
        """
    )
    dev_rows = (await session.execute(
        dev_sql, {"q": q, "qlike": qlike, "fuzzy": fuzzy, "limit": limit}
    )).all()
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

    # VPN tunnels（名稱 / 對端）
    vpn_sql = text(
        """
        SELECT id, name, type, status,
               GREATEST(similarity(name, :q), COALESCE(similarity(b_endpoint, :q), 0)) AS score
          FROM vpn_tunnels
         WHERE name ILIKE :qlike OR b_endpoint ILIKE :qlike
            OR (:fuzzy AND similarity(name, :q) > 0.3)
         ORDER BY score DESC LIMIT :limit
        """
    )
    for r in (await session.execute(vpn_sql, {"q": q, "qlike": qlike, "fuzzy": fuzzy, "limit": limit})).all():
        out.append(SearchHit(type="vpn", id=str(r.id), label=r.name,
                             sublabel=" ".join(filter(None, [r.type, r.status])) or None,
                             score=min(float(r.score or 0), 1.0)))

    # Customers（單位）
    cust_sql = text(
        """
        SELECT id, name, title,
               GREATEST(similarity(name, :q), COALESCE(similarity(title, :q), 0)) AS score
          FROM customers
         WHERE name ILIKE :qlike OR title ILIKE :qlike
            OR (:fuzzy AND similarity(name, :q) > 0.3)
         ORDER BY score DESC LIMIT :limit
        """
    )
    for r in (await session.execute(cust_sql, {"q": q, "qlike": qlike, "fuzzy": fuzzy, "limit": limit})).all():
        out.append(SearchHit(type="customer", id=str(r.id), label=r.name,
                             sublabel=r.title or None, score=min(float(r.score or 0), 1.0)))

    # Racks（機櫃）
    rack_sql = text(
        """
        SELECT id, name, similarity(name, :q) AS score
          FROM racks
         WHERE name ILIKE :qlike OR (:fuzzy AND similarity(name, :q) > 0.3)
         ORDER BY score DESC LIMIT :limit
        """
    )
    for r in (await session.execute(rack_sql, {"q": q, "qlike": qlike, "fuzzy": fuzzy, "limit": limit})).all():
        out.append(SearchHit(type="rack", id=str(r.id), label=r.name,
                             sublabel=None, score=min(float(r.score or 0), 1.0)))

    # Locations（地點）
    loc_sql = text(
        """
        SELECT id, name, similarity(name, :q) AS score
          FROM locations
         WHERE name ILIKE :qlike OR (:fuzzy AND similarity(name, :q) > 0.3)
         ORDER BY score DESC LIMIT :limit
        """
    )
    for r in (await session.execute(loc_sql, {"q": q, "qlike": qlike, "fuzzy": fuzzy, "limit": limit})).all():
        out.append(SearchHit(type="location", id=str(r.id), label=r.name,
                             sublabel=None, score=min(float(r.score or 0), 1.0)))

    # NAT 規則（名稱）
    nat_sql = text(
        """
        SELECT id, name, type, similarity(name, :q) AS score
          FROM nat_translations
         WHERE name ILIKE :qlike OR (:fuzzy AND similarity(name, :q) > 0.3)
         ORDER BY score DESC LIMIT :limit
        """
    )
    for r in (await session.execute(nat_sql, {"q": q, "qlike": qlike, "fuzzy": fuzzy, "limit": limit})).all():
        out.append(SearchHit(type="nat", id=str(r.id), label=r.name, sublabel=r.type,
                             score=min(float(r.score or 0), 1.0)))

    # DNS 紀錄（名稱 / 值）
    dns_sql = text(
        """
        SELECT id, name, type, value,
               GREATEST(similarity(name, :q), COALESCE(similarity(value, :q), 0)) AS score
          FROM dns_records
         WHERE name ILIKE :qlike OR value ILIKE :qlike OR (:fuzzy AND similarity(name, :q) > 0.3)
         ORDER BY score DESC LIMIT :limit
        """
    )
    for r in (await session.execute(dns_sql, {"q": q, "qlike": qlike, "fuzzy": fuzzy, "limit": limit})).all():
        out.append(SearchHit(type="dns_record", id=str(r.id), label=r.name,
                             sublabel=" ".join(filter(None, [r.type, r.value])) or None,
                             score=min(float(r.score or 0), 1.0)))

    # 防火牆（名稱）
    fw_sql = text(
        """
        SELECT id, name, similarity(name, :q) AS score
          FROM opnsense_firewalls
         WHERE name ILIKE :qlike OR (:fuzzy AND similarity(name, :q) > 0.3)
         ORDER BY score DESC LIMIT :limit
        """
    )
    for r in (await session.execute(fw_sql, {"q": q, "qlike": qlike, "fuzzy": fuzzy, "limit": limit})).all():
        out.append(SearchHit(type="firewall", id=str(r.id), label=r.name, sublabel=None,
                             score=min(float(r.score or 0), 1.0)))

    # IP 申請（requested_ip / hostname）
    ipr_sql = text(
        """
        SELECT id, requested_ip, hostname, status,
               GREATEST(COALESCE(similarity(host(requested_ip), :q), 0), COALESCE(similarity(hostname, :q), 0)) AS score
          FROM ip_requests
         WHERE host(requested_ip) ILIKE :qlike OR hostname ILIKE :qlike
            OR (:fuzzy AND similarity(COALESCE(hostname, ''), :q) > 0.3)
         ORDER BY score DESC LIMIT :limit
        """
    )
    for r in (await session.execute(ipr_sql, {"q": q, "qlike": qlike, "fuzzy": fuzzy, "limit": limit})).all():
        lbl = str(r.requested_ip).split("/")[0] if r.requested_ip else (r.hostname or "(request)")
        out.append(SearchHit(type="ip_request", id=str(r.id), label=lbl,
                             sublabel=" ".join(filter(None, [r.hostname, r.status])) or None,
                             score=min(float(r.score or 0), 1.0)))

    # VLAN（名稱模糊；數字已於 vlan_number kind 處理）
    vln_sql = text(
        """
        SELECT id, number, name, similarity(name, :q) AS score
          FROM vlans
         WHERE name ILIKE :qlike OR (:fuzzy AND similarity(name, :q) > 0.3)
         ORDER BY score DESC LIMIT :limit
        """
    )
    for r in (await session.execute(vln_sql, {"q": q, "qlike": qlike, "fuzzy": fuzzy, "limit": limit})).all():
        out.append(SearchHit(type="vlan", id=str(r.id), label=f"VLAN {r.number} — {r.name}",
                             sublabel=None, score=min(float(r.score or 0), 1.0)))

    # RBAC：過濾掉不可見的 device/customer/rack/location 結果（subnet/section/ip 已於各區塊內過濾）
    from app.services.permission import visible_ids
    for otype in ("device", "customer", "rack", "location"):
        vis = await visible_ids(session, user=user, object_type=otype)
        if vis is not None:
            allow = {str(x) for x in vis}
            out = [h for h in out if h.type != otype or h.id in allow]

    # 子字串優先：只要有任一「label/sublabel 含查詢字」的命中，
    # 就濾掉所有純 trigram 相似的命中（查 "nas2" 不該冒出 nas3 / nas4 / NAS4）。
    # 完全沒有子字串命中時（例如打錯字）才保留模糊結果，維持容錯。
    ql = q.lower()

    def _is_substr(h: SearchHit) -> bool:
        return ql in (h.label or "").lower() or ql in (h.sublabel or "").lower()

    if any(_is_substr(h) for h in out):
        out = [h for h in out if _is_substr(h)]

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
        return {"detected": "empty", "results": []}  # type: ignore[dict-item]

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

    # RBAC：全域基礎設施類結果（VLAN / NAT / 防火牆 / 站對站 VPN / DNS / IP 申請）
    # 只有管理員或具萬用讀取權限者可見；只被指派特定物件的帳號不得從搜尋窺見。
    from app.services.permission import visible_ids as _vis
    is_global = user.is_admin
    if not is_global:
        for ot in ("subnet", "device", "customer", "section", "rack", "location"):
            if await _vis(session, user=user, object_type=ot) is None:
                is_global = True
                break
    if not is_global:
        _global_types = {"vlan", "nat", "firewall", "vpn", "dns_record", "ip_request"}
        final = [h for h in final if h.type not in _global_types]

    return {
        "detected": kind,  # type: ignore[dict-item]
        "results": [h.as_dict() for h in final],
    }
