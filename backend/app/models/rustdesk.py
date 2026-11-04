"""RustDesk Server（開源版）整合（2026-10-04）。

開源版沒有管理 API（那是 Pro 版的功能），由裝在 RustDesk 主機上的專用代理（agent/jt_ipam_rustdesk_agent.py）
讀 hbbs 的資料庫（已註冊的 ID、首次註冊時間、登記 IP）、用 OnlineRequest 查線上狀態，只回報解析後的結果。
hbbs 不存主機名稱與 OS；那些來自客戶端自己的回報（代理聽 21114，見 docs/SPEC_RUSTDESK_API_zh-TW.md）。

ID 對應到 IP 記錄是保守的（見 services/rustdesk.py）：不確定就不對應。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RustDeskServer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "rustdesk_servers"
    __table_args__ = (
        CheckConstraint("transport IN ('tcp', 'ws')", name="ck_rustdesk_servers_transport"),
        CheckConstraint("web_file_max_file_mb BETWEEN 1 AND 1048576 AND web_file_max_total_mb BETWEEN 1 AND 1048576",
                        name="ck_rustdesk_servers_web_file_limits"),
    )

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # 專用代理的金鑰（sha256；明文加密存在 encrypted_secrets，object_type="rustdesk_server"）。
    # 一台伺服器一把金鑰：代理拿這把金鑰就只能讀寫這台伺服器的資料
    agent_key_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    agent_version: Mapped[str | None] = mapped_column(String(32))
    # 代理怎麼確認連到的是真的 jt-ipam（X-Agent-Tls：pinned / ca / insecure；舊版代理沒帶 → None）
    agent_tls_mode: Mapped[str | None] = mapped_column(String(16))
    agent_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_source_ip: Mapped[str | None] = mapped_column(String(64))
    agent_hostname: Mapped[str | None] = mapped_column(String(255))
    # 代理自報的狀態：{"receiver": {"listening", "port", "error"}, "data_dir"}
    agent_status: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 「立即同步」：代理下次輪詢（幾秒內）取走後馬上讀一次資料庫回報
    force_report_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 「測試」：代理下次輪詢取走 test_id，在 RustDesk 主機上逐項檢查後回報結果
    test_id: Mapped[str | None] = mapped_column(String(36))
    test_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    test_result_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    test_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    report_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    # 客戶端連這台伺服器用的位址（例如對外的網域名稱），組「以 RustDesk 連線」的網址用；
    # 空白＝不指定，客戶端用它自己設定的伺服器
    client_address: Mapped[str | None] = mapped_column(String(255))

    # 代理回報的
    public_key: Mapped[str | None] = mapped_column(String(64))
    server_version: Mapped[str | None] = mapped_column(String(32))
    last_report_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # 代理讀資料庫的狀態：{"db": {path, ok, error, truncated}}
    file_status: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    description: Mapped[str | None] = mapped_column(Text)
    # 客戶端回報（docs/SPEC_RUSTDESK_API_zh-TW.md）：代理聽 api_port 收心跳／系統資訊／稽核。網頁上的開關，預設開
    receive_reports: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    api_port: Mapped[int] = mapped_column(Integer, default=21114, nullable=False)
    last_events_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 代理丟掉的回報：{"unverified": n, "rate_limited": n, "bad_request": n, "queue_overflow": n, "rejected": n}（累計）
    events_dropped: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 相容 RustDesk 的網頁連線（docs/SPEC_RUSTDESK_WEBCLIENT_zh-TW.md）：後端連 hbbs 會合、連 hbbr 中繼，只轉送密文。
    # web_enabled 是網頁上的開關，預設關
    web_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 後端連 hbbs 用的位址（可帶 :埠）；空白＝用代理回報的來源位址 agent_source_ip
    hbbs_host: Mapped[str | None] = mapped_column(String(255))
    # 後端連 hbbr 用的位址（可帶 :埠）；空白＝hbbs 的主機＋中繼預設埠（規格 5.4）
    relay_host: Mapped[str | None] = mapped_column(String(255))
    # 後端連 hbbs／hbbr 的傳輸：tcp（21116／21117）或 ws（21118／21119）
    transport: Mapped[str] = mapped_column(String(8), default="tcp", nullable=False)
    # 「刪除舊註冊」：網頁上的開關，預設關（0182）。另外還要 RustDesk 主機的管理員以 --allow-delete 重裝代理，
    # 代理才寫得了 hbbs 的資料庫（兩邊都同意才會動到 RustDesk 的資料）
    allow_peer_delete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 網頁檔案傳輸（0183，規格附錄 J）：另外一條連線，沿用網頁連線的票證與中繼（後端照舊只轉送密文）。
    # 網頁上的開關，預設關；還要 web_enabled 開著。上傳上限由瀏覽器照著擋（MB）
    web_file_transfer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    web_file_max_file_mb: Mapped[int] = mapped_column(Integer, default=2048, nullable=False)
    web_file_max_total_mb: Mapped[int] = mapped_column(Integer, default=10240, nullable=False)


class RustDeskPeer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """hbbs 資料庫裡的一個 RustDesk ID。上游刪掉就跟著刪（只在完整讀到資料庫時）。"""

    __tablename__ = "rustdesk_peers"
    __table_args__ = (UniqueConstraint("server_id", "rustdesk_id", name="uq_rustdesk_peer"),)

    server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rustdesk_servers.id", ondelete="CASCADE"), nullable=False, index=True)
    rustdesk_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # hbbs 看到的來源位址（IP 變了才更新）；可能是 NAT 後的公網位址
    registered_ip: Mapped[str | None] = mapped_column(INET)
    first_registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # jt-ipam 最後一次看到它上線（hbbs 不記，是我們自己記的）
    last_online_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 對應到的 IP 記錄；match_status 說明為什麼有或沒有：
    # matched／not_seen_online／stale／shared（NAT）／ambiguous／unmanaged／no_ip／device_changed（最後出現早於位址換手）
    address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ip_addresses.id", ondelete="SET NULL"), index=True)
    match_status: Mapped[str] = mapped_column(String(16), default="no_ip", nullable=False)
    # 對應依據：["registered_ip", "report_ip", "hostname"] 的子集
    match_evidence: Mapped[list[str] | None] = mapped_column(JSONB)
    # 只有主機名稱對得上（IP 對不上）時的建議，不算關聯
    candidate_address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ip_addresses.id", ondelete="SET NULL"), index=True)

    # 客戶端回報的系統資訊（hbbs 不存這些）
    hostname: Mapped[str | None] = mapped_column(String(255))
    os_name: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255))
    cpu: Mapped[str | None] = mapped_column(String(255))
    memory: Mapped[str | None] = mapped_column(String(64))
    client_version: Mapped[str | None] = mapped_column(String(32))
    sysinfo_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_conns: Mapped[int | None] = mapped_column(Integer)
    # 心跳送進來的來源位址：內網客戶端直連 RustDesk 主機，沒有經過 NAT，是當下的真實位址
    report_ip: Mapped[str | None] = mapped_column(INET)
    report_ip_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Key 設錯（代理讀 hbbr／hbbs 日誌）：Key 錯的客戶端照樣註冊、回報、區網直連，只有走中繼被拒。
    # key_ok_at 比 key_fail_at 新＝已經改好（0181）
    key_fail_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    key_fail_scope: Mapped[str | None] = mapped_column(String(8))
    key_fail_count: Mapped[int | None] = mapped_column(Integer)
    key_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


#: 刪除請求的狀態：pending＝等代理取走；其餘是代理回報（或 jt-ipam 自己判定）的結果
PEER_DELETE_STATUSES = ("pending", "deleted", "skipped_online", "not_found", "failed", "cancelled")
#: 代理回報得出來的結果（pending／cancelled 只有 jt-ipam 自己會設）
PEER_DELETE_RESULTS = ("deleted", "skipped_online", "not_found", "failed")


class RustDeskPeerDelete(Base, UUIDPrimaryKeyMixin):
    """「刪除舊註冊」的請求與結果（0182）。管理員在 jt-ipam 選好要刪的 ID，代理下次輪詢取走，
    刪之前在 RustDesk 主機上再查一次線上狀態（上線中的不刪），只刪 hbbs 資料庫 peer 表裡那一列。"""

    __tablename__ = "rustdesk_peer_deletes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'deleted', 'skipped_online', 'not_found', 'failed', 'cancelled')",
            name="ck_rustdesk_peer_deletes_status"),
        Index("ix_rustdesk_peer_deletes_server_time", "server_id", "requested_at"),
        # 同一個 ID 同時只會有一筆等待中的請求（重複按不會變成兩筆）
        Index("uq_rustdesk_peer_deletes_pending", "server_id", "rustdesk_id", unique=True,
              postgresql_where=text("status = 'pending'")),
        Index("ix_rustdesk_peer_deletes_requested_by", "requested_by",
              postgresql_where=text("requested_by IS NOT NULL")),
    )

    server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rustdesk_servers.id", ondelete="CASCADE"), nullable=False)
    rustdesk_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    # 結果說明（失敗原因原文、逾時、關閉開關…），給人看
    detail: Mapped[str | None] = mapped_column(String(500))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RustDeskAuditEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """客戶端回報的連線／檔案傳輸／告警／備註。nonce 去重（客戶端重送時沿用同一個 nonce）。"""

    __tablename__ = "rustdesk_audit_events"
    __table_args__ = (
        Index("ix_rustdesk_audit_events_server_time", "server_id", "occurred_at"),
        Index("uq_rustdesk_audit_events_nonce", "server_id", "nonce", unique=True,
              postgresql_where=text("nonce IS NOT NULL")),
    )

    server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rustdesk_servers.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    action: Mapped[str | None] = mapped_column(String(8))
    rustdesk_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    peer_id: Mapped[str | None] = mapped_column(String(100), index=True)
    peer_name: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(INET)
    conn_type: Mapped[int | None] = mapped_column(SmallInteger)
    conn_id: Mapped[int | None] = mapped_column(BigInteger)
    session_id: Mapped[str | None] = mapped_column(String(32))
    alarm_type: Mapped[int | None] = mapped_column(SmallInteger)
    nonce: Mapped[str | None] = mapped_column(String(64))
    verified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    src_ip: Mapped[str | None] = mapped_column(INET)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
