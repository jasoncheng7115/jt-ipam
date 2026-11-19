"""Kubernetes 整合 schemas。token 與用戶端私鑰只進不出；也可以直接貼 kubeconfig。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from app.schemas.base import StrictModel

_SECRET = Annotated[str, Field(min_length=1, max_length=65536)]
_PEM = Annotated[str, Field(max_length=65536)]


class K8sCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    #: 貼上 kubeconfig 時，下面的連線欄位都從它取（有給的話以 kubeconfig 為準）
    kubeconfig: Annotated[str | None, Field(max_length=262144)] = None
    kubeconfig_context: Annotated[str | None, Field(max_length=253)] = None
    api_url: Annotated[str | None, Field(max_length=512)] = None
    ca_cert: _PEM | None = None
    verify_tls: bool = True
    auth_method: Literal["token", "client_cert"] = "token"
    token: _SECRET | None = None
    client_cert: _PEM | None = None
    client_key: _SECRET | None = None
    enabled: bool = True
    scope_subnet_ids: list[uuid.UUID] | None = None
    sync_interval_seconds: Annotated[int, Field(ge=60, le=86400)] = 300
    create_ip_ranges: bool = True
    pod_network_routable: bool = False
    description: Annotated[str | None, Field(max_length=2048)] = None


class K8sUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    kubeconfig: Annotated[str | None, Field(max_length=262144)] = None
    kubeconfig_context: Annotated[str | None, Field(max_length=253)] = None
    api_url: Annotated[str | None, Field(max_length=512)] = None
    #: 空字串＝清除
    ca_cert: _PEM | None = None
    verify_tls: bool | None = None
    auth_method: Literal["token", "client_cert"] | None = None
    #: 留空＝不變更
    token: _SECRET | None = None
    client_cert: _PEM | None = None
    client_key: _SECRET | None = None
    enabled: bool | None = None
    scope_subnet_ids: list[uuid.UUID] | None = None
    sync_interval_seconds: Annotated[int | None, Field(ge=60, le=86400)] = None
    create_ip_ranges: bool | None = None
    pod_network_routable: bool | None = None
    description: Annotated[str | None, Field(max_length=2048)] = None


class K8sRead(StrictModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    api_url: str
    has_ca_cert: bool = False
    verify_tls: bool
    auth_method: str
    has_token: bool = False
    has_client_key: bool = False
    enabled: bool
    scope_subnet_ids: list[uuid.UUID] | None = None
    sync_interval_seconds: int
    create_ip_ranges: bool
    pod_network_routable: bool = False
    missing_permissions: list[str] | None = None
    server_version: str | None = None
    description: str | None = None
    last_sync_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
