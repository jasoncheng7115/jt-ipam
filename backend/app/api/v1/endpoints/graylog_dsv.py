"""Graylog DSV 查表（lookup table）整合。

對外提供一個 token 保護的 DSV 端點，Graylog 用「DSV File from HTTP」資料配接器
定時抓取，key=IP、value=主機名稱 / FQDN。功能預設關閉，於管理區開啟並設定路徑。

  GET /api/v1/lookup/{name}?token=<token>   →  text/csv 或 text/tab-separated-values
       192.0.2.10,host-a.example.com
       192.0.2.11,host-b.example.com
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.address import IPAddress
from app.models.firewall import OPNsenseFirewall, OPNsenseRuleLabel, OPNsenseSyncedAlias
from app.models.pfsense import PfSenseFirewall, PfSenseSyncedAlias
from app.models.virt import VirtCluster, VirtualMachine
from app.schemas.base import StrictModel
from app.services.system_config import get_graylog_dsv, set_graylog_dsv

# 單一 alias→成員 DSV 每列成員數上限（crowdsec 類 alias 可達數萬筆，避免單格爆量）
_ALIAS_MEMBER_CAP = 1000


def _dsv_lines(pairs: list[tuple[str, str]], fmt: str) -> str:
    """把 (key, value) 串成 CSV（RFC 4180 雙引號跳脫）或 TSV（不加引號）。key 去重保留第一筆。"""
    is_csv = fmt != "tsv"
    sep = "\t" if not is_csv else ","
    lines: list[str] = []
    seen: set[str] = set()
    for k, v in pairs:
        k = (k or "").strip()
        v = (v or "").strip()
        if not k or k in seen:
            continue
        if "\n" in k or "\r" in k or "\n" in v or "\r" in v:
            continue
        seen.add(k)
        if is_csv:
            qk = '"' + k.replace('"', '""') + '"'
            qv = '"' + v.replace('"', '""') + '"'
            lines.append(f"{qk}{sep}{qv}")
        else:
            if sep in k or sep in v or '"' in k or '"' in v:
                continue
            lines.append(f"{k}{sep}{v}")
    media = "text/tab-separated-values" if fmt == "tsv" else "text/csv"
    return media, "\n".join(lines) + ("\n" if lines else "")


async def _fw_dsv_guard(
    firewall_id: uuid.UUID, token: str, session: AsyncSession,
) -> tuple[OPNsenseFirewall, dict[str, Any]]:
    """防火牆 DSV 共用守門：token（沿用 graylog_dsv）+ 該防火牆 expose_dsv。"""
    cfg = await get_graylog_dsv(session)
    if not cfg["token"] or token != cfg["token"]:
        raise HTTPException(status_code=401, detail="Invalid token")
    fw = await session.get(OPNsenseFirewall, firewall_id)
    if fw is None or not fw.expose_dsv:
        raise HTTPException(status_code=404, detail="Not found")
    return fw, cfg

# 公開（token 保護）— 不掛使用者驗證，給 Graylog 機器抓取
public_router = APIRouter(prefix="/lookup", tags=["lookup"])
# 管理區設定 — admin only
admin_router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_admin)])


@public_router.get("/{name}")
async def dsv_lookup(
    name: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    token: str = Query("", description="存取權杖"),
) -> PlainTextResponse:
    cfg = await get_graylog_dsv(session)
    if not cfg["enabled"] or name != cfg["path"]:
        raise HTTPException(status_code=404, detail="Not found")
    if not cfg["token"] or token != cfg["token"]:
        raise HTTPException(status_code=401, detail="Invalid token")
    sep = "\t" if cfg["fmt"] == "tsv" else ","
    rows = (await session.execute(
        select(func.host(IPAddress.ip), IPAddress.hostname)
        .where(IPAddress.hostname.is_not(None), IPAddress.hostname != "")
        .order_by(IPAddress.ip)
    )).all()
    is_csv = cfg["fmt"] != "tsv"
    lines: list[str] = []
    seen: set[str] = set()   # 同一 IP 可能存在於多個（重疊）子網路；Graylog DSV 的 key 必須唯一 → 每個 IP 只輸出一次
    for ip, host in rows:
        if not ip or not host:
            continue
        ip_s = str(ip)
        if ip_s in seen:
            continue
        seen.add(ip_s)
        h = str(host).strip()
        if "\n" in h or "\r" in h:  # 換行無法安全表達，跳過
            continue
        if is_csv:
            # CSV：每欄都用雙引號包起來，內含的 " 以 "" 跳脫（RFC 4180）
            qip = '"' + ip_s.replace('"', '""') + '"'
            qh = '"' + h.replace('"', '""') + '"'
            lines.append(f"{qip}{sep}{qh}")
        else:
            if sep in h or '"' in h:  # TSV 不加引號：含分隔符就跳過
                continue
            lines.append(f"{ip_s}{sep}{h}")
    media = "text/tab-separated-values" if cfg["fmt"] == "tsv" else "text/csv"
    return PlainTextResponse("\n".join(lines) + ("\n" if lines else ""),
                             media_type=f"{media}; charset=utf-8")


@public_router.get("/firewall/{firewall_id}/rule-aliases")
async def fw_rule_aliases_dsv(
    firewall_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    token: str = Query("", description="存取權杖（沿用 Graylog DSV token）"),
) -> PlainTextResponse:
    """防火牆規則 DSV：key = filterlog rid（pf 規則 label），value = 引用的 alias 名。"""
    fw, cfg = await _fw_dsv_guard(firewall_id, token, session)
    rows = (await session.execute(
        select(OPNsenseRuleLabel.label, OPNsenseRuleLabel.alias_names)
        .where(OPNsenseRuleLabel.firewall_id == fw.id)
        .order_by(OPNsenseRuleLabel.label)
    )).all()
    pairs = [
        (label, " ".join(aliases or []))
        for label, aliases in rows if aliases
    ]
    media, body = _dsv_lines(pairs, cfg["fmt"])
    return PlainTextResponse(body, media_type=f"{media}; charset=utf-8")


@public_router.get("/firewall/{firewall_id}/aliases")
async def fw_aliases_dsv(
    firewall_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    token: str = Query("", description="存取權杖（沿用 Graylog DSV token）"),
) -> PlainTextResponse:
    """別名 DSV：key = alias 名，value = 成員清單（空白分隔，超量截斷）。"""
    fw, cfg = await _fw_dsv_guard(firewall_id, token, session)
    rows = (await session.execute(
        select(OPNsenseSyncedAlias.name, OPNsenseSyncedAlias.content)
        .where(OPNsenseSyncedAlias.firewall_id == fw.id)
        .order_by(OPNsenseSyncedAlias.name)
    )).all()
    pairs: list[tuple[str, str]] = []
    for name, content in rows:
        members = [str(m) for m in (content or []) if m]
        val = " ".join(members[:_ALIAS_MEMBER_CAP])
        if len(members) > _ALIAS_MEMBER_CAP:
            val += f" …(+{len(members) - _ALIAS_MEMBER_CAP})"
        pairs.append((name, val))
    media, body = _dsv_lines(pairs, cfg["fmt"])
    return PlainTextResponse(body, media_type=f"{media}; charset=utf-8")


# ─────────────────── pfSense DSV（與 OPNsense 平行，獨立路徑）───────────────────
async def _pfsense_dsv_guard(
    firewall_id: uuid.UUID, token: str, session: AsyncSession,
) -> tuple[PfSenseFirewall, dict[str, Any]]:
    cfg = await get_graylog_dsv(session)
    if not cfg["token"] or token != cfg["token"]:
        raise HTTPException(status_code=401, detail="Invalid token")
    fw = await session.get(PfSenseFirewall, firewall_id)
    if fw is None or not fw.expose_dsv:
        raise HTTPException(status_code=404, detail="Not found")
    return fw, cfg


@public_router.get("/pfsense/{firewall_id}/aliases")
async def pfsense_aliases_dsv(
    firewall_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    token: str = Query("", description="存取權杖（沿用 Graylog DSV token）"),
) -> PlainTextResponse:
    """pfSense 別名 DSV：key = alias 名，value = 成員清單（空白分隔，超量截斷）。"""
    fw, cfg = await _pfsense_dsv_guard(firewall_id, token, session)
    rows = (await session.execute(
        select(PfSenseSyncedAlias.name, PfSenseSyncedAlias.members)
        .where(PfSenseSyncedAlias.firewall_id == fw.id)
        .order_by(PfSenseSyncedAlias.name)
    )).all()
    pairs: list[tuple[str, str]] = []
    for name, members in rows:
        ms = [str(m) for m in (members or []) if m]
        val = " ".join(ms[:_ALIAS_MEMBER_CAP])
        if len(ms) > _ALIAS_MEMBER_CAP:
            val += f" …(+{len(ms) - _ALIAS_MEMBER_CAP})"
        pairs.append((name, val))
    media, body = _dsv_lines(pairs, cfg["fmt"])
    return PlainTextResponse(body, media_type=f"{media}; charset=utf-8")


@public_router.get("/pfsense/{firewall_id}/rules")
async def pfsense_rules_dsv(
    firewall_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    token: str = Query("", description="存取權杖（沿用 Graylog DSV token）"),
) -> PlainTextResponse:
    """pfSense 規則 DSV：key = filterlog tracker（規則追蹤 ID），value = 規則說明。"""
    fw, cfg = await _pfsense_dsv_guard(firewall_id, token, session)
    pairs: list[tuple[str, str]] = []
    for r in (fw.rules or []):
        if not isinstance(r, dict):
            continue
        tracker = r.get("tracker")
        if tracker in (None, "", 0):
            continue
        descr = (r.get("descr") or "").strip()
        if not descr:
            act = r.get("type") or ""
            iface = r.get("interface") or ""
            descr = f"{act} {iface}".strip() or str(tracker)
        pairs.append((str(tracker), descr))
    media, body = _dsv_lines(pairs, cfg["fmt"])
    return PlainTextResponse(body, media_type=f"{media}; charset=utf-8")


async def _proxmox_vms_pairs(
    session: AsyncSession, token: str, cluster_id: uuid.UUID | None,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """共用：token 守門 + 撈 vmid→名稱。cluster_id=None 表示全部叢集（去重）。"""
    cfg = await get_graylog_dsv(session)
    if not cfg["token"] or token != cfg["token"]:
        raise HTTPException(status_code=401, detail="Invalid token")
    stmt = (
        select(VirtualMachine.legacy_vmid, VirtualMachine.name)
        .where(VirtualMachine.legacy_vmid.is_not(None))
        .order_by(VirtualMachine.legacy_vmid)
    )
    if cluster_id is not None:
        stmt = stmt.where(VirtualMachine.cluster_id == cluster_id)
    rows = (await session.execute(stmt)).all()
    return [(str(vmid), name or "") for vmid, name in rows], cfg


@public_router.get("/proxmox/vms")
async def proxmox_vms_dsv(
    session: Annotated[AsyncSession, Depends(get_session)],
    token: str = Query("", description="存取權杖（沿用 Graylog DSV token）"),
) -> PlainTextResponse:
    """全部 PVE 叢集的 VM DSV：key = vmid，value = VM 名稱（沿用 graylog_dsv token）。

    多叢集時 vmid 會跨叢集重複 → _dsv_lines 去重只保留第一筆；要正確區分請改用每叢集端點
    `/proxmox/{cluster_id}/vms`（比照 OPNsense 多防火牆）。路徑兩段，不撞 dsv_lookup 的單段 `/{name}`。
    """
    pairs, cfg = await _proxmox_vms_pairs(session, token, None)
    media, body = _dsv_lines(pairs, cfg["fmt"])
    return PlainTextResponse(body, media_type=f"{media}; charset=utf-8")


@public_router.get("/proxmox/{cluster_id}/vms")
async def proxmox_cluster_vms_dsv(
    cluster_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    token: str = Query("", description="存取權杖（沿用 Graylog DSV token）"),
) -> PlainTextResponse:
    """單一 PVE 叢集 / 獨立節點的 VM DSV：key = vmid，value = VM 名稱。

    多個 cluster / standalone node 時 vmid 會跨叢集重複，每個叢集各自一個 DSV 才不會混淆。
    """
    cluster = await session.get(VirtCluster, cluster_id)
    if cluster is None:
        # token 仍要先驗（避免用此端點探測叢集是否存在）
        await _proxmox_vms_pairs(session, token, cluster_id)
        raise HTTPException(status_code=404, detail="Not found")
    pairs, cfg = await _proxmox_vms_pairs(session, token, cluster_id)
    media, body = _dsv_lines(pairs, cfg["fmt"])
    return PlainTextResponse(body, media_type=f"{media}; charset=utf-8")


class GraylogDsvOut(StrictModel):
    enabled: bool
    fmt: str
    path: str
    token: str


class GraylogDsvPatch(StrictModel):
    enabled: bool
    fmt: str = "csv"
    path: str = "ip-fqdn"
    regenerate_token: bool = False


@admin_router.get("/graylog-dsv", response_model=GraylogDsvOut)
async def get_graylog_dsv_ep(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    return await get_graylog_dsv(session)


@admin_router.put("/graylog-dsv", response_model=GraylogDsvOut)
async def put_graylog_dsv_ep(
    payload: GraylogDsvPatch,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    out = await set_graylog_dsv(
        session, enabled=payload.enabled, fmt=payload.fmt, path=payload.path,
        regenerate_token=payload.regenerate_token,
        updated_by_user_id=uuid.UUID(str(user.id)),
    )
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system_setting", object_id=None, action="update",
        diff={"setting": "graylog_dsv", "enabled": out["enabled"], "fmt": out["fmt"],
              "path": out["path"]},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return out
