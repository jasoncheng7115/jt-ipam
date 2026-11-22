"""AdGuard Home API client + sync 邏輯。

AdGuard Home v0.107+ REST API：
  https://github.com/AdguardTeam/AdGuardHome/wiki/API
  - GET  /control/status               健康檢查
  - GET  /control/clients              已知 clients（含 IPs / MAC / 名稱）
  - GET  /control/rewrite/list         DNS rewrites（domain → IP/CNAME）
  - GET  /control/dhcp/status          DHCP（若 AdGuard 自己做 DHCP）

OWASP：
- A02：api_password 走 AES-GCM；aad 綁 instance id
- A05：所有對外請求走 safe_request；timeout 必填
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.safe_http import safe_request
from app.core.security import decrypt_secret, encrypt_secret
from app.models.adguard import AdGuardInstance
from app.services.hostname_reports import HostnameRun, enabled_peers
from app.services.ip_autocreate import match_existing_many


class AdGuardError(RuntimeError):
    pass


def _scope_subnet_uuids(inst: AdGuardInstance) -> set[Any]:
    """inst.scope_subnet_ids（JSONB 字串陣列）→ UUID set；空回空 set（不限範圍）。"""
    import uuid as _uuid
    out: set[Any] = set()
    for s in (inst.scope_subnet_ids or []):
        try:
            out.add(_uuid.UUID(str(s)))
        except (ValueError, TypeError):
            pass
    return out


# ─────────────────── 加解密 ───────────────────


def _aad(instance_id) -> bytes:  # type: ignore[no-untyped-def]
    return f"adguard:{instance_id}:api_password".encode()


def encrypt_password(instance_id, password: str) -> dict[str, bytes]:  # type: ignore[no-untyped-def]
    ct, nonce = encrypt_secret(password, aad=_aad(instance_id))
    return {"api_password_enc": ct, "api_password_nonce": nonce}


def _decrypt_password(inst: AdGuardInstance) -> str:
    pt = decrypt_secret(inst.api_password_enc, inst.api_password_nonce, aad=_aad(inst.id))
    return pt.decode("utf-8") if isinstance(pt, bytes) else pt


# ─────────────────── API 呼叫 ───────────────────


def _basic_auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


async def _api_get(inst: AdGuardInstance, path: str, *, timeout: float = 15.0) -> Any:
    url = inst.api_url.rstrip("/") + path
    headers = {
        "Accept": "application/json",
        "Authorization": _basic_auth_header(inst.api_user, _decrypt_password(inst)),
    }
    try:
        resp = await safe_request("GET", url, headers=headers, timeout=timeout,
                                   verify=inst.verify_tls)
    except httpx.HTTPError as exc:
        raise AdGuardError(f"AdGuard request failed: {exc}") from exc
    if resp.status_code == 401:
        raise AdGuardError("AdGuard auth failed (401) — check username / password")
    if resp.status_code >= 400:
        raise AdGuardError(f"AdGuard HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise AdGuardError(f"AdGuard response not JSON: {resp.text[:200]}") from exc


# ─────────────────── 健康檢查 ───────────────────


async def healthcheck(inst: AdGuardInstance) -> dict[str, Any]:
    data = await _api_get(inst, "/control/status", timeout=8.0)
    if not isinstance(data, dict):
        raise AdGuardError("/control/status didn't return dict")
    return {
        "version": data.get("version"),
        "dns_addresses": data.get("dns_addresses"),
        "running": data.get("running"),
    }


# ─────────────────── 同步：clients ───────────────────


async def sync_clients(session: AsyncSession, inst: AdGuardInstance) -> dict[str, int]:
    """從 AdGuard 拉已知 clients；對到 jt-ipam IPAddress 就 stamp last_seen_dns + 補 MAC / hostname。

    AdGuard `clients` 回應結構：
        { "clients": [
            { "name": "...", "ids": ["192.168.123.5", "aa:bb:cc:dd:ee:ff", "host.lan"], ... },
            ...
        ] }
    `ids` 是 list，可能混 IP / MAC / hostname。
    """
    data = await _api_get(inst, "/control/clients")
    clients = (data or {}).get("clients") or []
    seen = matched = 0
    # 重疊網段：若 instance 設了 scope_subnet_ids，IP→IPAddress 比對限定在這些子網路內
    scope_ids = _scope_subnet_uuids(inst)
    hn_run = HostnameRun(session, source="adguard", origin=f"adguard:{inst.id}:clients",
                         peers=await enabled_peers(session, AdGuardInstance))
    parsed: list[tuple[str | None, list[str], str | None]] = []
    for c in clients:
        name = (c.get("name") or "").strip() or None
        ids = c.get("ids") or []
        # 分類：IP vs MAC vs hostname
        ips: list[str] = []
        macs: list[str] = []
        for item in ids:
            s = str(item).strip()
            if not s:
                continue
            if ":" in s and len(s.split(":")) == 6:
                macs.append(s)
            elif "." in s and any(ch.isdigit() for ch in s.split(".")[0]):
                ips.append(s)
            # 其它（hostname）忽略
        parsed.append((name, ips, macs[0] if macs else None))
    # 一次比對（以前每個位址各查一次）；重疊網段：同一 IP 字串可能對到多筆 → 唯一才算，多筆不猜
    matches = await match_existing_many(session, {ip for _n, ips, _m in parsed for ip in ips}, scope_ids)
    for name, ips, primary_mac in parsed:
        for ip in ips:
            seen += 1
            ipa, _amb = matches.get(ip, (None, False))   # 唯一才算
            if ipa is None:
                continue
            ipa.last_seen_dns = datetime.now(UTC)
            hn_run.report(ipa, name)
            if primary_mac:
                from app.services.arp_precedence import consider_mac
                await consider_mac(session, ip=ipa, mac=primary_mac, source="adguard")
            matched += 1
    # 讀取失敗會往外拋；走到這裡就是完整的用戶端清單
    await hn_run.finish(complete=True)
    return {"clients": len(clients), "ips_seen": seen, "ips_matched": matched}


# ─────────────────── 同步：DNS rewrites ───────────────────


async def sync_rewrites(session: AsyncSession, inst: AdGuardInstance) -> dict[str, int]:
    """從 AdGuard 拉 DNS rewrites。

    `rewrite/list` 回應：[{ "domain": "...", "answer": "..." }, ...]
    answer 可能是 IP（直接對映）或 CNAME（hostname）。IP 才有意義填回 IPAddress。
    """
    data = await _api_get(inst, "/control/rewrite/list")
    rewrites = data or []
    seen = matched = 0
    # 重疊網段：若 instance 設了 scope_subnet_ids，IP→IPAddress 比對限定在這些子網路內
    scope_ids = _scope_subnet_uuids(inst)
    # 改寫與用戶端清單是兩個子來源：各記各的，名字不同時才不會每輪互相覆蓋
    hn_run = HostnameRun(session, source="adguard", origin=f"adguard:{inst.id}:rewrites",
                         peers=await enabled_peers(session, AdGuardInstance))
    pairs: list[tuple[str, str]] = []
    for r in rewrites:
        domain = (r.get("domain") or "").strip()
        answer = (r.get("answer") or "").strip()
        if not domain or not answer:
            continue
        # 新版 AdGuard Home 的 rewrite 可以停用（enabled: false）：停用的不拿來命名 IP
        # （使用者 2026-10-13：「停用的不要抓」）。舊版沒有這個欄位 → 照舊
        if r.get("enabled") is False:
            continue
        seen += 1
        # 只處理 answer 是 IP（含 . 跟全數字段）
        parts = answer.split(".")
        if not (len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)):
            continue
        pairs.append((domain, answer))
    # 一次比對；重疊網段：同 IP 多筆 → 唯一才算，多筆不猜（以前任意取一筆）
    matches = await match_existing_many(session, {a for _d, a in pairs}, scope_ids)
    for domain, answer in pairs:
        ipa, _amb = matches.get(answer, (None, False))   # 唯一才算
        if ipa is None:
            continue
        ipa.last_seen_dns = datetime.now(UTC)
        hn_run.report(ipa, domain)
        matched += 1
    await hn_run.finish(complete=True)
    return {"rewrites": len(rewrites), "rewrites_seen": seen, "rewrites_matched": matched}


# ─────────────────── 主入口 ───────────────────


async def sync_instance(session: AsyncSession, inst: AdGuardInstance) -> dict[str, Any]:
    summary: dict[str, Any] = {"instance": inst.name}
    try:
        if inst.sync_clients:
            summary["clients_result"] = await sync_clients(session, inst)
            await session.commit()
        if inst.sync_rewrites:
            summary["rewrites_result"] = await sync_rewrites(session, inst)
            await session.commit()
        inst.last_sync_at = datetime.now(UTC)
        inst.last_error = None
    except AdGuardError as exc:
        # 連不上／認證失敗是硬失敗：寫 last_error 後往上拋，作業才會是「失敗」而不是「成功、0 筆」
        # （issue #44 在 Proxmox 回報的同一個寫法）
        inst.last_error = str(exc)
        await session.commit()
        raise
    await session.commit()
    return summary
