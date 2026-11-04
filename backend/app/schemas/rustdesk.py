"""RustDesk Server（開源版）整合的 schema。"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from app.schemas.base import StrictModel

# 客戶端連線用的伺服器位址：主機名稱或 IP，可帶 :埠。會被組進 rustdesk:// 網址，所以只收這個形狀
_ADDR = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}"
                   r"[A-Za-z0-9])?)*|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$")


def _check_address(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if len(v) > 255 or not _ADDR.match(v):
        raise ValueError("client_address must be a host name or IP, optionally with :port")
    port = v.rsplit(":", 1)[1] if ":" in v and not v.endswith("]") else None
    if port is not None and not 1 <= int(port) <= 65535:
        raise ValueError("port out of range")
    return v


#: 網頁檔案傳輸的上傳上限（MB）：1 MB ～ 1 TB
_FileLimitMb = Annotated[int, Field(ge=1, le=1_048_576)]


class RustDeskTicketIn(StrictModel):
    """網頁連線的票證要給哪一種連線：遠端桌面（預設）或檔案傳輸（規格附錄 J.6）。"""

    kind: Literal["desktop", "file"] = "desktop"


class RustDeskServerCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    enabled: bool = True
    report_interval_seconds: Annotated[int, Field(ge=60, le=86400)] = 300
    client_address: str | None = None
    description: Annotated[str | None, Field(max_length=2048)] = None
    receive_reports: bool = True
    api_port: Annotated[int, Field(ge=1, le=65535)] = 21114
    # 相容 RustDesk 的網頁連線（預設關）；hbbs_host／relay_host 只收主機名稱或 IP（可帶 :埠），
    # 後端只會連到這兩個位址（防 SSRF）
    web_enabled: bool = False
    hbbs_host: str | None = None
    relay_host: str | None = None
    transport: Literal["tcp", "ws"] = "tcp"
    # 「刪除舊註冊」（預設關）：另外要 RustDesk 主機上以 --allow-delete 重裝代理，代理才寫得了 hbbs 的資料庫
    allow_peer_delete: bool = False
    # 網頁檔案傳輸（預設關，規格附錄 J）與上傳上限（MB：單檔、單次總量）
    web_file_transfer: bool = False
    web_file_max_file_mb: _FileLimitMb = 2048
    web_file_max_total_mb: _FileLimitMb = 10240

    _addr = field_validator("client_address", "hbbs_host", "relay_host")(_check_address)


class RustDeskServerUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    enabled: bool | None = None
    report_interval_seconds: Annotated[int | None, Field(ge=60, le=86400)] = None
    client_address: str | None = None
    description: Annotated[str | None, Field(max_length=2048)] = None
    receive_reports: bool | None = None
    api_port: Annotated[int | None, Field(ge=1, le=65535)] = None
    web_enabled: bool | None = None
    hbbs_host: str | None = None
    relay_host: str | None = None
    transport: Literal["tcp", "ws"] | None = None
    allow_peer_delete: bool | None = None
    web_file_transfer: bool | None = None
    web_file_max_file_mb: _FileLimitMb | None = None
    web_file_max_total_mb: _FileLimitMb | None = None

    _addr = field_validator("client_address", "hbbs_host", "relay_host")(_check_address)


class RustDeskServerRead(StrictModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    # 專用代理
    has_agent_key: bool = False
    agent_version: str | None = None
    agent_tls_mode: str | None = None          # pinned / ca / insecure；None＝舊版代理
    agent_latest_version: str | None = None     # 伺服器上這一版代理程式的版本（給畫面標「版本落後」）
    agent_last_seen_at: datetime | None = None
    agent_source_ip: str | None = None
    agent_hostname: str | None = None
    agent_status: dict[str, Any] | None = None
    agent_poll_seconds: int = 10
    force_report_at: datetime | None = None
    enabled: bool
    report_interval_seconds: int
    client_address: str | None = None
    description: str | None = None
    public_key: str | None = None
    server_version: str | None = None
    last_report_at: datetime | None = None
    last_error: str | None = None
    file_status: dict[str, Any] | None = None
    last_summary: dict[str, Any] | None = None
    # 目前 Key 設錯的裝置數（中繼拒絕過、之後沒通過；清單即時算）
    key_problems: int = 0
    # 代理目前算不算上線：後端在回應當下用自己的時鐘判斷（畫面不可以拿瀏覽器的現在時間去算舊資料）
    agent_online: bool = False
    receive_reports: bool = True
    api_port: int = 21114
    last_events_at: datetime | None = None
    events_dropped: dict[str, Any] | None = None
    web_enabled: bool = False
    hbbs_host: str | None = None
    relay_host: str | None = None
    transport: str = "tcp"
    allow_peer_delete: bool = False
    web_file_transfer: bool = False
    web_file_max_file_mb: int = 2048
    web_file_max_total_mb: int = 10240
    created_at: datetime
    updated_at: datetime


class RustDeskServerCreated(RustDeskServerRead):
    """新增或輪替金鑰時才回傳一次明文金鑰（之後要看用 GET /servers/{id}/agent-key）。"""

    agent_key: str


class RustDeskTestCheck(StrictModel):
    key: Annotated[str, Field(pattern=r"^[a-z_]{1,32}$")]
    ok: bool
    detail: Annotated[str | None, Field(max_length=2000)] = None


class RustDeskTestState(StrictModel):
    test_id: str | None = None
    requested_at: datetime | None = None
    result_at: datetime | None = None
    checks: list[RustDeskTestCheck] = []
    agent_last_seen_at: datetime | None = None


class RustDeskPeerRead(StrictModel):
    id: uuid.UUID
    rustdesk_id: str
    registered_ip: str | None = None
    first_registered_at: datetime | None = None
    online: bool
    last_online_at: datetime | None = None
    match_status: str
    address_id: uuid.UUID | None = None
    address_ip: str | None = None
    address_hostname: str | None = None
    address_device_kind: str | None = None      # 對應 IP 的設備類型（掃描代理／探測判讀）
    address_device_model: str | None = None
    subnet_id: uuid.UUID | None = None
    match_evidence: list[str] | None = None
    candidate_address_id: uuid.UUID | None = None
    candidate_ip: str | None = None
    candidate_hostname: str | None = None
    hostname: str | None = None
    os_name: str | None = None
    username: str | None = None
    client_version: str | None = None
    last_heartbeat_at: datetime | None = None
    active_conns: int | None = None
    report_ip: str | None = None
    # Key 設錯：{at, scope, count}；沒有問題（或之後已通過中繼）為 None
    key_problem: dict[str, Any] | None = None


class RustDeskAuditRead(StrictModel):
    id: uuid.UUID
    kind: str
    action: str | None = None
    rustdesk_id: str
    peer_id: str | None = None
    peer_name: str | None = None
    ip: str | None = None
    conn_type: int | None = None
    conn_id: int | None = None
    session_id: str | None = None
    alarm_type: int | None = None
    nonce: str | None = None
    verified: bool
    detail: dict[str, Any] | None = None
    src_ip: str | None = None
    occurred_at: datetime
    # 受控端對應到的 IP（沒對應就是 None）與它回報的主機名稱
    device_address_id: uuid.UUID | None = None
    device_ip: str | None = None
    device_hostname: str | None = None
    device_reported_hostname: str | None = None


# ── 刪除舊註冊（0182）────────────────────────────────────────────────────────

_RD_ID = r"^[A-Za-z0-9_-]{3,100}$"
#: 一次最多要求刪幾個 ID（代理每次輪詢最多取走 PEER_DELETE_BATCH 筆，分幾輪做完）
PEER_DELETE_MAX = 500


class RustDeskPeerDeleteIn(StrictModel):
    rustdesk_ids: Annotated[list[Annotated[str, Field(pattern=_RD_ID)]],
                            Field(min_length=1, max_length=PEER_DELETE_MAX)]


class RustDeskPeerDeleteQueued(StrictModel):
    queued: int                     # 這次新排入的筆數
    already_pending: int            # 已經在等代理處理的（不重複排）
    requested_at: datetime          # 畫面用 since=這個時間等結果
    eta_seconds: int
    agent_online: bool


class RustDeskPeerDeleteRead(StrictModel):
    id: uuid.UUID
    rustdesk_id: str
    status: str
    detail: str | None = None
    requested_by: uuid.UUID | None = None
    requested_by_name: str | None = None
    requested_at: datetime
    finished_at: datetime | None = None


# ── 代理回報 ─────────────────────────────────────────────────────────────────

class RustDeskReportPeer(StrictModel):
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{3,100}$")]
    created_at: Annotated[str | None, Field(max_length=40)] = None
    ip: Annotated[str | None, Field(max_length=64)] = None
    online: bool | None = None


class RustDeskReportFile(StrictModel):
    path: Annotated[str | None, Field(max_length=1024)] = None
    ok: bool = False
    error: Annotated[str | None, Field(max_length=2000)] = None
    truncated: bool = False


class RustDeskReportFiles(StrictModel):
    db: RustDeskReportFile


class RustDeskReport(StrictModel):
    """代理讀 hbbs 資料庫與查線上狀態的結果（只有解析後的資料）。"""

    source_id: uuid.UUID
    version: Annotated[str | None, Field(max_length=32)] = None
    public_key: Annotated[str | None, Field(pattern=r"^[A-Za-z0-9+/]{43}=$")] = None
    files: RustDeskReportFiles
    online_ok: bool = False
    online_error: Annotated[str | None, Field(max_length=2000)] = None
    peers: Annotated[list[RustDeskReportPeer], Field(max_length=50000)] = []


# ── 專用代理的輪詢與測試結果 ───────────────────────────────────────────────────

class RustDeskAgentReceiver(StrictModel):
    listening: bool = False
    port: int | None = None
    error: Annotated[str | None, Field(max_length=500)] = None


class RustDeskKeyFail(StrictModel):
    """日誌裡因為 Key 被拒：relay＝hbbr 拒絕（被連線那一端）、hbbs＝hbbs 拒絕主動連線（發起那一端）。"""

    scope: Literal["relay", "hbbs"]
    ip: Annotated[str, Field(max_length=64)]
    n: Annotated[int, Field(ge=1, le=10_000_000)]
    first: datetime
    last: datetime
    target: Annotated[str | None, Field(max_length=100)] = None


class RustDeskKeyOk(StrictModel):
    """日誌裡通過中繼（New relay request／got paired）。"""

    ip: Annotated[str, Field(max_length=64)]
    last: datetime


class RustDeskKeyLogs(StrictModel):
    dir: Annotated[str | None, Field(max_length=1024)] = None
    hbbr: bool = False
    hbbs: bool = False
    error: Annotated[str | None, Field(max_length=500)] = None


class RustDeskKeyChecks(StrictModel):
    """代理 1.1.0 起：上次輪詢之後 hbbr／hbbs 日誌裡的 Key 檢查結果（依 IP 彙總，不含日誌原文）。"""

    fails: Annotated[list[RustDeskKeyFail], Field(max_length=2000)] = []
    ok: Annotated[list[RustDeskKeyOk], Field(max_length=2000)] = []
    dropped: Annotated[int, Field(ge=0)] = 0
    logs: RustDeskKeyLogs | None = None


class RustDeskAgentCapabilities(StrictModel):
    """代理 1.2.0 起：這台主機上代理做得到什麼。delete＝寫得了 hbbs 的資料庫（主機端以 --allow-delete 安裝、
    資料庫與它的目錄都可寫）；做不到時 delete_reason 說原因。"""

    delete: bool = False
    delete_reason: Annotated[str | None, Field(max_length=500)] = None


class RustDeskAgentPollIn(StrictModel):
    """代理每次輪詢順便回報自己的狀態（畫面上看得到接收端有沒有在聽、讀的是哪個目錄）。"""

    version: Annotated[str | None, Field(max_length=32)] = None
    hostname: Annotated[str | None, Field(max_length=255)] = None
    data_dir: Annotated[str | None, Field(max_length=1024)] = None
    receiver: RustDeskAgentReceiver | None = None
    key_checks: RustDeskKeyChecks | None = None
    capabilities: RustDeskAgentCapabilities | None = None


class RustDeskPeerDeleteJob(StrictModel):
    """輪詢回應帶給代理的刪除工作。"""

    req_id: uuid.UUID
    rustdesk_id: str


class RustDeskAgentPollOut(StrictModel):
    source_id: uuid.UUID
    enabled: bool
    interval_seconds: int
    api_listen: bool
    api_port: int
    report_now: bool = False
    test_id: str | None = None
    agent_sha: str
    poll_seconds: int
    # 等待中的「刪除舊註冊」（只有網頁上允許、伺服器啟用時才帶；每次最多 PEER_DELETE_BATCH 筆）
    peer_deletes: list[RustDeskPeerDeleteJob] = []


class RustDeskTestResultIn(StrictModel):
    test_id: Annotated[str, Field(max_length=36)]
    checks: Annotated[list[RustDeskTestCheck], Field(max_length=20)]


class RustDeskDeleteResult(StrictModel):
    req_id: uuid.UUID
    status: Literal["deleted", "skipped_online", "not_found", "failed"]
    detail: Annotated[str | None, Field(max_length=500)] = None


class RustDeskDeleteResultIn(StrictModel):
    """代理執行「刪除舊註冊」的結果。"""

    source_id: uuid.UUID
    results: Annotated[list[RustDeskDeleteResult], Field(max_length=500)]


# ── 代理轉送的客戶端回報（docs/SPEC_RUSTDESK_API_zh-TW.md §7）─────────────────

class RustDeskEvent(StrictModel):
    """所有種類共用一個形狀；各種類只用到其中幾個欄位。字串長度代理端已截斷過，這裡再擋一次。"""

    kind: Literal["heartbeat", "sysinfo", "conn", "note", "file", "alarm"]
    at: datetime
    src_ip: Annotated[str | None, Field(max_length=64)] = None
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{3,100}$")]
    # heartbeat
    ver: int | None = None
    conns: int | None = None
    # sysinfo
    hostname: Annotated[str | None, Field(max_length=255)] = None
    username: Annotated[str | None, Field(max_length=255)] = None
    os: Annotated[str | None, Field(max_length=255)] = None
    cpu: Annotated[str | None, Field(max_length=255)] = None
    memory: Annotated[str | None, Field(max_length=255)] = None
    version: Annotated[str | None, Field(max_length=64)] = None
    # conn / file / alarm / note
    nonce: Annotated[str | None, Field(max_length=64)] = None
    conn_id: int | None = None
    session_id: Annotated[str | None, Field(max_length=32)] = None
    action: Literal["new", "auth", "close"] | None = None
    ip: Annotated[str | None, Field(max_length=64)] = None
    peer_id: Annotated[str | None, Field(max_length=100)] = None
    peer_name: Annotated[str | None, Field(max_length=255)] = None
    type: int | None = None
    primary_auth: int | None = None
    two_factor: int | None = None
    conn_audit_ref: Annotated[str | None, Field(max_length=100)] = None
    path: Annotated[str | None, Field(max_length=1024)] = None
    is_file: bool | None = None
    num: int | None = None
    files: Annotated[list[list[Any]] | None, Field(max_length=10)] = None
    typ: int | None = None
    info: dict[str, Any] | None = None
    note: Annotated[str | None, Field(max_length=2048)] = None
    verified: bool | None = None


class RustDeskEventsIn(StrictModel):
    """事件逐筆驗證（見 endpoint）：一筆不合不可以讓整批被拒 —— 代理送失敗會保留佇列重送，
    整批 422 會讓同一批每 5 秒重送，直到被佇列上限擠掉。"""

    source_id: uuid.UUID
    dropped: dict[str, int] = {}
    events: Annotated[list[dict[str, Any]], Field(max_length=5000)] = []
