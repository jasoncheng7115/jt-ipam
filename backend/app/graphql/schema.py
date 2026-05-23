"""GraphQL schema：read-only 巢狀查詢。

設計取捨：
- 寫入仍走 `/api/v1/`（已成熟、已稽核），GraphQL 專注讀取與聚合
- subscription 留給 Phase 4
- 認證沿用 REST 的 get_current_user dependency；context 提供 user / session
- 每個 endpoint 都會 RBAC 過濾（與 REST 行為一致）

OWASP A01：透過 filter_visible 把 user 沒權限的 Section/Subnet 過濾掉
OWASP A06：每個 list 都有 limit 上限避免 DoS
"""

from __future__ import annotations

import uuid
from typing import Annotated

import strawberry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strawberry.fastapi import GraphQLRouter

from app.api.v1.dependencies import get_current_user
from app.core.db import get_session
from app.graphql import types as gqltypes
from app.models.address import IPAddress as IPAddressModel
from app.models.device import Device as DeviceModel
from app.models.librenms import ARPEntry, FDBEntry
from app.models.section import Section as SectionModel
from app.models.subnet import Subnet as SubnetModel
from app.models.user import User
from app.models.vlan import VLAN as VLANModel
from app.services.permission import (
    filter_visible,
    get_object_permission,
    has_permission,
)
from app.services.subnet import get_usage


@strawberry.type
class Query:
    @strawberry.field(description="Current authenticated user (auth required).")
    async def me(self, info: strawberry.Info) -> str:
        user: User = info.context["user"]
        return user.username

    @strawberry.field(description="List sections visible to the caller.")
    async def sections(
        self, info: strawberry.Info, limit: int = 100,
    ) -> list[gqltypes.Section]:
        if limit > 500:
            limit = 500
        session: AsyncSession = info.context["session"]
        user: User = info.context["user"]
        rows = list(
            (
                await session.execute(
                    select(SectionModel)
                    .order_by(SectionModel.display_order, SectionModel.name)
                    .limit(limit)
                )
            ).scalars().all()
        )
        visible = set(
            await filter_visible(
                session, user=user, object_type="section",
                object_ids=[r.id for r in rows], required="read",
            )
        )
        return [gqltypes.Section(**_dict_section(r)) for r in rows if r.id in visible]

    @strawberry.field(description="Single subnet (404 if invisible).")
    async def subnet(
        self, info: strawberry.Info, id: uuid.UUID,
    ) -> gqltypes.Subnet | None:
        session: AsyncSession = info.context["session"]
        user: User = info.context["user"]
        s = await session.get(SubnetModel, id)
        if s is None:
            return None
        level = await get_object_permission(
            session, user=user, object_type="subnet", object_id=s.id
        )
        if not has_permission(level, "read"):
            return None
        return gqltypes.Subnet(**_dict_subnet(s))

    @strawberry.field(description="List subnets visible to the caller.")
    async def subnets(
        self,
        info: strawberry.Info,
        section_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[gqltypes.Subnet]:
        if limit > 500:
            limit = 500
        session: AsyncSession = info.context["session"]
        user: User = info.context["user"]
        stmt = select(SubnetModel)
        if section_id is not None:
            stmt = stmt.where(SubnetModel.section_id == section_id)
        stmt = stmt.order_by(SubnetModel.cidr).limit(limit)
        rows = list((await session.execute(stmt)).scalars().all())
        visible = set(
            await filter_visible(
                session, user=user, object_type="subnet",
                object_ids=[r.id for r in rows], required="read",
            )
        )
        return [gqltypes.Subnet(**_dict_subnet(r)) for r in rows if r.id in visible]

    @strawberry.field(description="Subnet usage (total/used/free/pct).")
    async def subnet_usage(
        self, info: strawberry.Info, id: uuid.UUID,
    ) -> gqltypes.SubnetUsage | None:
        session: AsyncSession = info.context["session"]
        user: User = info.context["user"]
        s = await session.get(SubnetModel, id)
        if s is None:
            return None
        level = await get_object_permission(
            session, user=user, object_type="subnet", object_id=s.id
        )
        if not has_permission(level, "read"):
            return None
        total, used, free, pct = await get_usage(session, s)
        return gqltypes.SubnetUsage(total=total, used=used, free=free, used_pct=pct)

    @strawberry.field(description="IP addresses inside a subnet (RBAC-filtered).")
    async def addresses(
        self,
        info: strawberry.Info,
        subnet_id: uuid.UUID,
        limit: int = 200,
    ) -> list[gqltypes.IPAddress]:
        if limit > 1000:
            limit = 1000
        session: AsyncSession = info.context["session"]
        user: User = info.context["user"]
        level = await get_object_permission(
            session, user=user, object_type="subnet", object_id=subnet_id
        )
        if not has_permission(level, "read"):
            return []
        rows = list(
            (
                await session.execute(
                    select(IPAddressModel)
                    .where(IPAddressModel.subnet_id == subnet_id)
                    .order_by(IPAddressModel.ip)
                    .limit(limit)
                )
            ).scalars().all()
        )
        return [gqltypes.IPAddress(**_dict_ip(r)) for r in rows]

    @strawberry.field(description="VLANs (auth required).")
    async def vlans(
        self,
        info: strawberry.Info,
        domain_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> list[gqltypes.VLAN]:
        if limit > 1000:
            limit = 1000
        session: AsyncSession = info.context["session"]
        stmt = select(VLANModel)
        if domain_id is not None:
            stmt = stmt.where(VLANModel.domain_id == domain_id)
        stmt = stmt.order_by(VLANModel.number).limit(limit)
        rows = list((await session.execute(stmt)).scalars().all())
        return [
            gqltypes.VLAN(
                id=r.id, domain_id=r.domain_id, number=r.number,
                name=r.name, description=r.description,
            )
            for r in rows
        ]

    @strawberry.field(description="Devices (auth required).")
    async def devices(
        self,
        info: strawberry.Info,
        type: str | None = None,
        limit: int = 200,
    ) -> list[gqltypes.Device]:
        if limit > 1000:
            limit = 1000
        session: AsyncSession = info.context["session"]
        stmt = select(DeviceModel)
        if type is not None:
            stmt = stmt.where(DeviceModel.type == type)
        stmt = stmt.order_by(DeviceModel.name).limit(limit)
        rows = list((await session.execute(stmt)).scalars().all())
        return [gqltypes.Device(**_dict_device(r)) for r in rows]

    @strawberry.field(
        description="ARP-derived IP→MAC→Switch+Port lookup (LibreNMS Phase 2)."
    )
    async def trace_ip(
        self, info: strawberry.Info, ip: str,
    ) -> gqltypes.ARPLookup:
        session: AsyncSession = info.context["session"]
        arp = (
            await session.execute(
                select(ARPEntry)
                .where(ARPEntry.ip == ip)
                .order_by(ARPEntry.last_seen_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if arp is None:
            return gqltypes.ARPLookup(
                ip=ip, mac=None, interface=None,
                switch_device_id=None, switch_port=None, vlan=None,
            )
        fdb = (
            await session.execute(
                select(FDBEntry)
                .where(FDBEntry.mac == arp.mac)
                .order_by(FDBEntry.last_seen_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return gqltypes.ARPLookup(
            ip=ip,
            mac=arp.mac,
            interface=arp.interface,
            switch_device_id=fdb.device_id if fdb else None,
            switch_port=fdb.port_name if fdb else None,
            vlan=fdb.vlan_id_num if fdb else None,
        )


# ─────────────────── helpers ───────────────────


def _dict_section(r: SectionModel) -> dict:  # type: ignore[type-arg]
    return {
        "id": r.id, "name": r.name, "description": r.description,
        "parent_id": r.parent_id, "strict_mode": r.strict_mode,
        "display_order": r.display_order,
        "created_at": r.created_at, "updated_at": r.updated_at,
    }


def _dict_subnet(r: SubnetModel) -> dict:  # type: ignore[type-arg]
    return {
        "id": r.id, "section_id": r.section_id,
        "master_subnet_id": r.master_subnet_id,
        "cidr": str(r.cidr), "description": r.description,
        "vlan_id": r.vlan_id, "vrf_id": r.vrf_id,
        "is_pool": r.is_pool, "is_full": r.is_full,
        "scan_enabled": r.scan_enabled, "auto_dns": r.auto_dns,
        "created_at": r.created_at, "updated_at": r.updated_at,
    }


def _dict_ip(r: IPAddressModel) -> dict:  # type: ignore[type-arg]
    return {
        "id": r.id, "subnet_id": r.subnet_id, "ip": str(r.ip).split("/")[0],
        "hostname": r.hostname, "description": r.description,
        "state": r.state, "mac": str(r.mac) if r.mac else None,
        "owner": r.owner, "discovery_source": r.discovery_source,
        "effective_status": r.effective_status,
        "created_at": r.created_at, "updated_at": r.updated_at,
    }


def _dict_device(r: DeviceModel) -> dict:  # type: ignore[type-arg]
    return {
        "id": r.id, "name": r.name, "type": r.type,
        "vendor": r.vendor, "model": r.model, "serial": r.serial,
        "primary_ip_id": r.primary_ip_id,
        "location_id": r.location_id, "rack_id": r.rack_id,
        "u_position": r.u_position, "u_size": r.u_size,
        "description": r.description,
        "created_at": r.created_at, "updated_at": r.updated_at,
    }


# ─────────────────── Schema + Router ───────────────────


schema = strawberry.Schema(query=Query)


async def _context_getter(
    user=Annotated[User, "from REST dep"],  # 由下方 dependency_overrides 取代
    session=Annotated[AsyncSession, "from REST dep"],
):  # pragma: no cover — 真正會被 GraphQLRouter 的 context_getter 替代
    return {"user": user, "session": session}


def make_graphql_router() -> GraphQLRouter:
    """組 GraphQL FastAPI router；context 內注入 RESTful 同樣的 user + session。"""
    from fastapi import Depends

    async def _ctx(
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ):
        return {"user": user, "session": session}

    return GraphQLRouter(
        schema,
        context_getter=_ctx,
        graphql_ide=None,  # production 不啟用 GraphiQL（A05）
    )
