"""System settings endpoints — admin only。

目前只有 LLM 設定；之後其他 system-level setting 也丟這。
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_global_read
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.safe_http import UnsafeOutboundURL, safe_request, transport_detail
from app.core.ui_error import ui_detail
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
# 需要登入且具全域讀取（不是 admin 專屬）的系統層唯讀資訊
view_router = APIRouter(prefix="/system", tags=["system"],
                        dependencies=[Depends(require_global_read)])


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


class SearchTypeSetting(StrictModel):
    type: Annotated[str, Field(max_length=32)]
    enabled: bool = True
    fields: Annotated[list[Annotated[str, Field(max_length=32)]], Field(max_length=20)] = Field(default_factory=list)


class SearchSettingsIn(StrictModel):
    # 隨打即找（邊打邊出結果）；關掉就按 Enter 才搜尋
    instant: bool = True
    # 順序＝結果的排列順序；沒列到的類型照預設接在後面
    types: Annotated[list[SearchTypeSetting], Field(max_length=50)] = Field(default_factory=list)


class SearchSettingsOut(SearchSettingsIn):
    # 每個類型可以比對的欄位（允許清單）
    catalog: dict[str, list[str]] = Field(default_factory=dict)


def _search_settings_out(s: dict[str, Any]) -> SearchSettingsOut:
    from app.services.search_settings import CATALOG
    return SearchSettingsOut(instant=s["instant"], types=[SearchTypeSetting(**x) for x in s["types"]],
                             catalog={t: list(f) for t, f in CATALOG.items()})


@public_router.get("/search-settings", response_model=SearchSettingsOut)
async def get_search_settings_endpoint(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SearchSettingsOut:
    """全站搜尋的設定（使用者 2026-10-13）。任何登入的人都讀得到：搜尋框要知道要不要隨打即找、怎麼排。"""
    from app.services.search_settings import get_search_settings
    return _search_settings_out(await get_search_settings(session))


@router.put("/search-settings", response_model=SearchSettingsOut)
async def put_search_settings(
    payload: SearchSettingsIn,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SearchSettingsOut:
    from app.core.ui_error import UiError, detail_of
    from app.services.search_settings import set_search_settings
    try:
        s = await set_search_settings(session, payload.model_dump(), updated_by_user_id=user.id)
    except UiError as exc:
        raise HTTPException(status_code=422, detail=detail_of(exc, "search_settings_invalid")) from exc
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "global_search", "instant": s["instant"],
              "types": [f"{x['type']}{'' if x['enabled'] else ' (off)'}: {','.join(x['fields'])}" for x in s["types"]]},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return _search_settings_out(s)


class DevicePortFilterOut(StrictModel):
    # 匯入裝置連接埠時，過濾掉符合這些名稱樣式（正則）的偽介面（Windows NDIS / WAN Miniport
    # 等）。filter_pseudo 是總開關；關閉時匯入不過濾也不清除。
    filter_pseudo: bool = True
    ignore_patterns: list[str] = Field(default_factory=list)


@router.get("/device-port-filter", response_model=DevicePortFilterOut)
async def get_device_port_filter_endpoint(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DevicePortFilterOut:
    from app.services.system_config import get_device_port_filter
    cfg = await get_device_port_filter(session)
    return DevicePortFilterOut(**cfg)


@router.put("/device-port-filter", response_model=DevicePortFilterOut)
async def put_device_port_filter(
    payload: DevicePortFilterOut,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DevicePortFilterOut:
    from app.services.system_config import set_device_port_filter
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "device_ports", "filter_pseudo": payload.filter_pseudo,
              "ignore_patterns": payload.ignore_patterns},
        request_id=getattr(request.state, "request_id", None),
    )
    cfg = await set_device_port_filter(
        session, filter_pseudo=payload.filter_pseudo,
        ignore_patterns=payload.ignore_patterns, updated_by_user_id=user.id)
    return DevicePortFilterOut(**cfg)


class ConsoleSecurityIn(StrictModel):
    """可以改的部分。"""

    # 允許 RDP 控制端把文字貼到被控端（剪貼簿單向重導；預設關閉）
    rdp_clipboard_paste: bool = False
    # RDP 連線引擎：guacd（預設，jt-ipam-guacd 服務，見 app/services/guacd.py）、
    # aardwolf（純 Python）、freerdp（相容性較好，需外部行程）。
    # 沒帶＝維持原值（以前預設成 aardwolf：沒帶這個欄位的舊頁面一存就把引擎改回去）
    rdp_engine: Literal["aardwolf", "freerdp", "guacd"] | None = None
    # VNC／SSH 連線引擎：builtin（一路以來的實作）或 guacd（VNC 的預設）。
    # 沒帶＝維持原值：還開著舊版頁面的人按儲存，不可以把別人剛設好的引擎改回去
    vnc_engine: Literal["builtin", "guacd"] | None = None
    ssh_engine: Literal["builtin", "guacd"] | None = None
    # SFTP 單檔上下傳上限（MB）；沒帶＝維持原值（理由同上）。上界見 SFTP_MAX_FILE_MB_LIMIT
    sftp_max_file_mb: Annotated[int, Field(ge=1, le=102_400)] | None = None
    # 允許主控台經由掃描代理中繼（issue #24 階段二，預設關）；沒帶＝維持原值
    console_relay: bool | None = None


class ConsoleSecurityOut(ConsoleSecurityIn):
    """再加上「這台機器實際上能不能用」。

    設定頁要看得到這一段：選項擺在那裡、按下去才發現缺套件，比沒有這個選項更糟。
    缺什麼與怎麼裝都由後端算好 —— 前端不該自己維護一份套件清單（兩份遲早不一致）。
    """

    freerdp_available: bool = False
    freerdp_missing: list[str] = []
    #: aardwolf（預設引擎與 VNC 主控台都靠它）有沒有裝起來，以及這台的 Python 版本 ——
    #: 裝不起來最常見的原因是 Python 太新、沒有預編譯套件（issue #39）
    aardwolf_available: bool = False
    python_version: str = ""
    freerdp_install_cmd: str = ""
    #: guacd：服務有沒有在跑、哪些協定的外掛載得到（設定頁要看得到，選了才發現不能用更糟）
    guacd_available: bool = False
    guacd_protocols: dict[str, bool] = {}
    guacd_address: str = ""
    guacd_error: str = ""
    guacd_install_cmd: str = ""


@public_router.get("/console-security", response_model=ConsoleSecurityOut)
async def get_console_security(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConsoleSecurityOut:
    from app.services.system_config import (
        get_console_relay_enabled,
        get_rdp_clipboard_paste,
        get_rdp_engine,
        get_sftp_max_file_mb,
        get_ssh_engine,
        get_vnc_engine,
    )
    return await _console_security_out(
        rdp_clipboard_paste=await get_rdp_clipboard_paste(session),
        rdp_engine=await get_rdp_engine(session),
        vnc_engine=await get_vnc_engine(session),
        ssh_engine=await get_ssh_engine(session),
        sftp_max_file_mb=await get_sftp_max_file_mb(session),
        console_relay=await get_console_relay_enabled(session),
    )


@router.put("/console-security", response_model=ConsoleSecurityOut)
async def put_console_security(
    payload: ConsoleSecurityIn,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConsoleSecurityOut:
    from app.services.system_config import (
        get_console_relay_enabled,
        get_rdp_engine,
        get_sftp_max_file_mb,
        get_ssh_engine,
        get_vnc_engine,
        set_console_relay_enabled,
        set_rdp_clipboard_paste,
        set_rdp_engine,
        set_sftp_max_file_mb,
        set_ssh_engine,
        set_vnc_engine,
    )
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        # 換引擎會改變「連得上／連不上」，稽核要看得出是誰換的
        diff={"target": "console_security",
              "rdp_clipboard_paste": payload.rdp_clipboard_paste,
              "rdp_engine": payload.rdp_engine,
              "vnc_engine": payload.vnc_engine, "ssh_engine": payload.ssh_engine,
              "sftp_max_file_mb": payload.sftp_max_file_mb,
              "console_relay": payload.console_relay},
        request_id=getattr(request.state, "request_id", None),
    )
    enabled = await set_rdp_clipboard_paste(
        session, enabled=payload.rdp_clipboard_paste, updated_by_user_id=user.id)
    engine = (await set_rdp_engine(session, engine=payload.rdp_engine, updated_by_user_id=user.id)
              if payload.rdp_engine else await get_rdp_engine(session))
    vnc_engine = (await set_vnc_engine(session, engine=payload.vnc_engine, updated_by_user_id=user.id)
                  if payload.vnc_engine else await get_vnc_engine(session))
    ssh_engine = (await set_ssh_engine(session, engine=payload.ssh_engine, updated_by_user_id=user.id)
                  if payload.ssh_engine else await get_ssh_engine(session))
    sftp_mb = (await set_sftp_max_file_mb(session, mb=payload.sftp_max_file_mb, updated_by_user_id=user.id)
               if payload.sftp_max_file_mb is not None else await get_sftp_max_file_mb(session))
    relay = (await set_console_relay_enabled(session, enabled=payload.console_relay, updated_by_user_id=user.id)
             if payload.console_relay is not None else await get_console_relay_enabled(session))
    return await _console_security_out(rdp_clipboard_paste=enabled, rdp_engine=engine,
                                       vnc_engine=vnc_engine, ssh_engine=ssh_engine,
                                       sftp_max_file_mb=sftp_mb, console_relay=relay)


async def _console_security_out(*, rdp_clipboard_paste: bool, rdp_engine: str,
                                vnc_engine: str = "guacd", ssh_engine: str = "builtin",
                                sftp_max_file_mb: int = 100,
                                console_relay: bool = False) -> ConsoleSecurityOut:
    from app.services import guacd as guac
    from app.services.rdp_freerdp import availability, freerdp_apt_hint

    av = availability()
    missing = list(av["missing_packages"]) + list(av["missing_modules"])
    from app.api.v1.endpoints.rdp_console import RDP_AVAILABLE, python_version
    gst = await guac.probe(use_cache=False)
    return ConsoleSecurityOut(
        rdp_clipboard_paste=rdp_clipboard_paste,
        rdp_engine=rdp_engine,  # type: ignore[arg-type]
        vnc_engine=vnc_engine,  # type: ignore[arg-type]
        ssh_engine=ssh_engine,  # type: ignore[arg-type]
        sftp_max_file_mb=sftp_max_file_mb,
        console_relay=console_relay,
        freerdp_available=bool(av["ok"]),
        freerdp_missing=missing,
        freerdp_install_cmd="" if av["ok"] else freerdp_apt_hint(),
        aardwolf_available=RDP_AVAILABLE,
        python_version=python_version(),
        guacd_available=bool(gst["ok"]),
        guacd_protocols=dict(gst["protocols"]),
        guacd_address=str(gst["address"]),
        guacd_error=str(gst.get("error") or ""),
        guacd_install_cmd="" if gst["ok"] else "sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade --with-guacd",
    )


@router.post("/sftp-probe/ticket")
async def issue_sftp_probe_ticket(user: CurrentUser, request: Request) -> dict[str, Any]:
    """SFTP 傳輸路徑測試的票證（管理者限定，見 app/services/sftp_probe.py）。

    測試要由瀏覽器發起、走跟 SFTP 同一條 WebSocket 路徑 —— 只有那樣才會經過前面的反向代理。
    不寫稽核：不讀也不改任何資料，只把一段隨機資料來回送一次。
    """
    import json as _json
    import secrets as _secrets

    from app.api.v1.endpoints.sftp_console import _probe_ticket_key, _redis_client
    from app.core.rate_limit import limit_per_ip
    from app.services import sftp_probe

    await limit_per_ip(request, name="ssh")
    ticket = _secrets.token_urlsafe(32)
    await _redis_client().set(_probe_ticket_key(ticket),
                              _json.dumps({"user_id": str(user.id)}), ex=60)
    return {
        "ticket": ticket,
        "ws_path": f"/api/v1/addresses/{sftp_probe.PROBE_ADDRESS_ID}/sftp/ws",
        "up_bytes": sftp_probe.DEFAULT_TEST_BYTES,
        "down_bytes": sftp_probe.DEFAULT_TEST_BYTES,
        "ttl": 60,
    }


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
class LivenessSourceOut(StrictModel):
    """設定頁要顯示的一個候選來源。"""

    key: str
    #: 這個來源的證據會不會過期 —— 不會過期的勾了就等於「看過一次就永遠上線」
    aging: bool
    #: 這個站台真的有這個整合嗎（沒有的就別佔版面）
    configured: bool


class OnlineGraceOut(StrictModel):
    minutes: Annotated[int, Field(ge=1, le=43200)]
    #: 哪些證據算「上線」。LibreNMS 的 ARP 預設不勾：它沒有時間概念，來源設備的
    #: 快取不老化就會讓關機的機器一直顯示上線（來源契約見 services/evidence.py）
    sources: list[str] = ["scanner", "librenms"]
    #: 候選來源（GET 才有值；PUT 的回應沿用同一個模型，可為空）
    available: list[LivenessSourceOut] = []


class CooldownOut(StrictModel):
    days: Annotated[int, Field(ge=0, le=3650)]


class CertExpiryOut(StrictModel):
    """憑證到期通知的**全域預設**天數。每張憑證仍可各自覆寫。"""

    days: Annotated[int, Field(ge=1, le=365)]


@public_router.get("/cert-expiry-alert", response_model=CertExpiryOut)
async def get_cert_expiry_alert(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CertExpiryOut:
    from app.services.system_config import get_cert_expiry_days
    return CertExpiryOut(days=await get_cert_expiry_days(session))


@router.put("/cert-expiry-alert", response_model=CertExpiryOut)
async def put_cert_expiry_alert(
    payload: CertExpiryOut, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CertExpiryOut:
    from app.services.system_config import set_cert_expiry_days
    days = await set_cert_expiry_days(session, days=payload.days, updated_by_user_id=user.id)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "cert_expiry_alert", "days": days},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return CertExpiryOut(days=days)


@public_router.get("/ip-cooldown", response_model=CooldownOut)
async def get_ip_cooldown(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CooldownOut:
    from app.services.ip_lifecycle import get_cooldown_days
    return CooldownOut(days=await get_cooldown_days(session))


@router.put("/ip-cooldown", response_model=CooldownOut)
async def put_ip_cooldown(
    payload: CooldownOut, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CooldownOut:
    from app.services.ip_lifecycle import set_cooldown_days
    days = await set_cooldown_days(session, days=payload.days, updated_by_user_id=user.id)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "ip_cooldown", "days": days},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return CooldownOut(days=days)


@public_router.get("/online-grace", response_model=OnlineGraceOut)
async def get_online_grace(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OnlineGraceOut:
    from app.services.system_config import get_liveness_config
    cfg = await get_liveness_config(session)
    chosen = list(cfg["sources"])
    return OnlineGraceOut(
        minutes=int(cfg["minutes"]), sources=chosen,
        available=await _liveness_candidates(session, chosen),
    )


async def _liveness_candidates(
    session: AsyncSession, chosen: list[str],
) -> list[LivenessSourceOut]:
    """候選來源清單：**只列這個站台真的有的整合**，加上已經被勾選的（不能讓設定
    因為某個整合被停用就從畫面消失、卻仍在後端生效）。

    掃描代理永遠列出來（它是內建能力，不是整合）。
    """
    from sqlalchemy import func, select

    from app.models.checkpoint_gaia import CheckPointGaiaTarget
    from app.models.firewall import OPNsenseFirewall
    from app.models.fortigate import FortiGateFirewall
    from app.models.librenms import LibreNMSInstance
    from app.models.mikrotik import MikroTikRouter
    from app.models.paloalto import PaloAltoFirewall
    from app.models.pfsense import PfSenseFirewall
    from app.models.wazuh import WazuhInstance
    from app.models.zabbix import ZabbixInstance
    from app.services.evidence import LIVENESS_SOURCES, is_aging

    async def _has(model: Any) -> bool:
        return bool(await session.scalar(select(func.count()).select_from(model)))

    present = {
        "scanner": True,
        # 「跟著所屬設備」是內建的（勾在 IP 上），跟掃描代理一樣永遠列出
        "device": True,
        "librenms": await _has(LibreNMSInstance),
        "wazuh": await _has(WazuhInstance),
        "zabbix": await _has(ZabbixInstance),
        "opnsense": await _has(OPNsenseFirewall),
        "pfsense": await _has(PfSenseFirewall),
        "fortigate": await _has(FortiGateFirewall),
        "paloalto": await _has(PaloAltoFirewall),
        "mikrotik": await _has(MikroTikRouter),
        # Check Point 的 ARP／租約來自閘道的 Gaia API（第二階段），有設定閘道才算
        "checkpoint": await _has(CheckPointGaiaTarget),
    }
    out: list[LivenessSourceOut] = []
    for key in LIVENESS_SOURCES:
        vendor = key.split(":", 1)[1] if ":" in key else key
        # 舊的籠統 "arp" 等同 arp:librenms
        configured = present.get("librenms" if key == "arp" else vendor, False)
        if configured or key in chosen:
            out.append(LivenessSourceOut(
                key=key, aging=is_aging(key), configured=configured))
    return out


@router.put("/online-grace", response_model=OnlineGraceOut)
async def put_online_grace(
    payload: OnlineGraceOut,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OnlineGraceOut:
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.system_setting import SystemSetting
    from app.services.evidence import LIVENESS_SOURCES
    m = min(43200, max(1, int(payload.minutes)))
    srcs = [s for s in payload.sources if s in LIVENESS_SOURCES]
    row = await session.get(SystemSetting, "online_grace_minutes")
    if row is None:
        row = SystemSetting(key="online_grace_minutes", value={}, updated_by=user.id)
        session.add(row)
    # known：存的當下畫面上有哪些來源 —— 之後新增的來源才分得出「沒看過」與「看過、沒勾」
    row.value = {"minutes": m, "sources": srcs, "known": list(LIVENESS_SOURCES)}
    row.updated_by = user.id
    flag_modified(row, "value")
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "online_grace_minutes", "minutes": m, "sources": ",".join(srcs)},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return OnlineGraceOut(minutes=m, sources=srcs,
                          available=await _liveness_candidates(session, srcs))


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
    from datetime import UTC, datetime

    from app.services.background_tasks import record_finished_task
    from app.services.geoip import get_geoip_config, update_databases, update_outcome
    started = datetime.now(UTC)
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
    ok, err = update_outcome(result)
    await record_finished_task(session, kind="geoip.refresh", ok=ok, actor_user_id=user.id,
                               started_at=started, summary=result, error=err)
    cfg = await get_geoip_config(session)
    return {"result": result, "config": cfg}


class LLMConfigOut(StrictModel):
    enabled: bool
    # ollama（原生）或 openai（OpenAI 相容端點）。金鑰只回「有沒有設」，不回明文。
    provider: str = "ollama"
    api_key_set: bool = False
    url: str
    embedding_model: str
    # 嵌入模型的位址；空＝沿用 url（GitHub issue #33：兩種模型常分開部署）
    embedding_base_url: str = ""
    chat_model: str
    timeout: float
    num_ctx: int | None = None
    mcp_external_enabled: bool = False
    mcp_api_key_set: bool = False        # 是否已產生對外 MCP 金鑰（不回明文）
    mcp_api_key_expires_at: datetime | None = None   # 到期時間（過期的金鑰會被拒絕）
    mcp_api_key_last_used_at: datetime | None = None  # 目前這把金鑰最後一次被 MCP 用戶端使用（重產後歸零）
    mcp_api_key_last_used_ip: str | None = None
    ai_audit_enabled: bool = False
    ai_audit_times: list[str] = []       # 執行時刻（"HH:MM"，伺服器本地時區）
    # 排程的「哪幾天」：daily / weekly（配 weekdays）／monthly（配 month_day）
    ai_audit_frequency: str = "daily"
    ai_audit_weekdays: list[int] = []    # 1=週一 … 7=週日
    ai_audit_month_day: int = 1          # 1–31；短月自動落在該月最後一天
    ai_audit_model: str | None = None    # None＝沿用對話模型
    ai_audit_num_ctx: int | None = None  # None＝沿用對話模型的上下文長度
    # AI 判讀（未授權 IP 判讀／IP 調查／防火牆規則異動解讀）；None＝沿用對話模型
    ai_interpret_model: str | None = None
    ai_interpret_num_ctx: int | None = None
    # 判讀／巡檢自己的 LLM Server；位址空字串＝繼承對話設定。金鑰只回「有沒有設」
    ai_interpret_provider: str = "ollama"
    ai_interpret_url: str = ""
    ai_interpret_api_key_set: bool = False
    ai_audit_provider: str = "ollama"
    ai_audit_url: str = ""
    ai_audit_api_key_set: bool = False
    chat_thinking: bool = True           # AI 對話允許模型先思考（False＝送關閉思考的參數）
    server_timezone: str = ""            # 排程時刻是照這個時區算的，UI 要講清楚


class LLMConfigPatch(StrictModel):
    enabled: bool | None = None
    provider: Literal["ollama", "openai"] | None = None
    # 空字串＝清掉金鑰（本地 vLLM／LM Studio 多半不需要），所以 min_length 是 0
    api_key: Annotated[str | None, Field(min_length=0, max_length=512)] = None
    url: Annotated[str | None, Field(min_length=4, max_length=512)] = None
    embedding_model: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    # 空字串＝清掉，回到「沿用對話模型的位址」，所以 min_length 是 0
    embedding_base_url: Annotated[str | None, Field(min_length=0, max_length=512)] = None
    chat_model: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    timeout: Annotated[float | None, Field(ge=1.0, le=600.0)] = None
    # 0 / 空＝沿用模型/Ollama 預設；上限取寬鬆合理值（128k）
    num_ctx: Annotated[int | None, Field(ge=0, le=131072)] = None
    mcp_external_enabled: bool | None = None
    ai_audit_enabled: bool | None = None
    # 執行時刻清單（"HH:MM"）。不合法的項目會被丟掉，不會整組失效。
    ai_audit_times: Annotated[list[str] | None, Field(max_length=24)] = None
    ai_audit_frequency: Literal["daily", "weekly", "monthly"] | None = None
    # 1=週一 … 7=週日；空清單不會被存（那等於排程永遠不觸發，但畫面上還開著）
    ai_audit_weekdays: Annotated[list[int] | None, Field(max_length=7)] = None
    ai_audit_month_day: Annotated[int | None, Field(ge=1, le=31)] = None
    # 巡檢專用模型；空字串＝清掉，回去沿用對話模型（所以 min_length 是 0）
    ai_audit_model: Annotated[str | None, Field(max_length=128)] = None
    # 巡檢專用上下文長度；0＝清掉，回去沿用對話模型的設定
    ai_audit_num_ctx: Annotated[int | None, Field(ge=0, le=131072)] = None
    # AI 判讀專用模型／上下文長度；空字串／0 ＝清掉，回去沿用對話模型
    ai_interpret_model: Annotated[str | None, Field(max_length=128)] = None
    ai_interpret_num_ctx: Annotated[int | None, Field(ge=0, le=131072)] = None
    # 判讀／巡檢自己的 LLM Server：位址空字串＝回到繼承對話設定（供應商與金鑰一起清掉）；
    # 金鑰空字串＝清掉、沒帶＝不動
    ai_interpret_provider: Literal["ollama", "openai"] | None = None
    ai_interpret_url: Annotated[str | None, Field(min_length=0, max_length=512)] = None
    ai_interpret_api_key: Annotated[str | None, Field(min_length=0, max_length=512)] = None
    ai_audit_provider: Literal["ollama", "openai"] | None = None
    ai_audit_url: Annotated[str | None, Field(min_length=0, max_length=512)] = None
    ai_audit_api_key: Annotated[str | None, Field(min_length=0, max_length=512)] = None
    chat_thinking: bool | None = None


def _server_tz() -> str:
    """伺服器本地時區名稱。排程時刻是照它算的 —— 畫面上不寫清楚就會有人設錯 8 小時。"""
    from datetime import datetime
    tz = datetime.now().astimezone().tzinfo
    return str(getattr(tz, "key", None) or tz or "")


def _llm_out(cfg: Any, usage: tuple[Any, Any] = (None, None)) -> LLMConfigOut:
    return LLMConfigOut(
        mcp_api_key_last_used_at=usage[0], mcp_api_key_last_used_ip=usage[1],
        enabled=cfg.enabled, url=cfg.url,
        embedding_model=cfg.embedding_model,
        embedding_base_url=getattr(cfg, "embedding_base_url", None) or "",
        chat_model=cfg.chat_model, timeout=cfg.timeout,
        provider=cfg.provider, api_key_set=bool(cfg.api_key),
        num_ctx=cfg.num_ctx,
        mcp_external_enabled=cfg.mcp_external_enabled,
        mcp_api_key_set=bool(cfg.mcp_api_key),
        mcp_api_key_expires_at=cfg.mcp_api_key_expires_at if cfg.mcp_api_key else None,
        ai_audit_enabled=cfg.ai_audit_enabled,
        ai_audit_times=cfg.ai_audit_times,
        ai_audit_frequency=cfg.ai_audit_frequency,
        ai_audit_weekdays=cfg.ai_audit_weekdays,
        ai_audit_month_day=cfg.ai_audit_month_day,
        ai_audit_model=cfg.ai_audit_model,
        ai_audit_num_ctx=cfg.ai_audit_num_ctx,
        ai_interpret_model=cfg.ai_interpret_model,
        ai_interpret_num_ctx=cfg.ai_interpret_num_ctx,
        ai_interpret_provider=cfg.ai_interpret_provider or "ollama",
        ai_interpret_url=cfg.ai_interpret_url or "",
        ai_interpret_api_key_set=bool(cfg.ai_interpret_api_key),
        ai_audit_provider=cfg.ai_audit_provider or "ollama",
        ai_audit_url=cfg.ai_audit_url or "",
        ai_audit_api_key_set=bool(cfg.ai_audit_api_key),
        chat_thinking=cfg.chat_thinking,
        server_timezone=_server_tz(),
    )


@router.get("/llm", response_model=LLMConfigOut)
async def get_llm(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LLMConfigOut:
    cfg = await get_llm_config(session)
    from app.services.system_config import get_mcp_key_usage
    return _llm_out(cfg, await get_mcp_key_usage(session, cfg.mcp_api_key_id))


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
        provider=changes.get("provider"),
        api_key=changes.get("api_key"),
        url=changes.get("url"),
        embedding_model=changes.get("embedding_model"),
        embedding_base_url=changes.get("embedding_base_url"),
        chat_model=changes.get("chat_model"),
        timeout=changes.get("timeout"),
        num_ctx=changes.get("num_ctx"),
        mcp_external_enabled=changes.get("mcp_external_enabled"),
        ai_audit_enabled=changes.get("ai_audit_enabled"),
        ai_audit_times=changes.get("ai_audit_times"),
        ai_audit_frequency=changes.get("ai_audit_frequency"),
        ai_audit_weekdays=changes.get("ai_audit_weekdays"),
        ai_audit_month_day=changes.get("ai_audit_month_day"),
        ai_audit_model=changes.get("ai_audit_model"),
        ai_audit_num_ctx=changes.get("ai_audit_num_ctx"),
        ai_interpret_model=changes.get("ai_interpret_model"),
        ai_interpret_num_ctx=changes.get("ai_interpret_num_ctx"),
        ai_interpret_provider=changes.get("ai_interpret_provider"),
        ai_interpret_url=changes.get("ai_interpret_url"),
        ai_interpret_api_key=changes.get("ai_interpret_api_key"),
        ai_audit_provider=changes.get("ai_audit_provider"),
        ai_audit_url=changes.get("ai_audit_url"),
        ai_audit_api_key=changes.get("ai_audit_api_key"),
        chat_thinking=changes.get("chat_thinking"),
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
    from app.services.system_config import get_mcp_key_usage
    return _llm_out(cfg, await get_mcp_key_usage(session, cfg.mcp_api_key_id))


@router.get("/llm/mcp-key")
async def reveal_mcp_key(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """檢視目前的對外 MCP 金鑰明文（管理員專用；尚未產生回 null）。每次檢視都留稽核。"""
    cfg = await get_llm_config(session)
    if cfg.mcp_api_key:
        await append_audit(
            session, actor_user_id=str(user.id),
            actor_ip=request.client.host if request.client else None,
            actor_user_agent=request.headers.get("user-agent"),
            object_type="system_setting", object_id=None, action="secret_view",
            diff={"setting": "mcp_api_key"},
            request_id=getattr(request.state, "request_id", None),
        )
        await session.commit()
    return {"api_key": cfg.mcp_api_key}


class McpKeyRotateIn(StrictModel):
    #: 有效天數（1–365）；不送 body 時用預設 90 天
    expires_in_days: int = Field(default=90, ge=1, le=365)


@router.post("/llm/mcp-key/rotate")
async def rotate_mcp_key(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    payload: Annotated[McpKeyRotateIn | None, Body()] = None,
) -> dict[str, Any]:
    """產生 / 更換對外 MCP 金鑰（唯讀），綁定目前管理員身份；回傳明文（僅此一次完整顯示）與到期時間。"""
    from app.services.system_config import rotate_mcp_api_key
    days = (payload or McpKeyRotateIn()).expires_in_days
    key = await rotate_mcp_api_key(session, principal_user_id=user.id, updated_by_user_id=user.id,
                                   expires_in_days=days)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system_setting", object_id=None,
        action="update", diff={"changes": {"mcp_api_key": "rotated"}, "expires_in_days": days},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    cfg = await get_llm_config(session)
    exp = cfg.mcp_api_key_expires_at
    return {"api_key": key, "expires_at": exp.isoformat() if exp else None}


@router.get("/llm/models")
async def list_ollama_models(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    role: Literal["chat", "interpret", "audit"] = "chat",
) -> dict[str, Any]:
    """列出 LLM 端點上可用的模型清單（給設定頁的下拉選用）。

    Ollama 走 `/api/tags`、OpenAI 相容走 `/v1/models` —— 兩者的回應結構也不同。
    `role` 指定哪一個用途的 LLM Server（判讀／巡檢可以各接一台，沒設就是對話那台）。
    """
    from app.services import ai as ai_mod
    from app.services.system_config import role_config

    cfg = role_config(await get_llm_config(session), role)
    provider = getattr(cfg, "provider", "ollama") or "ollama"
    url = ai_mod.models_url(cfg.url, provider)
    headers = ai_mod.auth_headers(provider, getattr(cfg, "api_key", None))
    try:
        resp = await safe_request("GET", url, timeout=10.0, headers=headers)
    except UnsafeOutboundURL as exc:
        # 被自己的 SSRF 防護擋下**是預期內的結果**，不是伺服器故障 —— 尤其
        # 預設的 Ollama 位址就是 loopback（`http://127.0.0.1:11434`），而 loopback
        # 在 safe_http 是一律封鎖的。原本這裡只接 httpx 的錯誤，於是設定頁一開就是
        # 四個 500，畫面上只有「伺服器發生錯誤」，看不出被擋的是哪個位址、怎麼放行。
        return {"models": [], "error_detail": ui_detail(
            "llm_models_ssrf_blocked",
            f"{exc} —— 這個位址被連外防護擋下了。若確定要連到這台機器上的服務，"
            f"請在 backend.env 的 OUTBOUND_ALLOW_CIDRS 加入該網段（例如 127.0.0.0/8）"
            f"後重啟後端。",
            reason=str(exc)[:200],
        )}
    except httpx.HTTPError as exc:
        return {"models": [], "error_detail": ui_detail(
            "llm_models_unreachable", f"{type(exc).__name__}: {exc}",
            reason=f"{type(exc).__name__}: {exc}"[:200])}
    if resp.status_code != 200:
        return {"models": [], "error_detail": ui_detail(
            "llm_models_http", f"HTTP {resp.status_code}: {resp.text[:200]}",
            status=resp.status_code, body=resp.text[:200])}
    return {"models": ai_mod.parse_models(resp.json() or {}, provider)}


# ─────────────────── 依 MAC 自動掛裝置 ───────────────────


class AutolinkOut(StrictModel):
    enabled: bool = False
    scope_subnet_ids: list[str] | None = None


class AutolinkPatch(StrictModel):
    enabled: bool | None = None
    scope_subnet_ids: list[str] | None = None


@router.get("/ip-device-autolink", response_model=AutolinkOut)
async def get_autolink(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    from app.services.system_config import get_autolink_config
    return AutolinkOut(**await get_autolink_config(session))


@router.put("/ip-device-autolink", response_model=AutolinkOut)
async def put_autolink(
    payload: AutolinkPatch, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    from app.services.system_config import set_autolink_config
    cfg = await set_autolink_config(
        session, enabled=payload.enabled, scope_subnet_ids=payload.scope_subnet_ids,
        updated_by_user_id=user.id,
    )
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system_setting", object_id=None, action="update",
        diff={"target": "ip_device_autolink", **cfg},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return AutolinkOut(**cfg)


@router.post("/ip-device-autolink/preview")
async def preview_autolink(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """先看會動到什麼再開啟 —— 這是會改資料的作業。

    只計算不寫入，並附上明細；跳過的筆數也一起回報（「全部被守門擋下」不能看起來
    跟「沒事可做」一樣）。
    """
    from app.services.ip_device_link import link_by_port_mac
    from app.services.system_config import get_autolink_config
    cfg = await get_autolink_config(session)
    st = await link_by_port_mac(session, dry_run=True,
                               scope_subnet_ids=cfg["scope_subnet_ids"])
    return {
        "would_link": st.linked,
        "samples": st.samples[:100],
        "skipped": {
            "ambiguous_mac": st.skipped_ambiguous,
            "manually_edited": st.skipped_manual,
            "invalid_mac": st.skipped_invalid_mac,
            "hostname_mismatch": st.skipped_hostname_mismatch,
            "customer_conflict": st.skipped_customer,
        },
    }


# ─────────────────── RBAC：權限指派 ───────────────────
import uuid as _uuid
from typing import Literal as _Literal

from sqlalchemy import func as _func
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


@router.get("/permissions/summary")
async def permissions_summary(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """權限指派清單用：每個帳號掛了哪些角色、有幾筆直接的物件授權（2026-10-22）。
    一次算全部帳號（兩個 GROUP BY），清單不必逐一打開每個人。"""
    from app.models.user import Group as _Group
    from app.models.user import UserGroupMember as _Member

    out: dict[str, Any] = {}
    for uid, name in (await session.execute(
            _select(_Member.user_id, _Group.name).join(_Group, _Group.id == _Member.group_id)
            .order_by(_Group.name))).all():
        out.setdefault(str(uid), {"roles": [], "grants": 0})["roles"].append(name)
    for pid, n in (await session.execute(
            _select(_Permission.principal_id, _func.count()).where(_Permission.principal_type == "user")
            .group_by(_Permission.principal_id))).all():
        out.setdefault(str(pid), {"roles": [], "grants": 0})["grants"] = int(n)
    return out


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

    from app.version import __license__, __version__

    # 授權條款：優先讀安裝後的套件 metadata（那份是由 pyproject 產生的，不會與封裝
    # 脫節）；沒有 metadata 時（直接從原始碼跑）退回程式碼裡的常數。
    def _license() -> str:
        try:
            from importlib.metadata import metadata as _dist_meta
            declared = _dist_meta("jt-ipam-backend").get("License")
        except Exception:
            declared = None
        return declared or __license__

    # 後端 Python 套件（含連線管理用：asyncssh〔SSH〕、aardwolf〔RDP/VNC，選用〕、
    #                    websockets〔PVE noVNC/xterm 主控台代理〕、Pillow）
    # 完整列出 pyproject 的執行期相依（使用者要求核對「有沒有沒顯示出來的」）——
    # 新增相依時記得同步這份清單（TEST_CHECKLIST 手動點檢版本頁時會對照）。
    pkgs = [
        "fastapi", "starlette", "sqlalchemy", "pydantic", "pydantic-settings",
        "asyncpg", "alembic", "uvicorn", "httpx", "redis", "celery",
        "argon2-cffi", "cryptography", "defusedxml", "pyjwt", "pyotp",
        "python-multipart", "email-validator", "urllib3", "multidict", "aiohttp",
        "authlib", "python3-saml", "ldap3", "pyrad",
        "pymysql", "asyncssh", "dnspython", "pywinrm", "geoip2",
        "pgvector", "mcp",
        "aardwolf", "websockets", "pillow", "fpdf2", "certifi",
        "structlog", "python-json-logger",
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
    # 完整列出 package.json 的執行期相依（＋建置工具 vite/typescript）。
    # 這份清單與後端那份一樣是手寫的，會靜靜過期 —— tests/test_dependency_page.py
    # 會拿它跟實際宣告的相依比對，漏了就擋下來。
    for p in ["vue", "naive-ui", "vite", "typescript", "pinia", "vue-router",
              "vue-i18n", "axios", "@xterm/xterm", "@xterm/addon-fit",
              "@xterm/addon-unicode11", "@xterm/addon-web-links",
              "@novnc/novnc", "@iconoir/vue", "@vueuse/core",
              "cytoscape", "cytoscape-cose-bilkent", "fzstd", "qrcode", "tweetnacl", "vfonts", "zod",
              "@fontsource-variable/inter", "@fontsource-variable/exo-2", "@fontsource-variable/orbitron",
              "@fontsource/audiowide", "@fontsource/rajdhani", "@fontsource/share-tech-mono"]:
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
        "license": _license(),
        "python": sys.version.split()[0],
        "packages": versions,
        "frontend": frontend,
        "host": host,
    }


@router.get("/doctor")
async def system_doctor(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """系統診斷（admin）：後端查得到的檢查一次跑完，每一項都附「該怎麼修」。

    由來（2026-09-05 客戶回報）：儀表板數得出 55 台裝置、裝置清單卻是 500 加空白，
    原因是資料庫結構落後於程式。系統其實查得出來，卻沒有任何地方講 ——
    這一支就是把「後端知道但沒說」的事情攤開來。

    系統層的檢查（systemd、nginx、備份檔、掃描代理）後端看不到，回應裡會註明，
    要用伺服器上的 `jt-ipam.sh doctor`。
    """
    from app.services.self_check import run_checks
    return (await run_checks(session)).as_dict()


@router.get("/doctor/stats")
async def system_doctor_stats(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """系統診斷的「資料統計」：各類資料各有幾筆（admin）。只在本機計算，不回傳、不蒐集。"""
    from datetime import UTC, datetime

    from app.services.data_stats import collect
    return {"generated_at": datetime.now(UTC).isoformat(), "groups": await collect(session)}


@router.get("/doctor/storage")
async def system_doctor_storage(
    session: Annotated[AsyncSession, Depends(get_session)],
    refresh: bool = Query(False, description="重新量主機上的目錄（預設用幾分鐘內的結果）"),
) -> dict[str, Any]:
    """系統診斷的「容量用量」（admin）：資料庫依功能分類、最大的資料表、主機上 jt-ipam 的各個目錄。
    只在本機計算，不回傳、不蒐集。"""
    from app.services.storage_usage import collect
    return await collect(session, refresh=refresh)


@router.get("/doctor/report", response_class=PlainTextResponse)
async def system_doctor_report(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> str:
    """同一份診斷的純文字版 —— 給「下載記錄檔」用，可直接貼進工單。"""
    from app.services.self_check import run_checks
    return (await run_checks(session)).as_text()


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

    # 選用的作業系統相依：功能會隨版本新增，但既有主機不一定有對應的執行檔。
    # 在這裡露出來，管理員才不用等使用者回報「按了沒反應」才發現缺套件。
    from app.services.netdiag import tool_availability
    caps = tool_availability()
    # traceroute 是路徑追蹤的主力（ICMP；v0.5.185 起優先）、tracepath 只是退路——
    # 分開列，缺主力時管理員才知道該補哪個套件。
    info["host"]["optional_tools"] = {
        "ping": {"present": caps["ping"], "package": "iputils-ping",
                 "used_by": "Tools → IP addresses → ping"},
        "traceroute": {"present": caps["traceroute"], "package": "traceroute",
                       "used_by": "Tools → IP addresses → traceroute"},
        "tracepath": {"present": caps["tracepath"], "package": "iputils-tracepath",
                      "used_by": "traceroute fallback"},
    }
    # FreeRDP 引擎要的那幾個。列在這裡是因為這一頁正是管理員用來確認「這台裝了什麼」
    # 的地方 —— 選了 FreeRDP 卻連不上時，第一個該看的就是這裡，而不是去翻日誌。
    import shutil as _shutil

    from app.services.rdp_freerdp import binary_present, required_binaries
    # 預設引擎是 guacd：FreeRDP 那組套件（約 150 MB）只有選了這個引擎才裝（升級也只在選了時補）。
    # 沒選時標 engine_off：列出來但不進「缺少」警告（客戶 2026-10-08 在 Ubuntu 26.04 上被紅字嚇到）
    try:
        from app.core.db import SessionLocal as _SL
        from app.services.system_config import get_rdp_engine
        async with _SL() as _s:
            _freerdp_on = (await get_rdp_engine(_s)) == "freerdp"
    except SQLAlchemyError:
        _freerdp_on = False
    _rdp_used_by = {
        "xfreerdp": "RDP console (FreeRDP engine)",
        "Xvfb": "RDP console (FreeRDP engine) — virtual display",
        "ffmpeg": "RDP console (FreeRDP engine) — screen capture",
    }
    for exe, pkg in required_binaries().items():
        info["host"]["optional_tools"][exe] = {
            # xfreerdp＝RDP 用戶端，FreeRDP 2（xfreerdp）或 3（xfreerdp3）任一個都算
            "present": binary_present(exe),
            "package": pkg,
            "used_by": _rdp_used_by.get(exe, "RDP console (FreeRDP engine)"),
            "engine_off": not _freerdp_on,
        }
    # SNMP 收集（0217）：本機掃描代理（收集者「伺服器」）要 net-snmp 用戶端工具；install／upgrade 的
    # ensure_runtime_deps 會裝，裝不起來時這裡看得到。遠端代理的工具盤點在掃描代理頁
    info["host"]["optional_tools"]["snmpbulkwalk"] = {
        "present": _shutil.which("snmpbulkwalk") is not None and _shutil.which("snmpget") is not None,
        "package": "snmp",
        "used_by": "SNMP collection by the local scan agent (ARP / MAC table / LLDP / CDP / interfaces)",
    }
    # PDF 報告的中文字型（fonts-noto-cjk）：沒有的話 PDF 匯出會回 report_pdf_no_font
    from app.services.report_pdf import font_status
    _font = font_status()
    info["host"]["optional_tools"]["cjk_font"] = {
        "present": _font["present"],
        "package": "fonts-noto-cjk",
        "used_by": "PDF reports (IP change assessment): Chinese / Japanese text",
        "version": _font["name"],
    }
    info["host"]["optional_tools"]["xclip"] = {
        "present": _shutil.which("xclip") is not None,
        "package": "xclip",
        "used_by": "RDP console (FreeRDP engine) — clipboard paste",
        "engine_off": not _freerdp_on,
    }
    # aardwolf：以前是 RDP 的預設引擎、VNC 唯一的引擎；2026-09-27 起只是選用的備用引擎
    # （guacd 連不到時才用）。Python 3.14 上它會當掉（GitHub issue #42），缺了不影響服務。
    from app.api.v1.endpoints.rdp_console import RDP_AVAILABLE
    info["host"]["optional_tools"]["aardwolf"] = {
        "present": RDP_AVAILABLE,
        "package": "aardwolf (pip: .[rdp])",
        "used_by": "RDP / VNC console — fallback engine when guacd is down",
        # 備用：沒裝不發警告（Python 3.14 根本裝不起來，「upgrade 會補上」也不成立）
        "fallback": True,
    }
    info["host"]["required_tools"] = await _required_tools()
    # Recog 指紋庫（選用資料庫）：列進選用相依，沒裝時跟缺套件一樣會出現在警告裡
    try:
        from app.core.db import SessionLocal
        from app.services import recog
        async with SessionLocal() as s:
            info["recog"] = await recog.status(s)
        info["host"]["optional_tools"]["recog"] = {
            "present": info["recog"]["installed"],
            "package": "Recog fingerprint database (github.com/rapid7/recog)",
            "used_by": "IP probe — recognises devices / OS from banners, page titles and certificates",
            "version": info["recog"]["release"],
        }
    except SQLAlchemyError:
        info["recog"] = None
    # MAC 製造商資料庫（Wireshark manuf，每月排程更新）：跟 Recog 一樣是下載來的資料庫。
    # 表是空的時候 IP 清單、MAC 歷程、異常偵測的製造商欄全部空白，卻沒有地方講（2026-10-06 使用者）。
    try:
        from app.services import oui
        async with SessionLocal() as s:
            st = await oui.stats(s)
        info["host"]["optional_tools"]["oui"] = {
            "present": st["count"] > 0,
            "package": "MAC vendor database (Wireshark manuf)",
            "used_by": "MAC vendor names in IP lists, MAC history, anomaly detection and the IP probe",
            "version": st["last_updated"][:10] if st["last_updated"] else None,
        }
    except SQLAlchemyError:
        pass
    # GeoIP：本機 mmdb 優先、沒有就走 MaxMind web service；兩者都要管理員自己的帳號，
    # 沒設定是正常狀態 → opt_in：列出來但不進「缺少」警告
    try:
        from app.services import geoip
        dbs = geoip.local_databases()
        async with SessionLocal() as s:
            acct, key = await geoip.get_geoip_creds(s)
        if dbs:
            geo_ver: str | None = max(ts for _, ts in dbs).date().isoformat()
        else:
            geo_ver = "web service" if acct and key else None
        info["host"]["optional_tools"]["geoip"] = {
            "present": bool(dbs) or bool(acct and key),
            "package": "GeoIP database (MaxMind GeoLite2 / GeoIP2)",
            "used_by": "Tools → GeoIP and the AI GeoIP tool: country, city and ASN of public IPs",
            "version": geo_ver,
            "opt_in": True,
        }
    except SQLAlchemyError:
        pass
    return info


@router.get("/recog/status")
async def recog_status(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """Recog 指紋庫的狀態與每個指紋檔的筆數（Recog 頁；版本頁只列版本）。"""
    from sqlalchemy import func, select

    from app.models.recog import RecogDatabase
    from app.services import recog
    out = await recog.status(session)
    out["database_list"] = [
        {"key": k, "protocol": proto, "database_type": dtype, "fingerprints": int(n or 0)}
        for k, proto, dtype, n in (await session.execute(
            select(RecogDatabase.key, RecogDatabase.protocol, RecogDatabase.database_type,
                   func.jsonb_array_length(RecogDatabase.fingerprints)).order_by(RecogDatabase.key))).all()
    ]
    return out


@router.post("/recog/update")
async def update_recog_now(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """立即檢查 Recog 指紋庫有沒有新版，有就下載安裝（手動觸發；排程是每週的 jt-ipam-recog-refresh.timer）。"""
    from datetime import UTC, datetime

    from app.services import recog
    from app.services.background_tasks import record_finished_task
    started = datetime.now(UTC)
    result = await recog.check_and_update(session)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system", object_id=None, action="update",
        diff={"target": "recog_db_update", "result": {k: v for k, v in result.items() if k != "error"},
              "error": result.get("error")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await record_finished_task(
        session, kind="recog.refresh", ok=result.get("status") != "error", actor_user_id=user.id,
        started_at=started, error=result.get("error"),
        summary={k: result.get(k) for k in ("status", "release", "previous", "latest",
                                            "databases", "fingerprints", "skipped")})
    return {"result": result, "status": await recog.status(session)}


async def _required_tools() -> dict[str, dict[str, Any]]:
    """必要相依（缺了對應功能就不能正常運作）。

    guacd：RDP 與 VNC 的預設引擎，2026-09-27 起必裝（GitHub issue #42）。不是 pip 套件，
    是安裝腳本裝的 jt-ipam-guacd 服務 —— 版本頁要看得到「有沒有裝、有沒有在跑、哪個版本」。
    """
    from app.services import guacd as guac
    st = await guac.probe()
    ver = guac.installed_version()
    return {"guacd": {
        "present": ver is not None or bool(st["ok"]),
        "running": bool(st["ok"]),
        "version": ver,
        "protocols": st.get("protocols") or {},
        "address": st.get("address"),
        "error": st.get("error") or "",
        "package": "jt-ipam-guacd (jt-ipam.sh install / upgrade)",
        "used_by": "RDP / VNC console (default engine); SSH when selected",
    }}


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
        error = f"transport: {transport_detail(exc)}"

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
            error = f"transport: {transport_detail(exc)}"

    return {
        "current": __version__,
        "latest": latest,
        # 只有「數字序確實比現行新」才算有更新（修 0.4.79>0.4.199 的字串比較 bug）
        "update_available": bool(latest and _ver_tuple(latest) > _ver_tuple(__version__)),
        # 專案不建 GitHub release，/releases 是空頁 → 連去專案首頁
        "release_url": f"https://github.com/{_GITHUB_REPO}",
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
    # Telegram
    telegram_enabled: bool = False
    telegram_chat_id: str | None = None
    telegram_token_set: bool = False
    # Slack（Incoming Webhook URL）
    slack_enabled: bool = False
    slack_webhook_set: bool = False
    # Microsoft Teams（Incoming Webhook URL）
    teams_enabled: bool = False
    teams_webhook_set: bool = False
    # Nextcloud Talk（bot：對話 token + 密鑰）
    nextcloud_enabled: bool = False
    nextcloud_url: str | None = None
    nextcloud_token: str | None = None
    nextcloud_secret_set: bool = False
    # Zulip（bot email + API key → 串流/主題）
    zulip_enabled: bool = False
    zulip_site: str | None = None
    zulip_bot_email: str | None = None
    zulip_stream: str | None = None
    zulip_topic: str | None = None
    zulip_api_key_set: bool = False
    # 通用 webhook
    webhook_enabled: bool = False
    webhook_url_set: bool = False
    webhook_token_set: bool = False
    channels: list[dict[str, Any]] = []


class NotificationChannelsIn(StrictModel):
    email_enabled: bool | None = None
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_tls: str | None = None
    smtp_username: str | None = None
    smtp_from: str | None = None
    smtp_password: str | None = None     # 給非空才更新；"" 清除；不給保留
    # Telegram
    telegram_enabled: bool | None = None
    telegram_chat_id: str | None = None
    telegram_token: str | None = None
    # Slack
    slack_enabled: bool | None = None
    slack_webhook: str | None = None
    # Teams
    teams_enabled: bool | None = None
    teams_webhook: str | None = None
    # Nextcloud Talk
    nextcloud_enabled: bool | None = None
    nextcloud_url: str | None = None
    nextcloud_token: str | None = None
    nextcloud_secret: str | None = None
    # Zulip
    zulip_enabled: bool | None = None
    zulip_site: str | None = None
    zulip_bot_email: str | None = None
    zulip_stream: str | None = None
    zulip_topic: str | None = None
    zulip_api_key: str | None = None
    # 通用 webhook
    webhook_enabled: bool | None = None
    webhook_url: str | None = None
    webhook_token: str | None = None


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
        telegram_enabled=bool(cfg.get("telegram_enabled")),
        telegram_chat_id=cfg.get("telegram_chat_id"),
        telegram_token_set=bool(cfg.get("telegram_token_enc")),
        slack_enabled=bool(cfg.get("slack_enabled")),
        slack_webhook_set=bool(cfg.get("slack_webhook_enc")),
        teams_enabled=bool(cfg.get("teams_enabled")),
        teams_webhook_set=bool(cfg.get("teams_webhook_enc")),
        nextcloud_enabled=bool(cfg.get("nextcloud_enabled")),
        nextcloud_url=cfg.get("nextcloud_url"),
        nextcloud_token=cfg.get("nextcloud_token"),
        nextcloud_secret_set=bool(cfg.get("nextcloud_secret_enc")),
        zulip_enabled=bool(cfg.get("zulip_enabled")),
        zulip_site=cfg.get("zulip_site"),
        zulip_bot_email=cfg.get("zulip_bot_email"),
        zulip_stream=cfg.get("zulip_stream"),
        zulip_topic=cfg.get("zulip_topic"),
        zulip_api_key_set=bool(cfg.get("zulip_api_key_enc")),
        webhook_enabled=bool(cfg.get("webhook_enabled")),
        webhook_url_set=bool(cfg.get("webhook_url_enc")),
        webhook_token_set=bool(cfg.get("webhook_token_enc")),
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
    from app.services.system_config import (
        NOTIFY_EVENTS,
        NOTIFY_IM_CHANNELS,
        get_notification_matrix,
    )
    return {
        "matrix": await get_notification_matrix(session),
        "events": [e[0] for e in NOTIFY_EVENTS],
        "channels": list(NOTIFY_IM_CHANNELS),   # 通訊軟體欄（畫面只顯示已啟用的）
    }


@router.put("/notification-matrix")
async def put_notification_matrix_ep(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    from app.services.system_config import (
        NOTIFY_EVENTS,
        NOTIFY_IM_CHANNELS,
        set_notification_matrix,
    )
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
    return {"matrix": mx, "events": [e[0] for e in NOTIFY_EVENTS], "channels": list(NOTIFY_IM_CHANNELS)}


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


class TestChannelIn(StrictModel):
    channel: str


@router.post("/notification-channels/test-channel")
async def test_notification_channel(
    payload: TestChannelIn,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """用目前『已儲存』的設定，對指定 webhook 型管道送一則測試通知。"""
    from fastapi import HTTPException

    from app.services.notify_channels import WEBHOOK_CHANNELS, send_one
    from app.services.system_config import get_notification_channels
    if payload.channel not in WEBHOOK_CHANNELS:
        raise HTTPException(400, detail="unknown channel")
    cfg = await get_notification_channels(session)
    try:
        from app.services.notify_channels import make_rich
        subject, text = "jt-ipam 測試通知 / test notification", "這是一則來自 jt-ipam 的測試通知；若你收到，代表此管道設定正確。"
        # 聊天型管道用正式通知的版面（🧪、引用、資訊列、連結），按一下就能預覽之後通知長什麼樣子
        await send_one(cfg, payload.channel, subject, text,
                       rich=make_rich(subject, text, link="/notification-channels", severity="info", emoji="🧪"))
    except Exception as exc:
        raise HTTPException(502, detail=f"send failed: {str(exc)[:300]}") from exc
    return {"ok": True}


# ── 整合是否已設定（給側邊選單用）──────────────────────────────────
@view_router.get("/integration-presence")
async def integration_presence(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    """各整合是否至少設定了一台。

    「進階」選單裡的整合唯讀檢視頁（防火牆 / 虛擬化 / DNS 記錄 / 憑證派送），
    在該整合完全沒設定時只會顯示「尚未設定 X」，等於是空選項 —— 前端據此隱藏。
    管理選單的「外部系統整合」子樹也用它在已設定的整合名稱後面加圖示（使用者 2026-10-07）。
    只回布林值、不回任何實例內容，所以掛 global_read 就夠（無需 admin），
    否則具全域讀取的非 admin 會看不到自己有權限看的選單。
    """
    from sqlalchemy import select

    from app.models.adguard import AdGuardInstance
    from app.models.certificate import CertAgent
    from app.models.checkpoint import CheckPointServer
    from app.models.dhcp_standalone import IscDhcpServer, KeaDhcpServer
    from app.models.dns import DNSServer
    from app.models.esxi import ESXiInstance
    from app.models.firewall import OPNsenseFirewall
    from app.models.fortigate import FortiGateFirewall
    from app.models.hyperv import HyperVInstance
    from app.models.isoinsight import IsoInsightSource
    from app.models.librenms import LibreNMSInstance
    from app.models.mikrotik import MikroTikRouter
    from app.models.ocs import OcsServer
    from app.models.openstack import OpenStackInstance
    from app.models.paloalto import PaloAltoFirewall
    from app.models.pfsense import PfSenseFirewall
    from app.models.rustdesk import RustDeskServer
    from app.models.snmp import SnmpTarget
    from app.models.technitium import TechnitiumDhcpServer
    from app.models.virt import ProxmoxInstance
    from app.models.wazuh import WazuhInstance
    from app.models.windows_dhcp import WindowsDhcpServer
    from app.models.zabbix import ZabbixInstance

    out: dict[str, bool] = {}
    for key, model in (
        ("opnsense", OPNsenseFirewall),
        ("pfsense", PfSenseFirewall),
        ("fortigate", FortiGateFirewall),
        ("paloalto", PaloAltoFirewall),
        ("checkpoint", CheckPointServer),
        ("mikrotik", MikroTikRouter),
        ("dns", DNSServer),
        ("cert_agents", CertAgent),
        ("proxmox", ProxmoxInstance),
        ("esxi", ESXiInstance),
        ("hyperv", HyperVInstance),
        ("openstack", OpenStackInstance),
        ("adguard", AdGuardInstance),
        ("librenms", LibreNMSInstance),
        ("windows_dhcp", WindowsDhcpServer),
        ("kea_dhcp", KeaDhcpServer),
        ("isc_dhcp", IscDhcpServer),
        ("isoinsight", IsoInsightSource),
        ("technitium", TechnitiumDhcpServer),
        ("rustdesk", RustDeskServer),
        ("wazuh", WazuhInstance),
        ("zabbix", ZabbixInstance),
        ("ocs", OcsServer),
        # SNMP 收集（0217）：有收集目標才算已設定（只有憑證、沒有目標＝還沒開始收）
        ("snmp", SnmpTarget),
    ):
        # 只要知道有沒有：EXISTS 比 count 省（大站台的表可能很大）
        out[key] = bool(await session.scalar(select(select(model.id).limit(1).exists())))
    # Graylog DSV 查表不是一台台的整合，是一組設定：打開了就算已設定
    from app.services.system_config import get_graylog_dsv
    out["graylog"] = (await get_graylog_dsv(session))["enabled"]
    return out


# ─────────────────── 登入安全（MFA 政策）───────────────────
class AuthPolicyIO(StrictModel):
    #: off＝不要求（個人自選）／admins＝管理員必須／all＝所有人必須
    mfa_required: Literal["off", "admins", "all"] = "off"
    #: SSO（OIDC／SAML）登入也套用：預設不套用，交給身分提供者自己的 MFA
    mfa_apply_to_sso: bool = False


@router.get("/auth-policy", response_model=AuthPolicyIO)
async def get_auth_policy(session: Annotated[AsyncSession, Depends(get_session)]) -> AuthPolicyIO:
    from app.services.mfa import get_policy
    p = await get_policy(session)
    return AuthPolicyIO(mfa_required=p.mfa_required, mfa_apply_to_sso=p.mfa_apply_to_sso)  # type: ignore[arg-type]


@router.put("/auth-policy", response_model=AuthPolicyIO)
async def put_auth_policy(
    payload: AuthPolicyIO,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthPolicyIO:
    """改 MFA 政策。被要求但還沒設定的帳號不會被鎖在外面：下次登入時先完成 TOTP 設定。"""
    from app.services.mfa import AuthPolicy, get_policy, set_policy
    before = await get_policy(session)
    await set_policy(session, AuthPolicy(payload.mfa_required, payload.mfa_apply_to_sso), updated_by=user.id)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system_setting", object_id=None, action="update",
        diff={"setting": "auth_policy",
              "mfa_required": {"old": before.mfa_required, "new": payload.mfa_required},
              "mfa_apply_to_sso": {"old": before.mfa_apply_to_sso, "new": payload.mfa_apply_to_sso}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return payload


# ─────────────────── 每日備份加密 ───────────────────
class BackupEncryptionOut(StrictModel):
    enabled: bool
    set_at: str | None = None


class BackupEncryptionIn(StrictModel):
    #: 至少 12 個字元；忘記就解不開舊備份 —— 請存在這台主機以外的地方
    passphrase: Annotated[str, Field(min_length=12, max_length=256)]


@router.get("/backup-encryption", response_model=BackupEncryptionOut)
async def get_backup_encryption_ep(session: Annotated[AsyncSession, Depends(get_session)]) -> BackupEncryptionOut:
    from app.services.system_config import get_backup_encryption
    cfg = await get_backup_encryption(session)
    return BackupEncryptionOut(enabled=cfg["enabled"], set_at=cfg["set_at"])


async def _audit_backup_enc(session: AsyncSession, user: Any, request: Request, change: str) -> None:
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system_setting", object_id=None, action="update",
        diff={"setting": "backup_encryption", "change": change},     # 不記密碼本身
        request_id=getattr(request.state, "request_id", None),
    )


@router.put("/backup-encryption", response_model=BackupEncryptionOut)
async def put_backup_encryption(
    payload: BackupEncryptionIn, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BackupEncryptionOut:
    """設定或更換備份加密密碼。之後的每日備份都會加密；舊備份仍要用當時的密碼解。"""
    from app.services.system_config import get_backup_encryption, set_backup_encryption
    had = (await get_backup_encryption(session))["enabled"]
    await set_backup_encryption(session, payload.passphrase, updated_by=user.id)
    await _audit_backup_enc(session, user, request, "changed" if had else "set")
    await session.commit()
    cfg = await get_backup_encryption(session)
    return BackupEncryptionOut(enabled=cfg["enabled"], set_at=cfg["set_at"])


@router.delete("/backup-encryption", status_code=204)
async def delete_backup_encryption(
    user: CurrentUser, request: Request, session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """移除備份加密密碼（之後的備份回到明文目錄；系統診斷會提醒）。"""
    from app.services.system_config import set_backup_encryption
    await set_backup_encryption(session, None, updated_by=user.id)
    await _audit_backup_enc(session, user, request, "removed")
    await session.commit()
