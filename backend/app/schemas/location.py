"""Location / Rack schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field

from app.schemas.base import StrictModel


class LocationBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    address: Annotated[str | None, Field(max_length=512)] = None
    latitude: Annotated[float | None, Field(ge=-90, le=90)] = None
    longitude: Annotated[float | None, Field(ge=-180, le=180)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class LocationCreate(LocationBase):
    pass


class LocationUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    address: Annotated[str | None, Field(max_length=512)] = None
    latitude: Annotated[float | None, Field(ge=-90, le=90)] = None
    longitude: Annotated[float | None, Field(ge=-180, le=180)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class LocationRead(LocationBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class RackBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    location_id: uuid.UUID | None = None
    u_height: Annotated[int, Field(ge=1, le=99)] = 42
    description: Annotated[str | None, Field(max_length=1024)] = None


class RackCreate(RackBase):
    pass


class RackUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    location_id: uuid.UUID | None = None
    u_height: Annotated[int | None, Field(ge=1, le=99)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class RackRead(RackBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
