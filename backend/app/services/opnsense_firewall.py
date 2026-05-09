"""OPNsense Firewall API client + alias 同步邏輯。

OPNsense API 文件：https://docs.opnsense.org/development/api.html
Firewall alias：https://docs.opnsense.org/development/api/core/firewall.html#aliases

主要 endpoints：
  GET  /api/firewall/alias/get                  讀全部 alias / 設定
  GET  /api/firewall/alias/getItem/{uuid}       讀單筆
  POST /api/firewall/alias/addItem              新增
  POST /api/firewall/alias/setItem/{uuid}       修改
  POST /api/firewall/alias/delItem/{uuid}       刪除
  POST /api/firewall/alias/reconfigure          套用變更
  POST /api/firewall/alias_util/list/{name}     看 alias 解析後的成員（runtime view）

OWASP：
- A02：API key/secret 雙欄 AES-GCM，aad 綁 instance id
- A05：所有對外請求走 safe_http；timeout 必填
- A09：每次同步寫 audit
"""

from __future__ import annotations

import base64
import ipaddress
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.core.security import decrypt_secret, encrypt_secret
from app.models.address import IPAddress
from app.models.firewall import OPNsenseAliasMapping, OPNsenseFirewall
from app.models.section import Section
from app.models.subnet import Subnet


class OPNsenseError(RuntimeError):
    pass


# ─────────────────── 加解密 ───────────────────


def _aad_key(instance_id) -> bytes:  # type: ignore[no-untyped-def]
    return f"opnsense_firewall:{instance_id}:api_key".encode("utf-8")


def _aad_secret(instance_id) -> bytes:  # type: ignore[no-untyped-def]
    return f"opnsense_firewall:{instance_id}:api_secret".encode("utf-8")


def encrypt_credentials(
    instance_id, api_key: str, api_secret: str,  # type: ignore[no-untyped-def]
) -> dict[str, bytes]:
    k_ct, k_nc = encrypt_secret(api_key, aad=_aad_key(instance_id))
    s_ct, s_nc = encrypt_secret(api_secret, aad=_aad_secret(instance_id))
    return {
        "api_key_enc": k_ct, "api_key_nonce": k_nc,
        "api_secret_enc": s_ct, "api_secret_nonce": s_nc,
    }


def _decrypt_creds(fw: OPNsenseFirewall) -> tuple[str, str]:
    key = decrypt_secret(fw.api_key_enc, fw.api_key_nonce, aad=_aad_key(fw.id)).decode("utf-8")
    secret = decrypt_secret(
        fw.api_secret_enc, fw.api_secret_nonce, aad=_aad_secret(fw.id)
    ).decode("utf-8")
    return key, secret


# ─────────────────── 低階 HTTP ───────────────────


def _basic_auth_header(api_key: str, api_secret: str) -> dict[str, str]:
    token = base64.b64encode(f"{api_key}:{api_secret}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}", "Accept": "application/json"}


async def _api_get(fw: OPNsenseFirewall, path: str, *, timeout: float = 15.0) -> dict[str, Any]:
    url = f"{fw.api_url.rstrip('/')}{path}"
    key, secret = _decrypt_creds(fw)
    try:
        resp = await safe_request(
            "GET", url, headers=_basic_auth_header(key, secret),
            timeout=timeout, verify=fw.verify_tls,
        )
    except UnsafeOutboundURL as exc:
        raise OPNsenseError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise OPNsenseError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise OPNsenseError(f"OPNsense GET {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json()


async def _api_post(
    fw: OPNsenseFirewall, path: str, body: dict[str, Any] | None = None, *, timeout: float = 15.0,
) -> dict[str, Any]:
    url = f"{fw.api_url.rstrip('/')}{path}"
    key, secret = _decrypt_creds(fw)
    try:
        resp = await safe_request(
            "POST", url,
            headers={**_basic_auth_header(key, secret), "Content-Type": "application/json"},
            json=body or {}, timeout=timeout, verify=fw.verify_tls,
        )
    except UnsafeOutboundURL as exc:
        raise OPNsenseError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise OPNsenseError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code not in (200, 201):
        raise OPNsenseError(f"OPNsense POST {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json()


# ─────────────────── 高階 API ───────────────────


async def healthcheck(fw: OPNsenseFirewall) -> dict[str, Any]:
    return await _api_get(fw, "/api/firewall/alias/get", timeout=8.0)


async def list_aliases(fw: OPNsenseFirewall) -> list[dict[str, Any]]:
    """讀 OPNsense 上所有 alias（attribute view）。"""
    data = await _api_get(fw, "/api/firewall/alias/get")
    aliases_obj = data.get("alias", {}).get("aliases", {}).get("alias", {})
    out: list[dict[str, Any]] = []
    if isinstance(aliases_obj, dict):
        for uuid_, info in aliases_obj.items():
            row = {"uuid": uuid_}
            row.update(info if isinstance(info, dict) else {})
            out.append(row)
    return out


async def find_alias_uuid(fw: OPNsenseFirewall, name: str) -> str | None:
    for a in await list_aliases(fw):
        if a.get("name") == name:
            return str(a["uuid"])
    return None


async def list_alias_members(fw: OPNsenseFirewall, alias_name: str) -> list[str]:
    """OPNsense alias 解析後的成員列表（runtime）。"""
    data = await _api_post(fw, f"/api/firewall/alias_util/list/{alias_name}")
    rows = data.get("rows") or []
    return [str(r.get("ip") or r.get("entry") or "") for r in rows if r]


async def upsert_alias(
    fw: OPNsenseFirewall, *, name: str, alias_type: str, content: list[str],
    description: str | None = None,
) -> str:
    """
    新增或更新 alias，回傳 uuid。OPNsense alias.content 是 \\n 分隔字串。
    """
    body = {
        "alias": {
            "name": name,
            "type": alias_type,
            "description": description or f"managed by jt-ipam",
            "content": "\n".join(content),
            "enabled": "1",
        }
    }
    existing_uuid = await find_alias_uuid(fw, name)
    if existing_uuid:
        await _api_post(fw, f"/api/firewall/alias/setItem/{existing_uuid}", body)
        await _api_post(fw, "/api/firewall/alias/reconfigure")
        return existing_uuid

    resp = await _api_post(fw, "/api/firewall/alias/addItem", body)
    new_uuid = str(resp.get("uuid") or "")
    if not new_uuid:
        raise OPNsenseError(f"addItem returned no uuid: {resp}")
    await _api_post(fw, "/api/firewall/alias/reconfigure")
    return new_uuid


async def delete_alias(fw: OPNsenseFirewall, name: str) -> bool:
    uuid_ = await find_alias_uuid(fw, name)
    if not uuid_:
        return False
    await _api_post(fw, f"/api/firewall/alias/delItem/{uuid_}")
    await _api_post(fw, "/api/firewall/alias/reconfigure")
    return True


# ─────────────────── 同步邏輯 ───────────────────


async def _resolve_selector_ips(
    session: AsyncSession, selector: dict[str, Any],
) -> list[str]:
    """根據 selector 抓出要送進 alias 的 IP 列表。"""
    sel_type = selector.get("type")
    stmt = None
    if sel_type == "subnet":
        sub_id = selector.get("subnet_id")
        if not sub_id:
            return []
        stmt = select(IPAddress.ip).where(IPAddress.subnet_id == sub_id)
    elif sel_type == "section":
        sec_id = selector.get("section_id")
        if not sec_id:
            return []
        stmt = (
            select(IPAddress.ip)
            .join(Subnet, Subnet.id == IPAddress.subnet_id)
            .where(Subnet.section_id == sec_id)
        )
    elif sel_type == "tag":
        # IPAddress.tags 是 ARRAY(String)（可能無；schema 沒有就忽略）
        # 退而求其次：用 custom_fields["tag"] 比對
        stmt = select(IPAddress.ip).where(
            IPAddress.custom_fields["tag"].astext == selector.get("tag")
        )
    elif sel_type == "custom_field":
        field = selector.get("field")
        value = selector.get("value")
        if not field or value is None:
            return []
        stmt = select(IPAddress.ip).where(
            IPAddress.custom_fields[field].astext == str(value)
        )
    else:
        raise OPNsenseError(f"unsupported selector type: {sel_type}")

    rows = (await session.execute(stmt)).scalars().all()
    out: list[str] = []
    for r in rows:
        # asyncpg 給 inet 物件，str() 會帶 prefix；alias 要單一 IP
        s = str(r).split("/", 1)[0]
        try:
            ipaddress.ip_address(s)
        except ValueError:
            continue
        out.append(s)
    # 去重 + 排序穩定 diff
    return sorted(set(out))


async def sync_mapping(
    session: AsyncSession, mapping: OPNsenseAliasMapping,
) -> dict[str, Any]:
    """執行單一 mapping 的同步；回傳 summary。"""
    fw = (
        await session.execute(
            select(OPNsenseFirewall).where(OPNsenseFirewall.id == mapping.firewall_id)
        )
    ).scalar_one()
    if not fw.enabled:
        raise OPNsenseError(f"firewall {fw.name!r} disabled")

    summary: dict[str, Any] = {"alias": mapping.alias_name, "direction": mapping.direction}

    try:
        if mapping.direction in ("push", "both"):
            ips = await _resolve_selector_ips(session, mapping.selector)
            uuid_ = await upsert_alias(
                fw, name=mapping.alias_name, alias_type=mapping.alias_type,
                content=ips, description=f"jt-ipam → {mapping.alias_name}",
            )
            mapping.last_alias_uuid = uuid_
            mapping.last_synced_count = len(ips)
            summary["pushed"] = len(ips)
        if mapping.direction in ("pull", "both"):
            members = await list_alias_members(fw, mapping.alias_name)
            summary["pulled"] = len(members)
            summary["pull_sample"] = members[:5]
        mapping.last_sync_at = datetime.now(UTC)
        mapping.last_error = None
    except OPNsenseError as exc:
        mapping.last_error = str(exc)
        raise

    return summary


async def sync_all_for_firewall(
    session: AsyncSession, fw: OPNsenseFirewall,
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(OPNsenseAliasMapping).where(OPNsenseAliasMapping.firewall_id == fw.id)
        )
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for m in rows:
        try:
            out.append(await sync_mapping(session, m))
        except OPNsenseError as exc:
            out.append({
                "alias": m.alias_name, "error": str(exc), "direction": m.direction,
            })
    fw.last_sync_at = datetime.now(UTC)
    fw.last_error = None
    return out
