"""System settings endpoints — admin only。

目前只有 LLM 設定；之後其他 system-level setting 也丟這。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
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
    provider: str   # "builtin" | "osm" | "google"


_MAP_PROVIDERS = ("builtin", "osm", "google")


@public_router.get("/map-provider", response_model=MapProviderOut)
async def get_map_provider(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MapProviderOut:
    from app.models.system_setting import SystemSetting
    row = await session.get(SystemSetting, "map_provider")
    prov = (row.value.get("provider") if row and isinstance(row.value, dict) else None) or "builtin"
    return MapProviderOut(provider=prov if prov in _MAP_PROVIDERS else "builtin")


@router.put("/map-provider", response_model=MapProviderOut)
async def put_map_provider(
    payload: MapProviderOut,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MapProviderOut:
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.system_setting import SystemSetting
    prov = payload.provider if payload.provider in _MAP_PROVIDERS else "builtin"
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


class UiDisplayOut(StrictModel):
    # 異動記錄超過幾天的項目以淡色顯示；0 = 不淡化
    change_log_dim_days: int = 30


@public_router.get("/ui-display", response_model=UiDisplayOut)
async def get_ui_display(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UiDisplayOut:
    from app.services.system_config import get_change_log_dim_days
    return UiDisplayOut(change_log_dim_days=await get_change_log_dim_days(session))


@router.put("/ui-display", response_model=UiDisplayOut)
async def put_ui_display(
    payload: UiDisplayOut,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UiDisplayOut:
    from app.services.system_config import set_change_log_dim_days
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "ui_display", "change_log_dim_days": payload.change_log_dim_days},
        request_id=getattr(request.state, "request_id", None),
    )
    days = await set_change_log_dim_days(
        session, days=payload.change_log_dim_days, updated_by_user_id=user.id)
    return UiDisplayOut(change_log_dim_days=days)


class ConsoleSecurityOut(StrictModel):
    # 允許 RDP 控制端把文字貼到被控端（剪貼簿單向重導；預設關閉）
    rdp_clipboard_paste: bool = False


@public_router.get("/console-security", response_model=ConsoleSecurityOut)
async def get_console_security(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConsoleSecurityOut:
    from app.services.system_config import get_rdp_clipboard_paste
    return ConsoleSecurityOut(rdp_clipboard_paste=await get_rdp_clipboard_paste(session))


@router.put("/console-security", response_model=ConsoleSecurityOut)
async def put_console_security(
    payload: ConsoleSecurityOut,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConsoleSecurityOut:
    from app.services.system_config import set_rdp_clipboard_paste
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "console_security", "rdp_clipboard_paste": payload.rdp_clipboard_paste},
        request_id=getattr(request.state, "request_id", None),
    )
    enabled = await set_rdp_clipboard_paste(
        session, enabled=payload.rdp_clipboard_paste, updated_by_user_id=user.id)
    return ConsoleSecurityOut(rdp_clipboard_paste=enabled)


# 本機地圖圖磚代理（OSM）：讓「OpenStreetMap」供應商在維持嚴格 CSP（img-src 'self'）+ COEP require-corp
# 下仍能在頁內顯示圖磚。URL 由伺服器端組（只連 OSM、z/x/y 驗證為整數範圍）→ 非開放代理、非 SSRF。
# 供 <img> 載入故不帶 auth header（token 走 Authorization，圖磚標籤帶不了）；由 nginx /api 限流保護。
# 小型記憶體 LRU 對 OSM 圖磚政策友善（避免重複抓取）。
_TILE_CACHE: OrderedDict[str, bytes] = OrderedDict()
_TILE_CACHE_MAX = 512
_OSM_HOSTS = ("a", "b", "c")
_TILE_HEADERS = {"Cache-Control": "public, max-age=604800"}


@public_router.get("/map-tile/{z}/{x}/{y}")
async def map_tile(z: int, x: int, y: int) -> Response:
    if not (0 <= z <= 19):
        raise HTTPException(status_code=400, detail="bad zoom")
    n = 1 << z
    if not (0 <= x < n and 0 <= y < n):
        raise HTTPException(status_code=400, detail="bad tile coordinate")
    key = f"{z}/{x}/{y}"
    cached = _TILE_CACHE.get(key)
    if cached is not None:
        _TILE_CACHE.move_to_end(key)
        return Response(content=cached, media_type="image/png", headers=_TILE_HEADERS)
    host = _OSM_HOSTS[(x + y) % 3]
    url = f"https://{host}.tile.openstreetmap.org/{z}/{x}/{y}.png"
    try:
        resp = await safe_request(
            "GET", url, timeout=10.0,
            headers={"User-Agent": "jt-ipam/1.0 (self-hosted IPAM; +https://github.com/jasoncheng7115/jt-ipam)"},
        )
    except (UnsafeOutboundURL, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail="tile upstream error") from exc
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"tile upstream {resp.status_code}")
    data = resp.content
    _TILE_CACHE[key] = data
    _TILE_CACHE.move_to_end(key)
    while len(_TILE_CACHE) > _TILE_CACHE_MAX:
        _TILE_CACHE.popitem(last=False)
    return Response(content=data, media_type="image/png", headers=_TILE_HEADERS)


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
    num_ctx: int | None = None
    mcp_external_enabled: bool = False
    mcp_api_key_set: bool = False        # 是否已產生對外 MCP 金鑰（不回明文）


class LLMConfigPatch(StrictModel):
    enabled: bool | None = None
    url: Annotated[str | None, Field(min_length=4, max_length=512)] = None
    embedding_model: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    chat_model: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    timeout: Annotated[float | None, Field(ge=1.0, le=600.0)] = None
    # 0 / 空＝沿用模型/Ollama 預設；上限取寬鬆合理值（128k）
    num_ctx: Annotated[int | None, Field(ge=0, le=131072)] = None
    mcp_external_enabled: bool | None = None


def _llm_out(cfg: Any) -> LLMConfigOut:
    return LLMConfigOut(
        enabled=cfg.enabled, url=cfg.url,
        embedding_model=cfg.embedding_model,
        chat_model=cfg.chat_model, timeout=cfg.timeout,
        num_ctx=cfg.num_ctx,
        mcp_external_enabled=cfg.mcp_external_enabled,
        mcp_api_key_set=bool(cfg.mcp_api_key),
    )


@router.get("/llm", response_model=LLMConfigOut)
async def get_llm(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LLMConfigOut:
    cfg = await get_llm_config(session)
    return _llm_out(cfg)


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
        num_ctx=changes.get("num_ctx"),
        mcp_external_enabled=changes.get("mcp_external_enabled"),
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
    return _llm_out(cfg)


@router.get("/llm/mcp-key")
async def reveal_mcp_key(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """檢視目前的對外 MCP 金鑰明文（管理員專用；尚未產生回 null）。"""
    cfg = await get_llm_config(session)
    return {"api_key": cfg.mcp_api_key}


@router.post("/llm/mcp-key/rotate")
async def rotate_mcp_key(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """產生 / 更換對外 MCP 金鑰（唯讀），綁定目前管理員身份；回傳明文（僅此一次完整顯示）。"""
    from app.services.system_config import rotate_mcp_api_key
    key = await rotate_mcp_api_key(session, principal_user_id=user.id, updated_by_user_id=user.id)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system_setting", object_id=None,
        action="update", diff={"changes": {"mcp_api_key": "rotated"}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return {"api_key": key}


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


def _gather_version_info() -> dict[str, Any]:
    """同步蒐集版本資訊（檔案讀取 / subprocess）→ 由 async 端以 to_thread 呼叫，不擋事件迴圈。"""
    import json
    import platform
    import re
    import shutil
    import subprocess
    import sys
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkgver
    from pathlib import Path

    from app.version import __version__

    # 後端 Python 套件（含連線管理用：asyncssh〔SSH〕、aardwolf〔RDP/VNC，選用〕、
    #                    websockets〔PVE noVNC/xterm 主控台代理〕、Pillow）
    pkgs = [
        "fastapi", "starlette", "sqlalchemy", "pydantic", "asyncpg", "alembic",
        "uvicorn", "httpx", "redis", "argon2-cffi", "cryptography", "defusedxml",
        "authlib", "mcp", "asyncssh", "aardwolf", "websockets", "pillow",
    ]
    versions: dict[str, str | None] = {}
    for p in pkgs:
        try:
            versions[p] = _pkgver(p)
        except PackageNotFoundError:
            versions[p] = None

    # 前端框架版本（讀 frontend/node_modules/<pkg>/package.json 的實裝版本）
    frontend: dict[str, str | None] = {}
    fe_root = Path(__file__).resolve().parents[5] / "frontend" / "node_modules"
    for p in ["vue", "naive-ui", "vite", "typescript", "pinia", "vue-router",
              "vue-i18n", "axios", "@xterm/xterm", "@novnc/novnc", "@iconoir/vue"]:
        ver: str | None = None
        try:
            ver = json.loads((fe_root / p / "package.json").read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError):
            ver = None
        frontend[p] = ver

    def _bin_ver(names: list[str], args: list[str], rx: str) -> str | None:
        for n in names:
            path = shutil.which(n) or (n if Path(n).exists() else None)
            if not path:
                continue
            try:
                out = subprocess.run([path, *args], capture_output=True, text=True, timeout=4)  # noqa: S603
            except (OSError, subprocess.SubprocessError):
                continue
            m = re.search(rx, (out.stdout or "") + (out.stderr or ""))
            if m:
                return m.group(1)
        return None

    os_name: str | None = None
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                os_name = line.split("=", 1)[1].strip().strip('"')
                break
    except OSError:
        os_name = None

    host: dict[str, str | None] = {
        "os": os_name,
        "kernel": platform.release(),
        "nginx": _bin_ver(["nginx", "/usr/sbin/nginx", "/usr/bin/nginx"], ["-v"], r"nginx/([\d.]+)"),
        "node": _bin_ver(["node", "/usr/local/bin/node", "/usr/bin/node"], ["-v"], r"v?([\d.]+)"),
        "postgres": None,
    }
    return {
        "current": __version__,
        "python": sys.version.split()[0],
        "packages": versions,
        "frontend": frontend,
        "host": host,
    }


@router.get("/version")
async def get_version_info() -> dict[str, Any]:
    """現行版本 + Python/後端套件/前端框架/本機環境（OS·kernel·nginx·node·PostgreSQL）版本。

    僅管理員可看（router 已掛 require_admin）；本機環境資訊不對外。
    """
    import asyncio

    info = await asyncio.to_thread(_gather_version_info)
    try:
        from sqlalchemy import text as _sqltext
        from sqlalchemy.exc import SQLAlchemyError

        from app.core.db import SessionLocal
        async with SessionLocal() as s:
            _pg = (await s.execute(_sqltext("SHOW server_version"))).scalar()
            info["host"]["postgres"] = str(_pg).split()[0] if _pg else None
    except SQLAlchemyError:
        pass
    return info


def _ver_tuple(v: str | None) -> tuple[int, ...]:
    """版本字串 → 數字序 tuple（'0.4.199' → (0,4,199)），給「誰比較新」做數值比較。
    純字串比較會把 '0.4.79' 當成新過 '0.4.199'（'7' > '1'）→ 必須用數字序。"""
    import re
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


@router.get("/version/check-latest")
async def check_latest_version() -> dict[str, Any]:
    """查 GitHub 上已發佈的最新版並與現行版本比較。

    發佈方式是 push 到 main 分支（不一定建 release/tag），所以**主要來源直接讀 main 上的
    version.py**；讀不到才退回 releases→tags。比較一律用「數字序」(_ver_tuple)，避免
    0.4.79 被誤判為新過 0.4.199。
    """
    import re

    from app.version import __version__

    headers = {"Accept": "application/vnd.github+json"}
    latest: str | None = None
    error: str | None = None

    # 主要來源：main 分支的 version.py（反映真正已發佈的最新碼）
    try:
        resp = await safe_request(
            "GET",
            f"https://raw.githubusercontent.com/{_GITHUB_REPO}/main/backend/app/version.py",
            timeout=10.0,
        )
        if resp.status_code == 200:
            m = re.search(r"""__version__\s*=\s*["']([0-9][^"']*)""", resp.text)
            if m:
                latest = m.group(1)
    except (UnsafeOutboundURL, httpx.HTTPError) as exc:
        error = f"transport: {exc.__class__.__name__}"

    # 退回：releases/latest →（無 release）tags。GitHub tags 順序非語意序 → 取數字序最大者。
    if latest is None and error is None:
        try:
            resp = await safe_request(
                "GET", f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest",
                timeout=10.0, headers=headers,
            )
            if resp.status_code == 200:
                latest = (resp.json().get("tag_name") or "").lstrip("v") or None
            elif resp.status_code == 404:
                tags_resp = await safe_request(
                    "GET", f"https://api.github.com/repos/{_GITHUB_REPO}/tags",
                    timeout=10.0, headers=headers,
                )
                if tags_resp.status_code == 200:
                    tags = tags_resp.json()
                    names = [
                        (t.get("name") or "").lstrip("v")
                        for t in tags if isinstance(t, dict)
                    ] if isinstance(tags, list) else []
                    names = [n for n in names if n]
                    if names:
                        latest = max(names, key=_ver_tuple)
            else:
                error = f"github http {resp.status_code}"
        except (UnsafeOutboundURL, httpx.HTTPError) as exc:
            error = f"transport: {exc.__class__.__name__}"

    return {
        "current": __version__,
        "latest": latest,
        # 只有「數字序確實比現行新」才算有更新（修 0.4.79>0.4.199 的字串比較 bug）
        "update_available": bool(latest and _ver_tuple(latest) > _ver_tuple(__version__)),
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


@router.get("/notification-matrix")
async def get_notification_matrix_ep(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """通知矩陣：哪些事件走哪些管道（站內 / Email）。回傳目前設定 + 事件登錄順序。"""
    from app.services.system_config import NOTIFY_EVENTS, get_notification_matrix
    return {
        "matrix": await get_notification_matrix(session),
        "events": [e[0] for e in NOTIFY_EVENTS],
    }


@router.put("/notification-matrix")
async def put_notification_matrix_ep(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    from app.services.system_config import NOTIFY_EVENTS, set_notification_matrix
    data = payload.get("matrix", payload) if isinstance(payload, dict) else {}
    mx = await set_notification_matrix(session, data=data, updated_by_user_id=user.id)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system_setting", object_id=None, action="update",
        diff={"notification_matrix": mx},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return {"matrix": mx, "events": [e[0] for e in NOTIFY_EVENTS]}


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
