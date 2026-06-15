"""IP Request schemas。"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, field_validator

from app.schemas.base import StrictModel


class IPRequestCreate(StrictModel):
    subnet_id: uuid.UUID
    purpose: Annotated[str, Field(min_length=3, max_length=512)]
    hostname: Annotated[str | None, Field(max_length=253)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    requested_ip: str | None = None
    expires_at: datetime | None = None

    @field_validator("requested_ip")
    @classmethod
    def _ip_valid(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        try:
            ipaddress.ip_address(v)
        except ValueError as exc:
            raise ValueError(f"Invalid IP: {v}") from exc
        return v


class IPRequestReject(StrictModel):
    reason: Annotated[str, Field(min_length=3, max_length=1024)]


class IPRequestRead(StrictModel):
    id: uuid.UUID
    status: str
    requester_user_id: uuid.UUID
    approver_user_id: uuid.UUID | None
    subnet_id: uuid.UUID
    requested_ip: str | None
    hostname: str | None
    description: str | None
    purpose: str
    expires_at: datetime | None
    allocated_ip_id: uuid.UUID | None
    approved_at: datetime | None
    rejected_at: datetime | None
    rejected_reason: str | None
    fulfilled_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # 此請求對「目前使用者」是否可核准/駁回（端點計算後填入；不在 ORM，預設 False）
    can_approve: bool = False

    @field_validator("requested_ip", mode="before")
    @classmethod
    def _coerce_requested_ip(cls, v: object) -> str | None:
        # requested_ip 是 INET 欄位，asyncpg 回傳 IPv4Address 物件而非 str；
        # 手動指定 IP 的申請開啟列表時，Pydantic str 驗證會失敗 → 整頁 500（issue #4）。
        if v is None or v == "":
            return None
        return str(v)


class IPRequestEventRead(StrictModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    event_type: str
    message: str | None
    created_at: datetime


class IPRequestDetail(StrictModel):
    request: IPRequestRead
    events: list[IPRequestEventRead]
    subnet_cidr: str | None = None
    # pending 時：實際會配發的 IP（requested_ip，或系統自動取的第一個空位）；給審核人預覽
    target_ip: str | None = None
    target_auto: bool = False        # target_ip 是否為系統自動挑的（非申請人指定）
    allocated_ip: str | None = None  # 已配發的 IP（fulfilled 後）
    # 多關卡進度（parallel / stages）：[{index, name, approved, is_current}]；單關卡模式為空
    stages: list[dict[str, Any]] = []


class IPApprove(StrictModel):
    """核准時審核人可改配發的 IP（留空＝照申請/自動）。"""
    ip: str | None = None


class IPRequestStep(StrictModel):
    """單一審核關卡（parallel / stages 用）。"""
    name: str = ""
    user_ids: list[uuid.UUID] = []
    group_ids: list[uuid.UUID] = []


class IPRequestPolicyModel(StrictModel):
    """審核政策（管理頁設定）。

    approver_mode：admin=僅管理員；designated=管理員+指定人/群組（單關卡，任一核准）；
    parallel=多組會簽（全部關卡都核准，不分先後）；stages=依序多關卡（逐關通過）。
    """
    approver_mode: str = "admin"
    designated_user_ids: list[uuid.UUID] = []
    designated_group_ids: list[uuid.UUID] = []
    allow_self_approve: bool = False
    stages: list[IPRequestStep] = []   # parallel / stages 模式的關卡清單（有序）
