"""System settings endpoints — admin only。

目前只有 LLM 設定；之後其他 system-level setting 也丟這。
"""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.models.ip_hostname import HOSTNAME_SOURCES
from app.schemas.base import StrictModel
from app.services import os_precedence
from app.services.hostname import (
    get_disabled,
    get_precedence,
    set_precedence,
)
from app.services.system_config import get_llm_config, set_llm_config

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_admin)])

# 不需 admin 的系統讀取路由（例如 Locations 地圖預覽要讀全域 map_provider）。
# 寫入（PUT）仍掛在上面的 admin 路由，只有 admin 能改。
public_router = APIRouter(prefix="/system", tags=["system"])


class HostnamePrecedenceOut(StrictModel):
    order: list[str]
    disabled: list[str] = []  # 停用（不參與名稱比對）的來源
    sources: list[str]  # 所有合法來源（給前端顯示用）


class HostnamePrecedencePatch(StrictModel):
    order: list[str]
    disabled: list[str] = []


@router.get("/hostname-precedence", response_model=HostnamePrecedenceOut)
async def get_hostname_precedence(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HostnamePrecedenceOut:
    """全域 hostname 來源優先序（feature A）。"""
    return HostnamePrecedenceOut(
        order=await get_precedence(session),
        disabled=await get_disabled(session),
        sources=list(HOSTNAME_SOURCES),
    )


@router.put("/hostname-precedence", response_model=HostnamePrecedenceOut)
async def put_hostname_precedence(
    payload: HostnamePrecedencePatch,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HostnamePrecedenceOut:
    order, disabled = await set_precedence(
        session, order=payload.order, disabled=payload.disabled, updated_by_user_id=user.id,
    )
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "hostname_precedence", "order": order, "disabled": disabled},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return HostnamePrecedenceOut(order=order, disabled=disabled, sources=list(HOSTNAME_SOURCES))


class OsPrecedenceOut(StrictModel):
    order: list[str]
    sources: list[str]


class OsPrecedencePatch(StrictModel):
    order: list[str]


@router.get("/os-precedence", response_model=OsPrecedenceOut)
async def get_os_precedence(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OsPrecedenceOut:
    """全域 OS 來源優先序（scanner / librenms / wazuh）。"""
    return OsPrecedenceOut(
        order=await os_precedence.get_order(session),
        sources=list(os_precedence.OS_SOURCES),
    )


@router.put("/os-precedence", response_model=OsPrecedenceOut)
async def put_os_precedence(
    payload: OsPrecedencePatch,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OsPrecedenceOut:
    order = await os_precedence.set_order(session, order=payload.order, updated_by_user_id=user.id)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "os_precedence", "order": order},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return OsPrecedenceOut(order=order, sources=list(os_precedence.OS_SOURCES))


class ArpPrecedenceOut(StrictModel):
    order: list[str]
    disabled: list[str] = []
    sources: list[str]


class ArpPrecedencePatch(StrictModel):
    order: list[str]
    disabled: list[str] = []


@router.get("/device-name-precedence", response_model=HostnamePrecedenceOut)
async def get_devname_precedence_ep(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HostnamePrecedenceOut:
    """裝置名稱來源順序：多來源（LibreNMS/DNS/Proxmox VM…）提供同一台 device 名稱時誰優先。"""
    from app.services.device_name_precedence import (
        DEVNAME_SOURCES,
        get_devname_disabled,
        get_devname_precedence,
    )
    return HostnamePrecedenceOut(
        order=await get_devname_precedence(session),
        disabled=await get_devname_disabled(session),
        sources=list(DEVNAME_SOURCES),
    )


@router.put("/device-name-precedence", response_model=HostnamePrecedenceOut)
async def put_devname_precedence_ep(
    payload: HostnamePrecedencePatch,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HostnamePrecedenceOut:
    from app.services.device_name_precedence import DEVNAME_SOURCES, set_devname_precedence
    order, disabled = await set_devname_precedence(
        session, order=payload.order, disabled=payload.disabled, updated_by_user_id=user.id,
    )
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "device_name_precedence", "order": order, "disabled": disabled},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return HostnamePrecedenceOut(order=order, disabled=disabled, sources=list(DEVNAME_SOURCES))


@router.get("/device-model-precedence", response_model=HostnamePrecedenceOut)
async def get_model_precedence_ep(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HostnamePrecedenceOut:
    """裝置型號來源順序：多來源（LibreNMS hardware/Proxmox/OPNsense…）提供型號時誰優先。"""
    from app.services.model_precedence import (
        MODEL_SOURCES,
        get_model_disabled,
        get_model_precedence,
    )
    return HostnamePrecedenceOut(
        order=await get_model_precedence(session),
        disabled=await get_model_disabled(session),
        sources=list(MODEL_SOURCES),
    )


@router.put("/device-model-precedence", response_model=HostnamePrecedenceOut)
async def put_model_precedence_ep(
    payload: HostnamePrecedencePatch,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HostnamePrecedenceOut:
    from app.services.model_precedence import MODEL_SOURCES, set_model_precedence
    order, disabled = await set_model_precedence(
        session, order=payload.order, disabled=payload.disabled, updated_by_user_id=user.id,
    )
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "device_model_precedence", "order": order, "disabled": disabled},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return HostnamePrecedenceOut(order=order, disabled=disabled, sources=list(MODEL_SOURCES))


@router.get("/arp-precedence", response_model=ArpPrecedenceOut)
async def get_arp_precedence_ep(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ArpPrecedenceOut:
    """ARP / MAC 來源順序：多來源回報同一 IP 的 MAC 時誰可覆寫誰；停用的來源不參與。"""
    from app.services.arp_precedence import ARP_SOURCES, get_arp_disabled, get_arp_precedence
    return ArpPrecedenceOut(
        order=await get_arp_precedence(session),
        disabled=await get_arp_disabled(session),
        sources=list(ARP_SOURCES),
    )


@router.put("/arp-precedence", response_model=ArpPrecedenceOut)
async def put_arp_precedence_ep(
    payload: ArpPrecedencePatch,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ArpPrecedenceOut:
    from app.services.arp_precedence import ARP_SOURCES, set_arp_precedence
    order, disabled = await set_arp_precedence(
        session, order=payload.order, disabled=payload.disabled, updated_by_user_id=user.id,
    )
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "arp_precedence", "order": order, "disabled": disabled},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return ArpPrecedenceOut(order=order, disabled=disabled, sources=list(ARP_SOURCES))


class MapProviderOut(StrictModel):
    provider: str   # "osm" | "google"


@public_router.get("/map-provider", response_model=MapProviderOut)
async def get_map_provider(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MapProviderOut:
    from app.models.system_setting import SystemSetting
    row = await session.get(SystemSetting, "map_provider")
    prov = (row.value.get("provider") if row and isinstance(row.value, dict) else None) or "osm"
    return MapProviderOut(provider=prov if prov in ("osm", "google") else "osm")


@router.put("/map-provider", response_model=MapProviderOut)
async def put_map_provider(
    payload: MapProviderOut,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MapProviderOut:
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.system_setting import SystemSetting
    prov = payload.provider if payload.provider in ("osm", "google") else "osm"
    row = await session.get(SystemSetting, "map_provider")
    if row is None:
        row = SystemSetting(key="map_provider", value={}, updated_by=user.id)
        session.add(row)
    row.value = {"provider": prov}
    row.updated_by = user.id
    flag_modified(row, "value")
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "map_provider", "provider": prov},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return MapProviderOut(provider=prov)


# ─────────────────── 機櫃示意圖：裝置名稱對齊（全域）───────────────────
class RackNameAlignOut(StrictModel):
    align: str   # "left" | "center" | "right"


@public_router.get("/rack-name-align", response_model=RackNameAlignOut)
async def get_rack_name_align(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RackNameAlignOut:
    from app.models.system_setting import SystemSetting
    row = await session.get(SystemSetting, "rack_name_align")
    align = (row.value.get("align") if row and isinstance(row.value, dict) else None) or "left"
    return RackNameAlignOut(align=align if align in ("left", "center", "right") else "left")


@router.put("/rack-name-align", response_model=RackNameAlignOut)
async def put_rack_name_align(
    payload: RackNameAlignOut,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RackNameAlignOut:
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.system_setting import SystemSetting
    align = payload.align if payload.align in ("left", "center", "right") else "left"
    row = await session.get(SystemSetting, "rack_name_align")
    if row is None:
        row = SystemSetting(key="rack_name_align", value={}, updated_by=user.id)
        session.add(row)
    row.value = {"align": align}
    row.updated_by = user.id
    flag_modified(row, "value")
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "rack_name_align", "align": align},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return RackNameAlignOut(align=align)


# ─────────────────── 上線判定閾值（全域，管理員設）───────────────────
class OnlineGraceOut(StrictModel):
    minutes: Annotated[int, Field(ge=1, le=43200)]


@public_router.get("/online-grace", response_model=OnlineGraceOut)
async def get_online_grace(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OnlineGraceOut:
    from app.models.system_setting import SystemSetting
    row = await session.get(SystemSetting, "online_grace_minutes")
    m = (row.value.get("minutes") if row and isinstance(row.value, dict) else None) or 30
    try:
        m = int(m)
    except (TypeError, ValueError):
        m = 30
    return OnlineGraceOut(minutes=min(43200, max(1, m)))


@router.put("/online-grace", response_model=OnlineGraceOut)
async def put_online_grace(
    payload: OnlineGraceOut,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OnlineGraceOut:
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.system_setting import SystemSetting
    m = min(43200, max(1, int(payload.minutes)))
    row = await session.get(SystemSetting, "online_grace_minutes")
    if row is None:
        row = SystemSetting(key="online_grace_minutes", value={}, updated_by=user.id)
        session.add(row)
    row.value = {"minutes": m}
    row.updated_by = user.id
    flag_modified(row, "value")
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "online_grace_minutes", "minutes": m},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return OnlineGraceOut(minutes=m)


# ─────────────────── GeoIP（MaxMind 本地 mmdb + 排程更新）───────────────────
class GeoIPConfigIn(StrictModel):
    account_id: Annotated[str | None, Field(max_length=64)] = None
    license_key: Annotated[str | None, Field(max_length=128)] = None   # 留空＝保留原本
    editions: list[Annotated[str, Field(max_length=32)]] | None = None
    auto_update: bool | None = None
    frequency: Annotated[str | None, Field(max_length=16)] = None


@router.get("/geoip")
async def get_geoip(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    from app.services.geoip import (
        ALL_EDITIONS,
        FREQUENCIES,
        get_geoip_config,
    )
    cfg = await get_geoip_config(session)
    cfg["all_editions"] = ALL_EDITIONS
    cfg["frequencies"] = list(FREQUENCIES.keys())
    return cfg


@router.put("/geoip")
async def put_geoip(
    payload: GeoIPConfigIn,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    from app.services.geoip import get_geoip_config, set_geoip_config
    await set_geoip_config(
        session, account_id=payload.account_id, license_key=payload.license_key,
        editions=payload.editions, auto_update=payload.auto_update,
        frequency=payload.frequency, updated_by=user.id,
    )
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "geoip", "account_id": payload.account_id,
              "key_changed": bool(payload.license_key),
              "editions": payload.editions, "auto_update": payload.auto_update,
              "frequency": payload.frequency},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return await get_geoip_config(session)


@router.post("/geoip/update")
async def update_geoip_now(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """立即下載/更新本地 mmdb（手動觸發；排程由 systemd timer 跑）。"""
    from app.services.geoip import get_geoip_config, update_databases
    result = await update_databases(session)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "geoip_db_update", "result": result},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    cfg = await get_geoip_config(session)
    return {"result": result, "config": cfg}


class LLMConfigOut(StrictModel):
    enabled: bool
    url: str
    embedding_model: str
    chat_model: str
    timeout: float


class LLMConfigPatch(StrictModel):
    enabled: bool | None = None
    url: Annotated[str | None, Field(min_length=4, max_length=512)] = None
    embedding_model: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    chat_model: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    timeout: Annotated[float | None, Field(ge=1.0, le=600.0)] = None


@router.get("/llm", response_model=LLMConfigOut)
async def get_llm(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LLMConfigOut:
    cfg = await get_llm_config(session)
    return LLMConfigOut(
        enabled=cfg.enabled, url=cfg.url,
        embedding_model=cfg.embedding_model,
        chat_model=cfg.chat_model, timeout=cfg.timeout,
    )


@router.patch("/llm", response_model=LLMConfigOut)
async def patch_llm(
    payload: LLMConfigPatch,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LLMConfigOut:
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    await set_llm_config(
        session,
        enabled=changes.get("enabled"),
        url=changes.get("url"),
        embedding_model=changes.get("embedding_model"),
        chat_model=changes.get("chat_model"),
        timeout=changes.get("timeout"),
        updated_by_user_id=user.id,
    )
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        # system_setting 是 singleton；audit.object_id 是 UUID 型別 → 傳 None，
        # 用 object_type 區分（"system_setting" + diff 已足夠 trace）
        object_type="system_setting", object_id=None,
        action="update", diff={"changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    cfg = await get_llm_config(session)
    return LLMConfigOut(
        enabled=cfg.enabled, url=cfg.url,
        embedding_model=cfg.embedding_model,
        chat_model=cfg.chat_model, timeout=cfg.timeout,
    )


@router.get("/llm/models")
async def list_ollama_models(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """列出 Ollama 上目前已 pull 的模型清單（給設定頁的下拉選用）。"""
    cfg = await get_llm_config(session)
    url = f"{cfg.url.rstrip('/')}/api/tags"
    try:
        resp = await safe_request("GET", url, timeout=10.0)
    except httpx.HTTPError as exc:
        return {"models": [], "error": f"{type(exc).__name__}: {exc}"}
    if resp.status_code != 200:
        return {"models": [], "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    data = resp.json() or {}
    out = []
    for m in (data.get("models") or []):
        out.append({
            "name": m.get("name"),
            "size": m.get("size"),
            "modified_at": m.get("modified_at"),
            "family": (m.get("details") or {}).get("family"),
            "parameter_size": (m.get("details") or {}).get("parameter_size"),
        })
    return {"models": out}


# ─────────────────── RBAC：權限指派 ───────────────────
import uuid as _uuid
from typing import Literal as _Literal

from sqlalchemy import select as _select

from app.models.permission import Permission as _Permission
from app.models.user import Group as _Group
from app.models.user import User as _User

_OBJ_TYPES = ("customer", "section", "subnet", "ip", "device", "rack", "location")


class PermissionGrantOut(StrictModel):
    id: _uuid.UUID
    object_type: str
    object_id: _uuid.UUID | None
    principal_type: str
    principal_id: _uuid.UUID
    level: str


class PermissionGrantCreate(StrictModel):
    object_type: _Literal["customer", "section", "subnet", "ip", "device", "rack", "location"]
    object_id: _uuid.UUID | None = None   # None = 全部（wildcard）
    principal_type: _Literal["user", "group"]
    principal_id: _uuid.UUID
    level: _Literal["read", "write", "admin"]


@router.get("/permissions", response_model=list[PermissionGrantOut])
async def list_permissions(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    principal_type: str | None = None,
    principal_id: _uuid.UUID | None = None,
) -> list[PermissionGrantOut]:
    stmt = _select(_Permission)
    if principal_type:
        stmt = stmt.where(_Permission.principal_type == principal_type)
    if principal_id:
        stmt = stmt.where(_Permission.principal_id == principal_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [PermissionGrantOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/permissions", response_model=PermissionGrantOut)
async def upsert_permission(
    payload: PermissionGrantCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PermissionGrantOut:
    from fastapi import HTTPException
    # principal 必須存在
    if payload.principal_type == "group":
        if await session.get(_Group, payload.principal_id) is None:
            raise HTTPException(404, detail="group not found")
    else:
        if await session.get(_User, payload.principal_id) is None:
            raise HTTPException(404, detail="user not found")
    # upsert：同 (type, object_id, principal) 存在就更新 level
    oid_cond = (_Permission.object_id.is_(None) if payload.object_id is None
                else _Permission.object_id == payload.object_id)
    existing = (await session.execute(_select(_Permission).where(
        _Permission.object_type == payload.object_type,
        oid_cond,
        _Permission.principal_type == payload.principal_type,
        _Permission.principal_id == payload.principal_id,
    ))).scalar_one_or_none()
    if existing is not None:
        existing.level = payload.level
        obj = existing
    else:
        obj = _Permission(
            object_type=payload.object_type, object_id=payload.object_id,
            principal_type=payload.principal_type, principal_id=payload.principal_id,
            level=payload.level,
        )
        session.add(obj)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="permission", object_id=None, action="grant",
        diff={"object_type": payload.object_type,
              "object_id": str(payload.object_id) if payload.object_id else "ALL",
              "principal": f"{payload.principal_type}:{payload.principal_id}", "level": payload.level},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return PermissionGrantOut.model_validate(obj, from_attributes=True)


@router.delete("/permissions/{grant_id}", status_code=204)
async def delete_permission(
    grant_id: _uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(_Permission, grant_id)
    if obj is not None:
        await append_audit(
            session, actor_user_id=str(user.id),
            actor_ip=request.client.host if request.client else None,
            actor_user_agent=request.headers.get("user-agent"),
            object_type="permission", object_id=None, action="revoke",
            diff={"object_type": obj.object_type, "level": obj.level,
                  "principal": f"{obj.principal_type}:{obj.principal_id}"},
            request_id=getattr(request.state, "request_id", None),
        )
        await session.delete(obj)
        await session.commit()


@router.get("/roles")
async def list_roles(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """群組／角色清單（is_builtin=true 即內建角色）。"""
    from sqlalchemy import func as _func

    from app.models.user import UserGroupMember as _UGM
    rows = (await session.execute(_select(_Group).order_by(_Group.name))).scalars().all()
    counts = {gid: n for gid, n in (await session.execute(
        _select(_UGM.group_id, _func.count()).group_by(_UGM.group_id)
    )).all()}
    return {"roles": [{
        "id": str(g.id), "name": g.name, "is_builtin": g.is_builtin,
        "member_count": int(counts.get(g.id, 0)),
    } for g in rows], "object_types": list(_OBJ_TYPES), "levels": ["read", "write", "admin"]}


# ─────────────────── 版本資訊 ───────────────────

_GITHUB_REPO = "jasoncheng7115/jt-ipam"


@router.get("/version")
async def get_version_info() -> dict[str, Any]:
    """現行版本 + Python 與主要套件版本（管理頁顯示用）。"""
    import sys
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkgver

    from app.version import __version__

    pkgs = [
        "fastapi", "starlette", "sqlalchemy", "pydantic", "asyncpg", "alembic",
        "uvicorn", "httpx", "redis", "argon2-cffi", "cryptography", "defusedxml",
        "authlib", "mcp",
    ]
    versions: dict[str, str | None] = {}
    for p in pkgs:
        try:
            versions[p] = _pkgver(p)
        except PackageNotFoundError:
            versions[p] = None
    return {
        "current": __version__,
        "python": sys.version.split()[0],
        "packages": versions,
    }


@router.get("/version/check-latest")
async def check_latest_version() -> dict[str, Any]:
    """查 GitHub 最新 release（無 release 則退回 tags），與現行版本比較。"""
    from app.version import __version__

    headers = {"Accept": "application/vnd.github+json"}
    latest: str | None = None
    error: str | None = None
    try:
        resp = await safe_request(
            "GET", f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest",
            timeout=10.0, headers=headers,
        )
        if resp.status_code == 200:
            latest = (resp.json().get("tag_name") or "").lstrip("v") or None
        elif resp.status_code == 404:
            # 尚無 release → 退回 tags
            tags_resp = await safe_request(
                "GET", f"https://api.github.com/repos/{_GITHUB_REPO}/tags",
                timeout=10.0, headers=headers,
            )
            if tags_resp.status_code == 200:
                tags = tags_resp.json()
                if isinstance(tags, list) and tags:
                    latest = (tags[0].get("name") or "").lstrip("v") or None
        else:
            error = f"github http {resp.status_code}"
    except (UnsafeOutboundURL, httpx.HTTPError) as exc:
        error = f"transport: {exc.__class__.__name__}"

    return {
        "current": __version__,
        "latest": latest,
        "update_available": bool(latest and latest != __version__),
        "release_url": f"https://github.com/{_GITHUB_REPO}/releases",
        "error": error,
    }


# ─────────────────── 通知發送設定（Email 已實作；其餘開發中）───────────────────
class NotificationChannelsOut(StrictModel):
    email_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_tls: str = "starttls"           # none / starttls / tls
    smtp_username: str | None = None
    smtp_from: str | None = None
    smtp_password_set: bool = False      # 是否已存密碼（不回傳明文）
    # 規劃中的管道：available=False 前端反灰顯示「開發中」
    channels: list[dict[str, Any]] = []


class NotificationChannelsIn(StrictModel):
    email_enabled: bool | None = None
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_tls: str | None = None
    smtp_username: str | None = None
    smtp_from: str | None = None
    smtp_password: str | None = None     # 給非空才更新；"" 清除；不給保留


class TestEmailIn(StrictModel):
    to: str


def _channels_payload(cfg: dict[str, Any]) -> NotificationChannelsOut:
    from app.services.system_config import NOTIFY_CHANNELS
    return NotificationChannelsOut(
        email_enabled=bool(cfg.get("email_enabled")),
        smtp_host=cfg.get("smtp_host"),
        smtp_port=int(cfg.get("smtp_port") or 587),
        smtp_tls=cfg.get("smtp_tls") or "starttls",
        smtp_username=cfg.get("smtp_username"),
        smtp_from=cfg.get("smtp_from"),
        smtp_password_set=bool(cfg.get("smtp_password_enc")),
        channels=[{"key": k, "available": avail} for k, avail in NOTIFY_CHANNELS],
    )


@router.get("/notification-channels", response_model=NotificationChannelsOut)
async def get_notification_channels_ep(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotificationChannelsOut:
    from app.services.system_config import get_notification_channels
    return _channels_payload(await get_notification_channels(session))


@router.put("/notification-channels", response_model=NotificationChannelsOut)
async def put_notification_channels_ep(
    payload: NotificationChannelsIn,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotificationChannelsOut:
    from app.services.system_config import set_notification_channels
    if payload.smtp_tls is not None and payload.smtp_tls not in ("none", "starttls", "tls"):
        from fastapi import HTTPException
        raise HTTPException(400, detail="smtp_tls must be none/starttls/tls")
    data = payload.model_dump(exclude_unset=True)
    cfg = await set_notification_channels(session, data=data, updated_by_user_id=user.id)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system_setting", object_id=None, action="update",
        diff={"notification_channels": {k: v for k, v in data.items() if k != "smtp_password"}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return _channels_payload(cfg)


@router.post("/notification-channels/test-email")
async def test_notification_email(
    payload: TestEmailIn,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    from fastapi import HTTPException

    from app.services.email import EmailNotConfigured, EmailSendError, send_email_via_config
    from app.services.system_config import get_notification_channels
    cfg = await get_notification_channels(session)
    # 測試信不要求「已啟用」——管理員是在啟用前先驗證 SMTP；只要有 host 即可送
    cfg = {**cfg, "email_enabled": True}
    if not cfg.get("smtp_host"):
        raise HTTPException(400, detail="missing_smtp_host")
    try:
        await send_email_via_config(
            cfg, to=payload.to.strip(),
            subject="[jt-ipam] 測試通知信",
            body_text="這是一封來自 jt-ipam 的測試通知信。若你收到此信，代表 Email 通知設定正確。",
        )
    except EmailNotConfigured:
        raise HTTPException(400, detail="missing_smtp_host") from None
    except EmailSendError as exc:
        raise HTTPException(502, detail=f"SMTP send failed: {exc}") from exc
    return {"ok": True}
