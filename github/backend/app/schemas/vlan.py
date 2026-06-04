"""VLAN / VLANDomain schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field

from app.schemas.base import StrictModel


class VLANDomainBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[str | None, Field(max_length=512)] = None


class VLANDomainCreate(VLANDomainBase):
    pass


class VLANDomainUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    description: Annotated[str | None, Field(max_length=512)] = None


class VLANDomainRead(VLANDomainBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class VLANBase(StrictModel):
    domain_id: uuid.UUID
    number: Annotated[int, Field(ge=1, le=4094)]
    name: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[str | None, Field(max_length=512)] = None
    customer_id: uuid.UUID | None = None
    section_id: uuid.UUID | None = None


class VLANCreate(VLANBase):
    pass


class VLANUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    description: Annotated[str | None, Field(max_length=512)] = None
    customer_id: uuid.UUID | None = None
    section_id: uuid.UUID | None = None


class VLANRead(VLANBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    # feature C：掛在此 VLAN 的（LibreNMS）裝置數
    device_count: int = 0
    port_count: int = 0    # FDB 中屬於此 VLAN 的不重複 (裝置, 連接埠) 數
    ip_count: int = 0      # 此 VLAN 下子網路的 IP 數
