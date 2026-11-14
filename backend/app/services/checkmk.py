"""Checkmk 同步（REST API，全程唯讀）。

**定位**跟 Zabbix 一樣是監控面補充，拿得到、也只承諾這四件事：
1. 主機↔IP 對應 → 主機名稱來源之一
2. 存活狀態（主機 UP）→ 上線判定的證據來源
3. 監控涵蓋落差（IPAM 有、Checkmk 沒監控的位址）
4. 停機排程（scheduled downtime）→ 畫面看得到「維護中」

**只讀監控端點** `GET /domain-types/host/collections/all`（livestatus）。內建的 guest 角色就讀得到全部主機；
讀「設定」的 `host_config` 要更高的權限，而且權限不夠時**靜靜回 0 筆、不報錯**（2.4／2.5 實測），所以不用它。
一次呼叫拿回全部主機（分散式監控時中央網站會一併問各網站），沒有逐台查詢。

網址：使用者填瀏覽器上看到的網站網址（`https://主機/網站名稱`），API 路徑由這裡補成
`/網站名稱/check_mk/api/1.0`（2.0 起都有；2.5 另有 `api/v1`，兩者同一份）。
認證：`Authorization: Bearer <自動化帳號> <automation secret>`。
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.safe_http import UnsafeOutboundURL, safe_request, transport_detail
from app.core.security import decrypt_secret, encrypt_secret
from app.core.sqlin import in_values
from app.core.ui_error import UiError
from app.models.address import IPAddress
from app.models.checkmk import CheckmkHost, CheckmkInstance
from app.services.ip_autocreate import match_existing_many


class CheckmkError(UiError, RuntimeError):
    pass


def _aad(instance_id: Any) -> bytes:
    return f"checkmk_instance:{instance_id}:api_secret".encode()


def encrypt_api_secret(instance_id: Any, raw: str) -> tuple[bytes, bytes]:
    return encrypt_secret(raw, aad=_aad(instance_id))


def _secret(inst: CheckmkInstance) -> str | None:
    if not inst.api_secret_enc or not inst.api_secret_nonce:
        return None
    return decrypt_secret(inst.api_secret_enc, inst.api_secret_nonce, aad=_aad(inst.id)).decode("utf-8")


def api_base(api_url: str) -> str:
    """網站網址 → API 根路徑。

    接受：`https://主機/網站`、從瀏覽器複製的 `https://主機/網站/check_mk/index.py?...`、
    完整的 `https://主機/網站/check_mk/api/1.0`（或 `api/v1`）。少了網站名稱（只有主機）時講清楚，
    不去猜 —— 一台主機可以有好幾個網站。
    """
    parts = urlsplit(str(api_url).strip())
    segs = [s for s in parts.path.split("/") if s]
    if "check_mk" in segs:
        segs = segs[:segs.index("check_mk")]
    if not segs:
        raise CheckmkError("網址要包含網站名稱，例如 https://checkmk.example.com/mysite",
                           code="cmk_need_site", url=str(api_url))
    path = "/" + "/".join(segs) + "/check_mk/api/1.0"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


async def _get(inst: CheckmkInstance, path: str, *, params: dict[str, Any] | None = None,
               timeout: float = 30.0) -> Any:
    secret = _secret(inst)
    if not secret:
        raise CheckmkError("未設定自動化密碼（automation secret）", code="cmk_no_credentials")
    url = api_base(inst.api_url) + path
    headers = {"Authorization": f"Bearer {inst.api_user} {secret}", "Accept": "application/json"}
    try:
        resp = await safe_request("GET", url, params=params, headers=headers,
                                  timeout=timeout, verify=inst.verify_tls)
    except UnsafeOutboundURL as exc:
        raise CheckmkError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise CheckmkError(f"transport: {transport_detail(exc)}") from exc
    ctype = resp.headers.get("content-type", "")
    if resp.status_code == 401:
        raise CheckmkError("帳號或自動化密碼錯誤", code="cmk_auth", user=inst.api_user)
    if resp.status_code == 404 and "json" not in ctype:
        # 網站名稱錯的時候是 Apache 的 HTML 404，不是 Checkmk 的 JSON
        raise CheckmkError("找不到這個網站：請確認網址裡的網站名稱", code="cmk_no_site", url=api_base(inst.api_url))
    if resp.status_code != 200:
        detail = ""
        try:
            body = resp.json()
            detail = str(body.get("detail") or body.get("title") or "")[:300]
        except ValueError:
            detail = " ".join(resp.text[:200].split())
        raise CheckmkError(f"Checkmk {path}: HTTP {resp.status_code} {detail}".strip(),
                           code="cmk_http", path=path, status=resp.status_code, body=detail)
    try:
        return resp.json()
    except ValueError as exc:
        snippet = " ".join(resp.text[:120].split())
        raise CheckmkError(
            f"Checkmk {path}: 回應不是 JSON（content-type={ctype or '?'} 內容開頭={snippet!r}）",
            code="cmk_not_json", path=path, ctype=ctype or "?", snippet=snippet) from exc


#: livestatus 的主機欄位。`custom_variables` 帶 ADDRESS_4／ADDRESS_6／ADDRESSES_4_n（額外位址）與 FILENAME（資料夾）
HOST_COLUMNS = ["name", "alias", "address", "state", "has_been_checked", "last_check",
                "active_checks_enabled", "scheduled_downtime_depth", "plugin_output",
                "groups", "labels", "custom_variables"]
#: 0 UP、1 DOWN、2 UNREACHABLE；還沒檢查過另外標 pending
STATE_MAP = {0: "up", 1: "down", 2: "unreachable"}
#: 解析不到 DNS 名稱時 Checkmk 填的佔位位址（2.4／2.5 實測），不是真的位址
_PLACEHOLDER = {"0.0.0.0", "::"}  # noqa: S104


async def list_hosts(inst: CheckmkInstance, *, columns: list[str] | None = None) -> list[dict[str, Any]]:
    body = await _get(inst, "/domain-types/host/collections/all",
                      params={"columns": columns or HOST_COLUMNS}, timeout=60.0)
    rows = body.get("value") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise CheckmkError("主機清單的格式非預期", code="cmk_hosts_shape")
    return [r.get("extensions") or {} for r in rows if isinstance(r, dict)]


async def healthcheck(inst: CheckmkInstance) -> dict[str, Any]:
    """測試連線：版本與版本別、網站、看得到幾台主機。

    看得到 0 台要提醒：角色不是 guest（或沒有「看到全部主機」的權限）時，只看得到聯絡群組裡的主機。
    """
    ver = await _get(inst, "/version", timeout=10.0)
    out: dict[str, Any] = {
        "version": (ver.get("versions") or {}).get("checkmk") if isinstance(ver, dict) else None,
        "edition": ver.get("edition") if isinstance(ver, dict) else None,
        "site": ver.get("site") if isinstance(ver, dict) else None,
    }
    try:
        hosts = await list_hosts(inst, columns=["name"])
        out["hosts_readable"] = True
        out["host_count"] = len(hosts)
    except CheckmkError as exc:
        out["hosts_readable"] = False
        out["error"] = str(exc)
    return out


def _valid_ip(raw: Any) -> str | None:
    s = str(raw or "").strip()
    if not s or s in _PLACEHOLDER:
        return None
    try:
        return str(ipaddress.ip_address(s))
    except ValueError:
        return None


def host_addresses(h: dict[str, Any]) -> tuple[str | None, list[str]]:
    """(主要位址, 全部位址)。主要位址取監控核心實際在用的 `address`，解析不到時退回 ADDRESS_4／ADDRESS_6。"""
    cv = h.get("custom_variables") or {}
    primary = _valid_ip(h.get("address")) or _valid_ip(cv.get("ADDRESS_4")) or _valid_ip(cv.get("ADDRESS_6"))
    allips: list[str] = []
    for raw in [primary, cv.get("ADDRESS_4"), cv.get("ADDRESS_6"),
                *(v for k, v in sorted(cv.items()) if k.startswith(("ADDRESSES_4_", "ADDRESSES_6_")))]:
        ip = _valid_ip(raw)
        if ip and ip not in allips:
            allips.append(ip)
    return primary, allips


def _folder(h: dict[str, Any]) -> str | None:
    """`/wato/dc/hosts.mk` → `/dc`。"""
    fn = str((h.get("custom_variables") or {}).get("FILENAME") or "")
    if not fn.startswith("/wato/"):
        return None
    inner = fn[len("/wato"):].rsplit("/", 1)[0]
    return inner or "/"


def _state(h: dict[str, Any]) -> str:
    if not h.get("has_been_checked"):
        return "pending"
    try:
        return STATE_MAP.get(int(h.get("state")), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def _ts(raw: Any) -> datetime | None:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(v, UTC) if v > 0 else None


def _scope_ids(inst: CheckmkInstance) -> set[Any]:
    out: set[Any] = set()
    for s in (inst.scope_subnet_ids or []):
        try:
            out.add(uuid.UUID(str(s)))
        except (ValueError, TypeError):
            pass
    return out


async def sync_instance(session: AsyncSession, inst: CheckmkInstance) -> dict[str, Any]:
    """同步主機清單並對應到 IPAM。**只標既有 IP、不新建**（與其他整合一致，未授權 IP 偵測才有意義）。"""
    hosts = await list_hosts(inst)
    scope = _scope_ids(inst)
    now = datetime.now(UTC)
    seen: set[str] = set()
    linked = 0

    from app.services.hostname_reports import HostnameRun, enabled_peers
    hn_run = HostnameRun(session, source="checkmk", origin=f"checkmk:{inst.id}",
                         peers=await enabled_peers(session, CheckmkInstance))
    existing_by_name = {row.host_name: row for row in (await session.execute(
        select(CheckmkHost).where(CheckmkHost.instance_id == inst.id))).scalars()}
    addrs = {str(h.get("name") or ""): host_addresses(h) for h in hosts}
    matches = await match_existing_many(session, {p for p, _all in addrs.values() if p}, scope)
    for h in hosts:
        name = str(h.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        ip, allips = addrs[name]
        state = _state(h)
        checks = bool(h.get("active_checks_enabled", 1))
        last_check = _ts(h.get("last_check"))
        addr_id = None
        if ip:
            ipa, _amb = matches.get(ip, (None, False))     # 唯一才算（重疊網段又沒設範圍時不猜）
            if ipa is not None:
                addr_id = ipa.id
                linked += 1
                # 上線判定：監控核心自己在檢查，UP 就是那一刻可達。只有 UP 才蓋時間（DOWN 是「不活著」、
                # 還沒檢查過是沒有證據）；主動檢查關掉時狀態停在舊值，也不算。時間用 Checkmk 實際檢查的時間
                if state == "up" and checks and last_check:
                    ipa.last_seen_checkmk = min(last_check, now)
                hn_run.report(ipa, name)
        values = {
            "site": str((h.get("labels") or {}).get("cmk/site") or "")[:64] or None,
            "alias": str(h.get("alias") or "")[:255] or None,
            "state": state,
            "checks_enabled": checks,
            "downtime": int(h.get("scheduled_downtime_depth") or 0) > 0,
            "ip": ip, "addresses": allips or None,
            "folder": _folder(h),
            "groups": [str(g) for g in (h.get("groups") or [])],
            "labels": {k: v for k, v in (h.get("labels") or {}).items() if not str(k).startswith("cmk/")} or None,
            "output": str(h.get("plugin_output") or "")[:500] or None,
            "last_check": last_check,
            "jt_ipam_address_id": addr_id,
            "last_seen_at": now,
            "synced_at": now,
        }
        row = existing_by_name.get(name)
        if row is None:
            session.add(CheckmkHost(instance_id=inst.id, host_name=name[:255], **values))
        else:
            for k, v in values.items():
                setattr(row, k, v)

    # 主機清單是單一呼叫、失敗會往外拋 → 走到這裡就是完整的清單
    await hn_run.finish(complete=True)
    removed = 0
    for name, row in existing_by_name.items():
        if name not in seen:
            await session.delete(row)
            removed += 1
    inst.last_sync_at = now
    inst.last_error = None
    return {"hosts": len(seen), "linked": linked, "removed": removed}


async def coverage_gap(
    session: AsyncSession, *, instance_id: uuid.UUID | None = None,
    subnet_ids: list[uuid.UUID] | None = None,
) -> list[dict[str, Any]]:
    """有主機名稱、卻沒有被 Checkmk 監控的 IP（監控涵蓋缺口，同 Zabbix）。"""
    sub = select(CheckmkHost.jt_ipam_address_id).where(CheckmkHost.jt_ipam_address_id.is_not(None))
    if instance_id is not None:
        sub = sub.where(CheckmkHost.instance_id == instance_id)
    stmt = select(IPAddress.id, IPAddress.ip, IPAddress.hostname).where(
        IPAddress.id.not_in(sub), IPAddress.hostname.is_not(None), IPAddress.hostname != "")
    if subnet_ids is not None:
        if not subnet_ids:
            return []
        stmt = stmt.where(in_values(IPAddress.subnet_id, subnet_ids))
    rows = (await session.execute(stmt)).all()
    return [{"ip_address_id": str(i), "ip": str(ip).split("/", 1)[0] if ip else None,
             "hostname": hn} for i, ip, hn in rows]
