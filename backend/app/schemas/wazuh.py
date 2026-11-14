"""Wazuh schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field, HttpUrl, field_validator

from app.schemas.base import StrictModel


class WazuhInstanceBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    api_url: HttpUrl
    api_user: Annotated[str, Field(min_length=1, max_length=128)]
    enabled: bool = True
    verify_tls: bool = True
    sync_interval_seconds: Annotated[int, Field(ge=30, le=86400)] = 300
    description: Annotated[str | None, Field(max_length=2048)] = None
    scope_subnet_ids: list[str] | None = None


class WazuhInstanceCreate(WazuhInstanceBase):
    api_password: Annotated[str, Field(min_length=4, max_length=512)]


class WazuhInstanceUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    api_url: HttpUrl | None = None
    api_user: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    api_password: Annotated[str | None, Field(min_length=4, max_length=512)] = None
    enabled: bool | None = None
    verify_tls: bool | None = None
    sync_interval_seconds: Annotated[int | None, Field(ge=30, le=86400)] = None
    description: Annotated[str | None, Field(max_length=2048)] = None
    scope_subnet_ids: list[str] | None = None


class WazuhInstanceRead(WazuhInstanceBase):
    id: uuid.UUID
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class WazuhAgentRead(StrictModel):
    id: uuid.UUID
    instance_id: uuid.UUID
    agent_id: str
    name: str | None
    ip: str | None
    register_ip: str | None
    status: str | None
    os_platform: str | None
    os_version: str | None
    os_name: str | None = None
    agent_version: str | None
    group: str | None
    node_name: str | None
    last_keep_alive: datetime | None
    last_seen_at: datetime | None
    jt_ipam_address_id: uuid.UUID | None
    cve_summary_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("ip", "register_ip", mode="before")
    @classmethod
    def _coerce_ip(cls, v: object) -> str | None:
        if v is None:
            return None
        return str(v).split("/", 1)[0]


class MissingAgentRow(StrictModel):
    ip_address_id: uuid.UUID
    ip: str | None
    hostname: str | None
    # 所屬範圍（畫面依子網路／區段／單位篩選；單位＝IP → 子網路 → 區段第一個有掛的）
    subnet_id: uuid.UUID | None = None
    subnet_cidr: str | None = None
    section_id: uuid.UUID | None = None
    section_name: str | None = None
    customer_id: uuid.UUID | None = None
    customer_name: str | None = None
    # 上線判斷要吃的欄位（前端用 IP 清單燈號同一套規則算，畫面可依狀態篩選）
    last_seen_scanner: str | None = None
    last_seen_librenms: str | None = None
    last_seen_arp: str | None = None
    last_seen_wazuh: str | None = None
    last_seen_zabbix: str | None = None
    last_seen_checkmk: str | None = None
    arp_seen: dict[str, str] = {}
    exclude_from_ping: bool = False
    subnet_scan_enabled: bool | None = None
    # 「設備類型」欄：掃描代理判讀出的類型與型號（services/device_identity）
    device_kind: str | None = None
    device_model: str | None = None



class MissingAgentFacet(StrictModel):
    value: str
    label: str


class MissingAgentFacets(StrictModel):
    sections: list[MissingAgentFacet] = []
    subnets: list[MissingAgentFacet] = []
    customers: list[MissingAgentFacet] = []
    statuses: list[MissingAgentFacet] = []


class MissingAgentPageRow(MissingAgentRow):
    #: 伺服器依畫面燈號同一套規則算的上線狀態（online／stale／offline／unknown）
    status: str | None = None


class MissingAgentPage(StrictModel):
    """帶 page 參數時的回應：一頁資料＋篩選後總數＋全部缺口數＋篩選選項（Wazuh 與 OCS 共用）。"""
    items: list[MissingAgentPageRow]
    total: int
    total_all: int
    facets: MissingAgentFacets
