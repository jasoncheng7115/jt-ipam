"""網域（客戶許願清單 2026-10-28）：註冊了哪些網域、在哪家註冊商、何時到期、NS 與 DNS 託管在哪。

到期日、NS、註冊商名稱由 RDAP 查（`rdap_*` 欄位，每天更新）；不支援 RDAP 的頂級網域（例如 .io、.jp）
用手填的 `expires_at`。註冊商的帳號只記帳號名稱，**不存密碼**（使用者 2026-10-28 決定）。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Domain(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "domains"

    name: Mapped[str] = mapped_column(String(253), unique=True, nullable=False)   # 小寫、去掉結尾的點
    registrar: Mapped[str | None] = mapped_column(String(128))         # 手填；沒填時畫面用 RDAP 查到的
    registrar_url: Mapped[str | None] = mapped_column(Text)            # 註冊商的管理網址
    registrar_account: Mapped[str | None] = mapped_column(String(128))  # 帳號名稱（不存密碼）
    dns_provider: Mapped[str | None] = mapped_column(String(128))      # 手填；沒填時畫面用 NS 推測的
    services: Mapped[str | None] = mapped_column(Text)                 # 綁著的特殊服務（郵件、驗證紀錄…）
    description: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[date | None] = mapped_column(Date)              # 手填的到期日（RDAP 查不到時用）
    auto_renew: Mapped[bool | None] = mapped_column(Boolean)

    rdap_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rdap_error: Mapped[str | None] = mapped_column(String(64))         # 代碼，例如 rdap_no_service / rdap_not_found
    rdap_registrar: Mapped[str | None] = mapped_column(String(255))
    rdap_registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rdap_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rdap_nameservers: Mapped[list[str] | None] = mapped_column(JSONB)
    rdap_status: Mapped[list[str] | None] = mapped_column(JSONB)
