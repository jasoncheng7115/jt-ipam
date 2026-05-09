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


class VLANCreate(VLANBase):
    pass


class VLANUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    description: Annotated[str | None, Field(max_length=512)] = None


class VLANRead(VLANBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
