"""NAT schemas（phpIPAM 招牌：1:1 / N:1 / Port Forward）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field, field_validator

from app.schemas.base import StrictModel

_TYPES = {"one_to_one", "many_to_one", "port_forward"}
_PROTOS = {"tcp", "udp", "any", "icmp", "esp", "gre", "tcp/udp"}


class _NATExtra(StrictModel):
    """OPNsense 規則完整欄位（建立/更新/讀取共用）。"""
    disabled: bool = False
    no_rdr: bool = False
    ip_version: Annotated[str, Field(max_length=8)] = "inet"
    src_not: bool = False
    dst_not: bool = False
    src_port_to: Annotated[int | None, Field(ge=1, le=65535)] = None
    dst_port_to: Annotated[int | None, Field(ge=1, le=65535)] = None
    log: bool = False
    category: Annotated[str | None, Field(max_length=128)] = None
    nat_reflection: Annotated[str | None, Field(max_length=16)] = None
    pool_options: Annotated[str | None, Field(max_length=32)] = None
    filter_rule: Annotated[str | None, Field(max_length=128)] = None
    src_alias: Annotated[str | None, Field(max_length=64)] = None
    dst_alias: Annotated[str | None, Field(max_length=64)] = None
    src_port_alias: Annotated[str | None, Field(max_length=64)] = None
    dst_port_alias: Annotated[str | None, Field(max_length=64)] = None
    redirect_alias: Annotated[str | None, Field(max_length=64)] = None


class NATBase(_NATExtra):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    type: str
    src_ip_id: uuid.UUID | None = None
    dst_ip_id: uuid.UUID | None = None
    src_port: Annotated[int | None, Field(ge=1, le=65535)] = None
    dst_port: Annotated[int | None, Field(ge=1, le=65535)] = None
    protocol: str = "any"
    device_id: uuid.UUID | None = None
    src_interface: Annotated[str | None, Field(max_length=64)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None

    @field_validator("type")
    @classmethod
    def _type_valid(cls, v: str) -> str:
        if v not in _TYPES:
            raise ValueError(f"type must be one of {sorted(_TYPES)}")
        return v

    @field_validator("protocol")
    @classmethod
    def _proto_valid(cls, v: str) -> str:
        if v not in _PROTOS:
            raise ValueError(f"protocol must be one of {sorted(_PROTOS)}")
        return v


class NATCreate(NATBase):
    pass


class NATUpdate(_NATExtra):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    src_ip_id: uuid.UUID | None = None
    dst_ip_id: uuid.UUID | None = None
    src_port: Annotated[int | None, Field(ge=1, le=65535)] = None
    dst_port: Annotated[int | None, Field(ge=1, le=65535)] = None
    protocol: str | None = None
    device_id: uuid.UUID | None = None
    src_interface: Annotated[str | None, Field(max_length=64)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None

    @field_validator("protocol")
    @classmethod
    def _proto_valid(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in _PROTOS:
            raise ValueError(f"protocol must be one of {sorted(_PROTOS)}")
        return v


class NATRead(NATBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    src_interface: str | None = None
    # 來源資訊：從 source_origin 解出
    source_origin: str | None = None
    source_kind: str | None = None          # "opnsense" | "phpipam" | "manual"
    source_firewall_id: uuid.UUID | None = None
    source_label: str | None = None         # "OPNsense: router-007" / "phpIPAM" / "手動"
    external_id: str | None = None
