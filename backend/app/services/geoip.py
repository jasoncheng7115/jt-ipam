"""GeoIP：本地 MaxMind mmdb（自動排程更新）優先，無檔時退回 GeoLite2 web service。

管理者在系統設定填 MaxMind account ID + license key（後者加密存），選 editions
（GeoLite2-City / GeoLite2-ASN / 付費 GeoIP2-City…）與更新頻率。systemd timer 每日
觸發 scripts/geoip_refresh.py，依設定頻率決定是否真的下載。
"""
from __future__ import annotations

import base64
import io
import tarfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.core.security import decrypt_secret, encrypt_secret
from app.models.system_setting import SystemSetting

GEOIP_KEY = "geoip"
_WS_HOST = "geolite.info"                          # web service fallback
_DL_BASE = "https://download.maxmind.com/geoip/databases"   # 本地 DB 下載
DB_DIR = Path("/var/lib/jt-ipam/geoip")
DEFAULT_EDITIONS = ["GeoLite2-City", "GeoLite2-ASN"]
ALL_EDITIONS = [
    "GeoLite2-City", "GeoLite2-Country", "GeoLite2-ASN",   # 免費
    "GeoIP2-City", "GeoIP2-Country", "GeoIP2-ISP",         # 付費
]
# 更新頻率 → 最小間隔秒數（timer 每日觸發，script 依此判斷是否到期）
FREQUENCIES: dict[str, int] = {
    "daily": 86400, "twice-weekly": 3 * 86400, "weekly": 7 * 86400, "monthly": 30 * 86400,
}
DEFAULT_FREQUENCY = "twice-weekly"


def _db_path(edition: str) -> Path:
    return DB_DIR / f"{edition}.mmdb"


async def _row(session: AsyncSession) -> dict[str, Any]:
    r = await session.get(SystemSetting, GEOIP_KEY)
    return dict(r.value) if r and isinstance(r.value, dict) else {}


async def get_geoip_creds(session: AsyncSession) -> tuple[str | None, str | None]:
    v = await _row(session)
    acct = v.get("account_id") or None
    key: str | None = None
    if v.get("key_ct") and v.get("key_nonce"):
        try:
            key = decrypt_secret(base64.b64decode(v["key_ct"]), base64.b64decode(v["key_nonce"])).decode()
        except Exception:
            key = None
    return acct, key


async def get_geoip_config(session: AsyncSession) -> dict[str, Any]:
    v = await _row(session)
    acct, key = await get_geoip_creds(session)
    editions = v.get("editions") or DEFAULT_EDITIONS
    dbs = []
    for ed in editions:
        p = _db_path(ed)
        exists = p.exists()
        dbs.append({
            "edition": ed,
            "present": exists,
            "size": p.stat().st_size if exists else None,
            "built_at": datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat() if exists else None,
        })
    return {
        "account_id": acct,
        "has_key": bool(key),
        "editions": editions,
        "auto_update": bool(v.get("auto_update", False)),
        "frequency": v.get("frequency", DEFAULT_FREQUENCY),
        "last_update_at": v.get("last_update_at"),
        "last_error": v.get("last_error"),
        "dbs": dbs,
    }


async def set_geoip_config(
    session: AsyncSession, *,
    account_id: str | None = None, license_key: str | None = None,
    editions: list[str] | None = None, auto_update: bool | None = None,
    frequency: str | None = None, updated_by: uuid.UUID | None = None,
) -> None:
    from sqlalchemy.orm.attributes import flag_modified
    row = await session.get(SystemSetting, GEOIP_KEY)
    if row is None:
        row = SystemSetting(key=GEOIP_KEY, value={}, updated_by=updated_by)
        session.add(row)
    cur: dict[str, Any] = dict(row.value or {})
    if account_id is not None:
        cur["account_id"] = account_id.strip()
    if license_key:
        ct, nonce = encrypt_secret(license_key.strip())
        cur["key_ct"] = base64.b64encode(ct).decode()
        cur["key_nonce"] = base64.b64encode(nonce).decode()
    if editions is not None:
        cur["editions"] = [e for e in editions if e in ALL_EDITIONS] or DEFAULT_EDITIONS
    if auto_update is not None:
        cur["auto_update"] = bool(auto_update)
    if frequency is not None and frequency in FREQUENCIES:
        cur["frequency"] = frequency
    row.value = cur
    row.updated_by = updated_by
    flag_modified(row, "value")
    await session.commit()


async def _mark(session: AsyncSession, *, error: str | None) -> None:
    from sqlalchemy.orm.attributes import flag_modified
    row = await session.get(SystemSetting, GEOIP_KEY)
    if row is None:
        return
    cur = dict(row.value or {})
    cur["last_update_at"] = datetime.now(UTC).isoformat()
    cur["last_error"] = error
    row.value = cur
    flag_modified(row, "value")
    await session.commit()


async def update_databases(session: AsyncSession) -> dict[str, Any]:
    """依設定下載/更新所有選定 edition 的 mmdb。回傳每個 edition 的結果。"""
    acct, key = await get_geoip_creds(session)
    if not acct or not key:
        return {"error": "not_configured"}
    cfg = await get_geoip_config(session)
    editions: list[str] = cfg["editions"]
    try:
        DB_DIR.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — 一次性建目錄，可接受同步
    except OSError as exc:
        await _mark(session, error=f"mkdir {DB_DIR}: {exc}")
        return {"error": f"mkdir_failed: {exc}"}

    results: dict[str, Any] = {}
    any_err: str | None = None
    for ed in editions:
        # 用 legacy geoip_download 端點（license_key 走 query param）。
        # 新版 permalink 端點會 302 轉到 S3，而把 Authorization 標頭一起帶過去會被
        # S3 當成 AWS 簽章 → 400 InvalidRequest。query-param 版沒這問題。
        url = (f"https://download.maxmind.com/app/geoip_download"
               f"?edition_id={quote(ed)}&license_key={quote(key)}&suffix=tar.gz")
        try:
            resp = await safe_request("GET", url, timeout=120.0)
            if resp.status_code != 200:
                results[ed] = {"ok": False, "error": f"http_{resp.status_code}"}
                any_err = f"{ed}: http_{resp.status_code}"
                continue
            # tar.gz 內含 {edition}_{date}/{edition}.mmdb
            mmdb_bytes = _extract_mmdb(resp.content)
            if mmdb_bytes is None:
                results[ed] = {"ok": False, "error": "no_mmdb_in_archive"}
                any_err = f"{ed}: no_mmdb"
                continue
            tmp = _db_path(ed).with_suffix(".mmdb.tmp")
            tmp.write_bytes(mmdb_bytes)
            tmp.replace(_db_path(ed))   # 原子替換
            results[ed] = {"ok": True, "size": len(mmdb_bytes)}
        except (UnsafeOutboundURL, httpx.HTTPError, OSError) as exc:
            results[ed] = {"ok": False, "error": str(exc)}
            any_err = f"{ed}: {exc}"
    await _mark(session, error=any_err)
    return {"results": results}


def _extract_mmdb(tar_gz: bytes) -> bytes | None:
    with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
        for m in tf.getmembers():
            if m.name.endswith(".mmdb"):
                f = tf.extractfile(m)
                if f is not None:
                    return f.read()
    return None


# ── 查詢 ──────────────────────────────────────────────────────
_reader_cache: dict[str, tuple[float, Any]] = {}


def _reader(edition: str):  # type: ignore[no-untyped-def]
    """快取的 maxminddb reader（檔案 mtime 變了就重開）。"""
    p = _db_path(edition)
    if not p.exists():
        return None
    mtime = p.stat().st_mtime
    cached = _reader_cache.get(edition)
    if cached and cached[0] == mtime:
        return cached[1]
    import geoip2.database
    try:
        rd = geoip2.database.Reader(str(p))
    except Exception:
        return None
    _reader_cache[edition] = (mtime, rd)
    return rd


def _local_lookup(ip: str) -> dict[str, Any] | None:
    """用本地 mmdb 查（City/Country + ASN）。沒有任何本地 DB 回 None。"""
    city_rd = _reader("GeoLite2-City") or _reader("GeoIP2-City")
    country_rd = _reader("GeoLite2-Country") or _reader("GeoIP2-Country")
    asn_rd = _reader("GeoLite2-ASN")
    if not (city_rd or country_rd or asn_rd):
        return None
    out: dict[str, Any] = {"ip": ip, "source": "local-db"}
    try:
        if city_rd is not None:
            r = city_rd.city(ip)
            out.update({
                "country": r.country.name, "country_iso": r.country.iso_code,
                "city": r.city.name,
                "subdivisions": [s.name for s in r.subdivisions if s.name],
                "postal": r.postal.code,
                "latitude": r.location.latitude, "longitude": r.location.longitude,
                "time_zone": r.location.time_zone, "accuracy_radius": r.location.accuracy_radius,
            })
        elif country_rd is not None:
            r = country_rd.country(ip)
            out.update({"country": r.country.name, "country_iso": r.country.iso_code})
        if asn_rd is not None:
            a = asn_rd.asn(ip)
            out.update({"asn": a.autonomous_system_number, "as_org": a.autonomous_system_organization,
                        "network": str(a.network) if a.network else None})
    except Exception as exc:
        if not any(k in out for k in ("country", "asn")):
            return {"ip": ip, "source": "local-db", "error": f"not_found: {type(exc).__name__}"}
    return out


async def geoip_lookup(session: AsyncSession, ip: str) -> dict[str, Any]:
    # 1) 本地 mmdb 優先（離線、快）
    local = _local_lookup(ip)
    if local is not None:
        return local
    # 2) 退回 web service（需憑證）
    acct, key = await get_geoip_creds(session)
    if not acct or not key:
        return {"ip": ip, "error": "not_configured"}
    auth = base64.b64encode(f"{acct}:{key}".encode()).decode()
    url = f"https://{_WS_HOST}/geoip/v2.1/city/{ip}"
    try:
        resp = await safe_request("GET", url, headers={"Authorization": f"Basic {auth}"}, timeout=10.0)
    except (UnsafeOutboundURL, httpx.HTTPError) as exc:
        return {"ip": ip, "error": f"request_failed: {exc}"}
    if resp.status_code == 401:
        return {"ip": ip, "error": "auth_failed"}
    if resp.status_code != 200:
        return {"ip": ip, "error": f"http_{resp.status_code}"}
    d = resp.json()
    loc = d.get("location") or {}
    traits = d.get("traits") or {}
    return {
        "ip": ip, "source": "web-service",
        "country": (d.get("country") or {}).get("names", {}).get("en"),
        "country_iso": (d.get("country") or {}).get("iso_code"),
        "city": (d.get("city") or {}).get("names", {}).get("en"),
        "subdivisions": [s.get("names", {}).get("en") for s in (d.get("subdivisions") or []) if s.get("names")],
        "postal": (d.get("postal") or {}).get("code"),
        "latitude": loc.get("latitude"), "longitude": loc.get("longitude"),
        "time_zone": loc.get("time_zone"), "accuracy_radius": loc.get("accuracy_radius"),
        "network": traits.get("network"), "asn": traits.get("autonomous_system_number"),
        "as_org": traits.get("autonomous_system_organization"),
    }


async def maybe_scheduled_update(session: AsyncSession) -> dict[str, Any]:
    """geoip_refresh.py 用：依 auto_update + frequency 判斷是否到期，到期才更新。"""
    cfg = await get_geoip_config(session)
    if not cfg["auto_update"]:
        return {"skipped": "auto_update_off"}
    interval = FREQUENCIES.get(cfg["frequency"], FREQUENCIES[DEFAULT_FREQUENCY])
    last = cfg.get("last_update_at")
    if last:
        try:
            last_ts = datetime.fromisoformat(last).timestamp()
            if time.time() - last_ts < interval:
                return {"skipped": "not_due"}
        except ValueError:
            pass
    return await update_databases(session)
