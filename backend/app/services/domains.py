"""網域管理（客戶許願清單 2026-10-28）：RDAP 查註冊商、到期日與 NS，從 NS 推測 DNS 託管在哪家，到期前通知。

RDAP（RFC 9083）是註冊管理機構的標準查詢協定。哪個頂級網域問哪台伺服器，照 IANA 的 bootstrap 檔
（https://data.iana.org/rdap/dns.json）。不是每個頂級網域都有 RDAP（2026-10 實測：.com、.net、.org、.tw、
.dev、.app 有；.io、.jp、.cn、.de、.hk 沒有）—— 沒有的回 `rdap_no_service`，到期日改用手填的。

查的是**註冊的網域本身**（example.com、example.com.tw），不是子網域；子網域會被註冊管理機構回 404。
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.core.ui_error import UiError
from app.models.domain import Domain

BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
_TIMEOUT = 20.0
_BOOTSTRAP_TTL = 24 * 3600
_bootstrap: dict[str, Any] = {"at": 0.0, "map": {}}

#: 到期前幾天通知（含 0＝到期當天／已過期）。網域續約常要事先付款，所以比憑證早
THRESHOLDS = (30, 14, 7, 1, 0)
EVENT = "domain.expiring"
STATE_KEY = "domain_expiry_notified"

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class DomainError(UiError):
    """RDAP 查詢失敗（查不到、沒有 RDAP 服務、連不上）—— 屬上游問題。"""


class DomainInputError(DomainError):
    """使用者輸入的不是網域名稱 —— 用戶端問題。"""


def normalize_domain(raw: str) -> str:
    """小寫、去掉結尾的點與前後空白、中文網域轉成 punycode；同時是輸入驗證（這個值會接進查詢網址）。"""
    v = (raw or "").strip().rstrip(".").lower()
    if v.startswith(("http://", "https://")):
        v = v.split("://", 1)[1].split("/", 1)[0]
    try:
        v = v.encode("idna").decode("ascii") if any(ord(ch) > 127 for ch in v) else v
    except UnicodeError as exc:
        raise DomainInputError(f"不是有效的網域名稱：{raw}", code="domain_invalid", value=str(raw)[:80]) from exc
    labels = v.split(".")
    if len(v) > 253 or len(labels) < 2 or not all(_LABEL.match(x) for x in labels) or labels[-1].isdigit():
        raise DomainInputError(f"不是有效的網域名稱：{raw}", code="domain_invalid", value=str(raw)[:80])
    return v


@dataclass
class RdapDomain:
    registrar: str | None = None
    registered_at: datetime | None = None
    expires_at: datetime | None = None
    nameservers: list[str] = field(default_factory=list)
    status: list[str] = field(default_factory=list)


def _ts(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def parse_rdap_domain(d: dict[str, Any]) -> RdapDomain:
    """RDAP 的 domain 物件 → 我們要的欄位（純函式，好測）。"""
    from app.services.rdap import _entity_name
    out = RdapDomain(status=[str(s) for s in (d.get("status") or [])])
    for e in d.get("events") or []:
        act = str(e.get("eventAction") or "").lower()
        if act == "registration":
            out.registered_at = _ts(e.get("eventDate"))
        elif act == "expiration":
            out.expires_at = _ts(e.get("eventDate"))
    out.nameservers = sorted({str(n.get("ldhName")).lower().rstrip(".")
                              for n in d.get("nameservers") or [] if n.get("ldhName")})
    for e in d.get("entities") or []:
        if "registrar" in (e.get("roles") or []):
            out.registrar = _entity_name(e) or e.get("handle")
            break
    return out


# 從 NS 推測 DNS 託管在哪家（後綴比對；推不出來就不寫，畫面標「推測」）
_DNS_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("ns.cloudflare.com", "Cloudflare"),
    ("awsdns-", "AWS Route 53"),
    ("azure-dns.", "Azure DNS"),
    ("googledomains.com", "Google Domains"),
    ("ns-cloud-", "Google Cloud DNS"),
    ("domaincontrol.com", "GoDaddy"),
    ("registrar-servers.com", "Namecheap"),
    ("gandi.net", "Gandi"),
    ("ovh.net", "OVHcloud"),
    ("dnsimple.com", "DNSimple"),
    ("nsone.net", "NS1"),
    ("akam.net", "Akamai"),
    ("ultradns.", "UltraDNS"),
    ("digitalocean.com", "DigitalOcean"),
    ("linode.com", "Linode"),
    ("hetzner.com", "Hetzner"),
    ("vercel-dns.com", "Vercel"),
    ("netlify.com", "Netlify"),
    ("hinet.net", "HiNet"),
    ("seed.net.tw", "SEEDNet"),
    ("pchome.com.tw", "PChome"),
    ("gname.net", "Gname"),
)


def infer_dns_provider(nameservers: list[str] | None) -> str | None:
    found: list[str] = []
    for ns in nameservers or []:
        for pat, name in _DNS_PROVIDERS:
            if pat in ns and name not in found:
                found.append(name)
    return " / ".join(found) or None


async def _service_map() -> dict[str, str]:
    now = time.monotonic()
    if _bootstrap["map"] and now - _bootstrap["at"] < _BOOTSTRAP_TTL:
        return _bootstrap["map"]
    try:
        resp = await safe_request("GET", BOOTSTRAP_URL, timeout=_TIMEOUT, max_bytes=2_000_000)
        resp.raise_for_status()
        data = resp.json()
    except UnsafeOutboundURL as exc:
        raise DomainError(f"查詢被安全檢查擋下：{exc}", code="rdap_ssrf", reason=str(exc)[:200]) from exc
    except Exception as exc:
        raise DomainError(f"連不上 IANA 的 RDAP 清單：{exc}", code="rdap_bootstrap_failed",
                          reason=str(exc)[:200]) from exc
    m: dict[str, str] = {}
    for svc in data.get("services") or []:
        if isinstance(svc, list) and len(svc) == 2 and svc[1]:
            base = next((u for u in svc[1] if str(u).startswith("https://")), svc[1][0])
            for tld in svc[0]:
                m[str(tld).lower()] = str(base).rstrip("/") + "/"
    _bootstrap.update(at=now, map=m)
    return m


async def lookup_domain(name: str) -> RdapDomain:
    n = normalize_domain(name)
    base = (await _service_map()).get(n.rsplit(".", 1)[-1])
    if not base:
        raise DomainError(f".{n.rsplit('.', 1)[-1]} 沒有 RDAP 服務，到期日請手填", code="rdap_no_service",
                          tld=n.rsplit(".", 1)[-1])
    url = f"{base}domain/{n}"
    try:
        resp = await safe_request("GET", url, timeout=_TIMEOUT, max_bytes=1_000_000,
                                  headers={"Accept": "application/rdap+json"})
    except UnsafeOutboundURL as exc:
        raise DomainError(f"查詢被安全檢查擋下：{exc}", code="rdap_ssrf", reason=str(exc)[:200]) from exc
    except Exception as exc:
        raise DomainError(f"連不上 {base}：{exc}", code="rdap_unreachable", base=base, reason=str(exc)[:200]) from exc
    if resp.status_code == 404:
        raise DomainError(f"註冊管理機構查無 {n}（要輸入註冊的網域本身，不是子網域）", code="rdap_not_found",
                          name=n)
    if resp.status_code == 429:
        raise DomainError("註冊管理機構限制查詢次數，稍後再試", code="rdap_rate_limited")
    if resp.status_code >= 400:
        raise DomainError(f"註冊管理機構回應 HTTP {resp.status_code}", code="rdap_http", status=resp.status_code)
    try:
        data = resp.json()
    except ValueError as exc:
        raise DomainError("對方回的不是 JSON", code="rdap_not_json") from exc
    if not isinstance(data, dict):
        raise DomainError("RDAP 回應格式非預期", code="rdap_bad_shape")
    return parse_rdap_domain(data)


async def refresh(session: AsyncSession, d: Domain) -> bool:
    """查一次 RDAP 寫回；失敗只記代碼，**不清掉**上次查到的（暫時連不上不該讓到期日消失）。"""
    d.rdap_checked_at = datetime.now(UTC)
    try:
        r = await lookup_domain(d.name)
    except DomainError as exc:
        d.rdap_error = exc.code or "rdap_failed"
        return False
    d.rdap_error = None
    d.rdap_registrar = (r.registrar or "")[:255] or None
    d.rdap_registered_at = r.registered_at
    d.rdap_expires_at = r.expires_at
    d.rdap_nameservers = r.nameservers
    d.rdap_status = r.status
    return True


def _tz() -> ZoneInfo:
    """日期一律用站台時區（`APP_TIMEZONE`，預設 Asia/Taipei）換算：用 UTC 的話，台灣早上 8 點前
    「今天」還是前一天，手填 100 天後的日期會顯示「剩 101 天」。"""
    from app.core.config import get_settings
    try:
        return ZoneInfo(get_settings().app_timezone)
    except Exception:
        return ZoneInfo("UTC")


def today() -> date:
    return datetime.now(_tz()).date()


def effective_expiry(d: Domain) -> date | None:
    """RDAP 查得到就用 RDAP（權威）；查不到的頂級網域用手填的。"""
    if d.rdap_expires_at is not None:
        return d.rdap_expires_at.astimezone(_tz()).date()
    return d.expires_at


def days_left(d: Domain, on: date | None = None) -> int | None:
    e = effective_expiry(d)
    if e is None:
        return None
    return (e - (on or today())).days


def bucket(left: int) -> int | None:
    if left > THRESHOLDS[0]:
        return None
    return min(t for t in THRESHOLDS if left <= t) if left > 0 else 0


async def refresh_all(session: AsyncSession, *, limit: int = 500, pause: float = 1.0,
                      stale_hours: float | None = None) -> dict[str, int]:
    """照最久沒查的先查，每筆之間停一下（註冊管理機構有查詢次數限制）。

    排程每 5 分鐘呼叫一次、`stale_hours=20`：只查超過 20 小時沒查的，一次最多 `limit` 筆 ——
    等於每個網域大約一天查一次，網域很多時自然分散到好幾輪，不必另外記「今天跑過沒」。
    """
    stmt = select(Domain)
    if stale_hours is not None:
        cutoff = datetime.now(UTC) - timedelta(hours=stale_hours)
        stmt = stmt.where(or_(Domain.rdap_checked_at.is_(None), Domain.rdap_checked_at < cutoff))
    rows = list((await session.execute(
        stmt.order_by(Domain.rdap_checked_at.asc().nulls_first()).limit(limit))).scalars())
    ok = failed = 0
    for i, d in enumerate(rows):
        if i:
            await asyncio.sleep(pause)
        if await refresh(session, d):
            ok += 1
        else:
            failed += 1
        await session.flush()
    return {"checked": len(rows), "ok": ok, "failed": failed}


async def _load_state(session: AsyncSession) -> dict[str, int]:
    from app.models.system_setting import SystemSetting
    row = await session.get(SystemSetting, STATE_KEY)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


async def _save_state(session: AsyncSession, state: dict[str, int]) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.system_setting import SystemSetting
    row = await session.get(SystemSetting, STATE_KEY)
    if row is None:
        session.add(SystemSetting(key=STATE_KEY, value=state))
    else:
        row.value = state
        flag_modified(row, "value")
    await session.flush()


async def check_domain_expiry(session: AsyncSession) -> dict[str, int]:
    """到期前 30、14、7、1 天與到期當天各通知管理員一次（站內、Email、通訊軟體照通知矩陣）。"""
    from app.services.notification import notify_admins_event
    now_day = today()
    state = await _load_state(session)
    live: dict[str, int] = {}
    notified = 0
    rows = list((await session.execute(select(Domain))).scalars())
    for d in rows:
        left = days_left(d, now_day)
        key = f"{d.id}:{effective_expiry(d)}"          # 續約後到期日變了＝重新從頭算
        if key in state:
            live[key] = state[key]
        if left is None or left < -1:
            continue
        bkt = bucket(left)
        if bkt is None or state.get(key) == bkt:
            continue
        live[key] = bkt
        expired = left <= 0
        exp = effective_expiry(d)
        params = {"name": d.name, "date": exp.isoformat() if exp else "", "days": max(left, 0)}
        title = f"網域{'已到期' if expired else '即將到期'}：{d.name}"
        remain = "" if expired else f"（剩 {left} 天）"
        body = f"{d.name} {'已於' if expired else '將於'} {params['date']} 到期{remain}，請確認續約。"
        await notify_admins_event(
            session, event=EVENT, title=title, body=body, severity="error" if expired else "warning",
            link=f"/advanced/domains/{d.id}", object_type="domain", object_id=d.id,
            title_key="notif.domain_expired" if expired else "notif.domain_expiring",
            body_key="notif.domain_expired_body" if expired else "notif.domain_expiring_body", params=params)
        notified += 1
    if live != state:
        await _save_state(session, live)
    return {"checked": len(rows), "notified": notified}


def summary_for_ai(d: Domain, on: date | None = None) -> dict[str, Any]:
    exp = effective_expiry(d)
    return {
        "name": d.name, "registrar": d.registrar or d.rdap_registrar, "registrar_url": d.registrar_url,
        "registrar_account": d.registrar_account,
        "dns_provider": d.dns_provider or infer_dns_provider(d.rdap_nameservers),
        "dns_provider_inferred": not d.dns_provider and bool(infer_dns_provider(d.rdap_nameservers)),
        "nameservers": d.rdap_nameservers or [], "expires": exp.isoformat() if exp else None,
        "expires_source": "rdap" if d.rdap_expires_at else ("manual" if d.expires_at else None),
        "days_left": days_left(d, on), "auto_renew": d.auto_renew, "services": d.services,
        "registered_at": d.rdap_registered_at.date().isoformat() if d.rdap_registered_at else None,
        "rdap_error": d.rdap_error, "description": d.description,
    }


__all__ = [
    "EVENT",
    "THRESHOLDS",
    "DomainError",
    "DomainInputError",
    "bucket",
    "check_domain_expiry",
    "days_left",
    "effective_expiry",
    "infer_dns_provider",
    "lookup_domain",
    "normalize_domain",
    "parse_rdap_domain",
    "refresh",
    "refresh_all",
    "summary_for_ai",
]
