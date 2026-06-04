"""VRF schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field

from app.schemas.base import StrictModel


class VRFBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    rd: Annotated[str | None, Field(max_length=64)] = None
    description: Annotated[str | None, Field(max_length=512)] = None
    allow_overlap: bool = True


class VRFCreate(VRFBase):
    pass


class VRFUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    rd: Annotated[str | None, Field(max_length=64)] = None
    description: Annotated[str | None, Field(max_length=512)] = None
    allow_overlap: bool | None = None


class VRFRead(VRFBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
