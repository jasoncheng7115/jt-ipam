"""OUI vendor lookup + refresh service。

Source: Wireshark `manuf` file（每月更新；URL 穩定多年）。
備援來源：本地 cache（lib 包的 dataset）。

格式（每行）：
  00:00:00      Officially Xerox        Officially Xerox
  00:01:42/24   Cisco                   Cisco Systems, Inc
  00:50:C2:00:30:00/36  ...

我們只取 24-bit prefix（前 3 個 octet），忽略 MA-M (28-bit) / MA-S (36-bit)
細分區段；對 IPAM 場景已夠用。
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.safe_http import safe_request
from app.models.oui import OUIVendor

logger = logging.getLogger(__name__)

# Wireshark mirror — gitlab + github 雙路試
SOURCES = [
    "https://www.wireshark.org/download/automated/data/manuf",
    "https://gitlab.com/wireshark/wireshark/-/raw/master/manuf",
]

# 解析 manuf 格式：prefix \t short_name \t long_name (\t 或多空白分隔)
# prefix 可能是 "00:00:0C" 或 "00:00:0C/24" 或 "00:50:C2:00:30:00/36"
_LINE_RE = re.compile(
    r"^(?P<prefix>[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2})+)(?:/(?P<masklen>\d+))?\s+"
    r"(?P<short>\S+)(?:\s+(?P<long>.+?))?\s*$"
)


def _normalize_mac_prefix(s: str) -> str | None:
    """'00:00:0C' → '00000C'；只取前 3 octet (24 bits)；非法回 None。"""
    hex_only = re.sub(r"[^0-9A-Fa-f]", "", s)
    if len(hex_only) < 6:
        return None
    return hex_only[:6].upper()


async def _download_manuf() -> str:
    """下載 wireshark manuf；多 mirror 試。"""
    last_exc: Exception | None = None
    for url in SOURCES:
        try:
            resp = await safe_request("GET", url, timeout=60.0)
            if resp.status_code == 200 and len(resp.text) > 100_000:
                return resp.text
            last_exc = RuntimeError(f"{url}: HTTP {resp.status_code} len={len(resp.text)}")
        except httpx.HTTPError as exc:
            last_exc = exc
    raise RuntimeError(f"all OUI mirrors failed; last: {last_exc}")


def _parse_manuf(text: str) -> list[dict[str, Any]]:
    """解析 wireshark manuf；回傳 [{prefix, short_name, name, source}, ...]"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        masklen = int(m.group("masklen") or 24)
        # 只收 24 bit OUI；MA-M (28) / MA-S (36) 跳過（會被 24-bit 覆蓋）
        if masklen != 24:
            continue
        prefix = _normalize_mac_prefix(m.group("prefix"))
        if not prefix or prefix in seen:
            continue
        seen.add(prefix)
        short = (m.group("short") or "").strip() or None
        long_ = (m.group("long") or "").strip() or short or "unknown"
        out.append({
            "prefix": prefix,
            "short_name": short,
            "name": long_,
            "source": "wireshark",
        })
    return out


async def refresh_oui_db(session: AsyncSession) -> dict[str, int]:
    """從 wireshark manuf 拉取並 upsert 進 oui_vendors。

    回傳 {downloaded, parsed, inserted, updated}。
    """
    text = await _download_manuf()
    entries = _parse_manuf(text)
    if not entries:
        return {"downloaded": len(text), "parsed": 0, "inserted": 0, "updated": 0}

    # 既有 prefix
    existing = {
        r[0]: r[1] for r in (
            await session.execute(select(OUIVendor.prefix, OUIVendor.name))
        ).all()
    }

    inserted = updated = 0
    BATCH = 1000
    for i in range(0, len(entries), BATCH):
        chunk = entries[i:i + BATCH]
        # ON CONFLICT DO UPDATE
        stmt = pg_insert(OUIVendor).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["prefix"],
            set_={
                "short_name": stmt.excluded.short_name,
                "name": stmt.excluded.name,
                "source": stmt.excluded.source,
                "updated_at": datetime.now(UTC),
            },
        )
        await session.execute(stmt)

    # 統計
    for e in entries:
        if e["prefix"] in existing:
            if existing[e["prefix"]] != e["name"]:
                updated += 1
        else:
            inserted += 1
    await session.commit()
    return {
        "downloaded": len(text),
        "parsed": len(entries),
        "inserted": inserted,
        "updated": updated,
    }


async def vendor_for_mac(session: AsyncSession, mac: str | None) -> str | None:
    """單筆 MAC → vendor 名稱；找不到回 None。"""
    if not mac:
        return None
    prefix = _normalize_mac_prefix(mac)
    if not prefix:
        return None
    row = (
        await session.execute(
            select(OUIVendor.short_name, OUIVendor.name).where(OUIVendor.prefix == prefix)
        )
    ).first()
    if not row:
        return None
    return row[0] or row[1]  # type: ignore[no-any-return]


async def vendor_map(session: AsyncSession, macs: list[str | None]) -> dict[str, str]:
    """批次查詢；回傳 {prefix: vendor_label}（key 是 6 char hex，給呼叫端 lookup 用）。"""
    prefixes: set[str] = set()
    for m in macs:
        p = _normalize_mac_prefix(m) if m else None
        if p:
            prefixes.add(p)
    if not prefixes:
        return {}
    rows = (
        await session.execute(
            select(OUIVendor.prefix, OUIVendor.short_name, OUIVendor.name)
            .where(OUIVendor.prefix.in_(prefixes))
        )
    ).all()
    return {r[0]: (r[1] or r[2]) for r in rows}


def mac_prefix(mac: str | None) -> str | None:
    """給 endpoint 用：MAC → 6 char hex prefix（給 vendor_map lookup）。"""
    return _normalize_mac_prefix(mac) if mac else None


def _fmt_prefix(p: str) -> str:
    """'00000C' → '00:00:0C'（顯示用）。"""
    return ":".join(p[i:i + 2] for i in range(0, len(p), 2))


async def search_oui_vendors(
    session: AsyncSession,
    *,
    prefix: str | None = None,
    name: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """依 OUI 首碼（如 '22' / '00:11' / '0011') 或廠商名子字串搜尋 OUI 紀錄。

    至少要給 prefix 或 name 其一。回傳 {count, truncated, vendors:[{prefix, name, short_name}]}。
    """
    limit = max(1, min(int(limit), 500))
    stmt = select(OUIVendor.prefix, OUIVendor.short_name, OUIVendor.name)
    conds: list[Any] = []
    if prefix:
        hex_only = re.sub(r"[^0-9A-Fa-f]", "", prefix).upper()
        if not hex_only:
            raise ValueError("prefix 需含至少一個 hex 字元")
        conds.append(OUIVendor.prefix.like(f"{hex_only}%"))
    if name:
        like = f"%{name.strip()}%"
        conds.append(OUIVendor.name.ilike(like) | OUIVendor.short_name.ilike(like))
    if not conds:
        raise ValueError("請提供 prefix 或 name 至少一項")
    for c in conds:
        stmt = stmt.where(c)
    # 多抓一筆判斷是否被截斷
    stmt = stmt.order_by(OUIVendor.prefix).limit(limit + 1)
    rows = (await session.execute(stmt)).all()
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {
        "count": len(rows),
        "truncated": truncated,
        "vendors": [
            {"prefix": _fmt_prefix(r[0]), "short_name": r[1], "name": r[2]} for r in rows
        ],
    }


async def stats(session: AsyncSession) -> dict[str, Any]:
    """OUI DB 統計 — count + last_updated。"""
    cnt = int(await session.scalar(select(func.count()).select_from(OUIVendor)) or 0)
    last = await session.scalar(select(func.max(OUIVendor.updated_at)))
    return {"count": cnt, "last_updated": last.isoformat() if last else None}
