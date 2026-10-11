"""Audit log read endpoint（admin only）+ chain verify。

寫入由各 service 透過 app.core.audit.append_audit；這裡只提供 admin 查看 + 鏈完整性 verify。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_admin
from app.core.audit import verify_chain
from app.core.db import get_session
from app.models.audit import AuditLog
from app.schemas.base import Paginated, StrictModel

router = APIRouter(
    prefix="/audit",
    tags=["audit"],
    dependencies=[Depends(require_admin)],
)


class AuditLogRead(StrictModel):
    id: int
    ts: datetime
    actor_user_id: uuid.UUID | None
    actor_name: str | None
    actor_ip: str | None
    actor_user_agent: str | None
    object_type: str
    object_id: uuid.UUID | None
    object_label: str | None
    action: str
    diff: dict[str, Any] | None
    request_id: uuid.UUID | None
    prev_hash_hex: str
    this_hash_hex: str

    @classmethod
    def from_orm_row(
        cls,
        row: AuditLog,
        actor_name: str | None = None,
        object_label: str | None = None,
    ) -> AuditLogRead:
        return cls(
            id=row.id,
            ts=row.ts,
            actor_user_id=row.actor_user_id,
            actor_name=actor_name,
            actor_ip=str(row.actor_ip) if row.actor_ip else None,
            actor_user_agent=row.actor_user_agent,
            object_type=row.object_type,
            object_id=row.object_id,
            object_label=object_label,
            action=row.action,
            diff=row.diff,
            request_id=row.request_id,
            prev_hash_hex=row.prev_hash.hex(),
            this_hash_hex=row.this_hash.hex(),
        )


# object_type → (模型 import 路徑, 名稱欄位)；用來把 object_id 解析成可讀標籤。
# 找不到對應或查無資料時，前端會退回顯示縮短的 UUID。
_LABEL_REGISTRY: dict[str, tuple[str, str, str]] = {
    "device": ("app.models.device", "Device", "name"),
    "subnet": ("app.models.subnet", "Subnet", "cidr"),
    "section": ("app.models.section", "Section", "name"),
    "ip": ("app.models.address", "IPAddress", "ip"),
    "ip_address": ("app.models.address", "IPAddress", "ip"),
    "vlan": ("app.models.vlan", "VLAN", "name"),
    "vrf": ("app.models.vrf", "VRF", "name"),
    "location": ("app.models.location", "Location", "name"),
    "rack": ("app.models.location", "Rack", "name"),
    "customer": ("app.models.customer", "Customer", "name"),
    "change_plan": ("app.models.change_impact", "ChangePlan", "title"),
    "impact_service": ("app.models.change_impact", "ImpactService", "name"),
    "ssh_credential": ("app.models.ssh_credential", "SSHCredential", "label"),
    "rdp_credential": ("app.models.ssh_credential", "SSHCredential", "label"),
    "vnc_credential": ("app.models.ssh_credential", "SSHCredential", "label"),
    "pve_credential": ("app.models.ssh_credential", "SSHCredential", "label"),
    "rustdesk_credential": ("app.models.ssh_credential", "SSHCredential", "label"),
    # ── 整合實例 ──
    # 沒有這些的話，稽核的「目標」欄只會顯示截斷 UUID，多台同型整合時完全分不出
    # 是哪一台在同步（客戶實測 FortiGate 時發現）。
    "fortigate_firewall": ("app.models.fortigate", "FortiGateFirewall", "name"),
    "paloalto_firewall": ("app.models.paloalto", "PaloAltoFirewall", "name"),
    "checkpoint_server": ("app.models.checkpoint", "CheckPointServer", "name"),
    "checkpoint_gaia_target": ("app.models.checkpoint_gaia", "CheckPointGaiaTarget", "name"),
    "mikrotik_router": ("app.models.mikrotik", "MikroTikRouter", "name"),
    "pfsense_firewall": ("app.models.pfsense", "PfSenseFirewall", "name"),
    "opnsense_firewall": ("app.models.firewall", "OPNsenseFirewall", "name"),
    "librenms_instance": ("app.models.librenms", "LibreNMSInstance", "name"),
    "wazuh_instance": ("app.models.wazuh", "WazuhInstance", "name"),
    "adguard_instance": ("app.models.adguard", "AdGuardInstance", "name"),
    "windows_dhcp_server": ("app.models.windows_dhcp", "WindowsDhcpServer", "name"),
    "kea_dhcp_server": ("app.models.dhcp_standalone", "KeaDhcpServer", "name"),
    "technitium_dhcp_server": ("app.models.technitium", "TechnitiumDhcpServer", "name"),
    "isc_dhcp_server": ("app.models.dhcp_standalone", "IscDhcpServer", "name"),
    "isoinsight_source": ("app.models.isoinsight", "IsoInsightSource", "name"),
    "rustdesk_server": ("app.models.rustdesk", "RustDeskServer", "name"),
    "proxmox_instance": ("app.models.virt", "ProxmoxInstance", "api_url"),
    "virt_cluster": ("app.models.virt", "VirtCluster", "name"),
    "dns_server": ("app.models.dns", "DNSServer", "name"),
    "dns_compare_group": ("app.models.dns_compare_group", "DNSCompareGroup", "name"),
    # ── 代理 / 憑證 / 權杖 ──
    "scan_agent": ("app.models.scan_agent", "ScanAgent", "name"),
    "cert_agent": ("app.models.certificate", "CertAgent", "name"),
    "certificate": ("app.models.certificate", "Certificate", "name"),
    "api_token": ("app.models.user", "APIToken", "name"),
    "webhook": ("app.models.notification", "WebhookSubscription", "name"),
    # ── 其他常見物件 ──
    "group": ("app.models.user", "Group", "name"),
    "nat": ("app.models.nat", "NATTranslation", "name"),
    "tenant": ("app.models.advanced", "Tenant", "name"),
    "provider": ("app.models.advanced", "Provider", "name"),
    "contact": ("app.models.advanced", "Contact", "name"),
    "circuit": ("app.models.advanced", "Circuit", "cid"),
    "asn": ("app.models.advanced", "ASN", "asn"),
    "vlan_domain": ("app.models.vlan", "VLANDomain", "name"),
    "vpn_tunnel": ("app.models.physical", "VPNTunnel", "name"),
    "wireless_ssid": ("app.models.advanced", "WirelessSSID", "ssid"),
    "custom_field": ("app.models.custom_field", "CustomFieldDefinition", "name"),
}


async def _resolve_labels(
    session: AsyncSession, rows: list[AuditLog]
) -> tuple[dict[uuid.UUID, str], dict[tuple[str, uuid.UUID], str]]:
    """批次把 actor_user_id → 使用者名稱、(object_type, object_id) → 實體標籤解析出來。"""
    import importlib

    # ── actor 名稱（含 auth/user 物件本身就是使用者 id）──
    from app.models.user import User

    user_ids: set[uuid.UUID] = set()
    for r in rows:
        if r.actor_user_id:
            user_ids.add(r.actor_user_id)
        if r.object_type in ("user", "auth") and r.object_id:
            user_ids.add(r.object_id)
    actor_names: dict[uuid.UUID, str] = {}
    if user_ids:
        urows = (
            await session.execute(
                select(User.id, User.username, User.display_name).where(User.id.in_(user_ids))  # bounded: users on one page
            )
        ).all()
        for uid, uname, dname in urows:
            actor_names[uid] = dname or uname

    # ── object 標籤（依 object_type 分組批次查）──
    labels: dict[tuple[str, uuid.UUID], str] = {}
    by_type: dict[str, set[uuid.UUID]] = {}
    for r in rows:
        if r.object_id is None:
            continue
        if r.object_type in ("user", "auth"):
            if r.object_id in actor_names:
                labels[(r.object_type, r.object_id)] = actor_names[r.object_id]
            continue
        if r.object_type in _LABEL_REGISTRY:
            by_type.setdefault(r.object_type, set()).add(r.object_id)

    for otype, ids in by_type.items():
        module_path, cls_name, attr = _LABEL_REGISTRY[otype]
        try:
            model = getattr(importlib.import_module(module_path), cls_name)
            col = getattr(model, attr)
            qrows = (
                await session.execute(select(model.id, col).where(model.id.in_(ids)))  # bounded: objects on one page
            ).all()
            for oid, val in qrows:
                if val is not None:
                    labels[(otype, oid)] = str(val)
        except Exception:  # noqa: S112 — 任一型別解析失敗都不該讓稽核頁壞掉（稽核頁穩定優先）
            continue

    return actor_names, labels


class ChainVerifyResult(BaseModel):
    ok: bool
    broken_at_id: int | None
    checked: int


@router.get("/actions")
async def list_audit_actions(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, Any]]:
    """稽核記錄裡**實際出現過**的動作與筆數。

    不寫死清單：動作會隨功能增加（光是這一版就多了 `group_member_add`、
    `cert_agent_key_rotate`…），寫死的清單一定會過期，而且過期的方式很難察覺 ——
    使用者只會覺得「篩選裡沒有這個動作」而不知道是清單沒更新。
    """
    rows = (await session.execute(
        select(AuditLog.action, func.count().label("n"))
        .group_by(AuditLog.action)
        .order_by(func.count().desc())
    )).all()
    return [{"action": a, "count": int(n)} for a, n in rows if a]


@router.get("", response_model=Paginated[AuditLogRead])
async def list_audit(
    session: Annotated[AsyncSession, Depends(get_session)],
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    # 可複選：`?action=create&action=delete`。單一值仍然照舊可用（FastAPI 會收成一元素清單）
    action: Annotated[list[str] | None, Query()] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    base = select(AuditLog)
    if object_type:
        base = base.where(AuditLog.object_type == object_type)
    if object_id is not None:
        base = base.where(AuditLog.object_id == object_id)
    if actor_user_id is not None:
        base = base.where(AuditLog.actor_user_id == actor_user_id)
    if action:
        picked = [a for a in action if a]
        if picked:
            base = base.where(AuditLog.action.in_(picked))  # bounded: picked actions
    if since is not None:
        base = base.where(AuditLog.ts >= since)
    if until is not None:
        base = base.where(AuditLog.ts <= until)

    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                base.order_by(desc(AuditLog.id)).offset(offset).limit(limit)
            )
        ).scalars().all()
    )
    actor_names, labels = await _resolve_labels(session, rows)
    return {
        "items": [
            AuditLogRead.from_orm_row(
                r,
                actor_name=actor_names.get(r.actor_user_id) if r.actor_user_id else None,
                object_label=(
                    labels.get((r.object_type, r.object_id)) if r.object_id else None
                ),
            )
            for r in rows
        ],
        "total": total,
        "page": offset // limit + 1,
        "page_size": limit,
    }


@router.post("/verify", response_model=ChainVerifyResult)
async def verify_audit_chain(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int | None, Query(ge=1, le=1_000_000)] = None,
) -> Any:
    ok, broken = await verify_chain(session, limit=limit)
    # checked = total rows we walked
    if limit is None:
        checked_q = (
            await session.execute(select(func.count()).select_from(AuditLog))
        ).scalar_one()
    else:
        checked_q = limit
    return ChainVerifyResult(ok=ok, broken_at_id=broken, checked=int(checked_q))
