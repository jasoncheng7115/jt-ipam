"""憑證派送 schemas（管理面;agent 協定的 schema 放 endpoint 內）。

機敏:cert_pem/chain 可回(公開資訊),**私鑰一律不回傳**(schema 根本沒有 key 欄位)。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from app.schemas.base import StrictModel


class CertificateCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    description: Annotated[str | None, Field(max_length=1024)] = None


class CertificateUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class SelfSignedRequest(StrictModel):
    """產生自簽憑證:自訂名稱(CN) + SAN + 效期天數。"""
    common_name: Annotated[str, Field(min_length=1, max_length=253)]
    sans: list[Annotated[str, Field(max_length=253)]] = Field(default_factory=list)
    days: Annotated[int, Field(ge=1, le=3650)] = 365


class CertVersionRead(StrictModel):
    id: uuid.UUID
    fingerprint_sha256: str
    serial: str | None
    subject: str | None
    issuer: str | None
    not_before: datetime | None
    not_after: datetime
    domains: list[str] | None
    is_current: bool
    uploaded_by: uuid.UUID | None
    created_at: datetime
    # 憑證鏈分析（Zimbra/PDM 等嚴格驗鏈服務需要完整鏈到根 CA）
    chain_has_root: bool = False        # 可用憑證中含自簽根
    chain_complete: bool = False        # 葉能鏈到根（中繼+根都齊）
    chain_can_rebuild: bool = False     # 可一鍵組合出含根的完整鏈（根藏在 cert 欄或順序不對）


class CertificateRead(StrictModel):
    id: uuid.UUID
    name: str
    description: str | None
    domains: list[str] | None
    created_at: datetime
    updated_at: datetime
    # 由端點計算填入（目前版本摘要 + 統計）
    current_fingerprint: str | None = None
    current_not_after: datetime | None = None
    current_days_remaining: int | None = None
    version_count: int = 0
    # 目前版本是否為自簽（subject==issuer）→ 前端據此顯示「續簽」按鈕並帶出 CN/SAN
    current_is_self_signed: bool = False
    current_common_name: str | None = None
    current_sans: list[str] | None = None
    # 自動抓取來源狀態（source_config 不含機敏:URL/host/路徑/帳號;密碼/key 在 encrypted_secret）
    source_type: str = "none"
    source_config: dict[str, Any] | None = None
    fetch_interval_seconds: int = 86400
    last_fetch_at: datetime | None = None
    last_fetch_error: str | None = None


class CertSourceUpdate(StrictModel):
    """設定自動抓取來源。source_config 放非機敏設定;密碼/私鑰另傳(write-only)。"""
    source_type: Literal["none", "url", "sftp"] = "none"
    # url: {cert_url, key_url?, chain_url?}  sftp: {host, port?, username, cert_path, key_path?, chain_path?}
    source_config: dict[str, Any] = Field(default_factory=dict)
    fetch_interval_seconds: Annotated[int, Field(ge=300, le=2592000)] = 86400
    source_password: str | None = None        # write-only:SFTP 密碼
    source_private_key: str | None = None      # write-only:SFTP 私鑰(PEM)


# ─────────────────── Cert Agents ───────────────────

class CertAgentCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    description: Annotated[str | None, Field(max_length=1024)] = None
    enabled: bool = True
    # 此 agent 可取的 certificate id 清單(deny-by-default;空＝不可取)
    scope_cert_ids: list[uuid.UUID] = Field(default_factory=list)


class CertAgentUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    enabled: bool | None = None
    scope_cert_ids: list[uuid.UUID] | None = None


class CertAgentRead(StrictModel):
    id: uuid.UUID
    name: str
    description: str | None
    enabled: bool
    scope_cert_ids: list[uuid.UUID] | None
    last_seen_at: datetime | None
    last_source_ip: str | None
    # 近期（7 天）回報過的去重來源 IP；>1 表示同把 Key 被多台主機共用 → 前端警告
    recent_source_ips: list[str] = Field(default_factory=list)
    multi_source_recent: bool = False
    agent_version: str | None
    server_agent_version: str | None = None  # server 端 agent.sh 版本；UI 比對標「可更新」
    reported: list[dict[str, Any]] | None
    has_key: bool = False
    created_at: datetime
    updated_at: datetime


class CertAgentCreated(CertAgentRead):
    """建立 / 輪替 key 時一次性回傳明文 enrollment key。"""
    enroll_key: str
