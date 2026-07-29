"""Windows DHCP Server 整合（Beta）—— 自己的設定，與 OPNsense / pfSense 的 DHCP 各自獨立。

透過 WinRM + PowerShell（與 Windows DNS 同一套管道）唯讀拉取：
- Get-DhcpServerv4Scope → 發放範圍（寫進 dhcp_pool_ranges，source_type='windows_dhcp'）
- Get-DhcpServerv4Lease → 租約（標記既有 IP 的 in_dhcp_lease，不自動新建 IP）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WindowsDhcpServer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "windows_dhcp_servers"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)       # DHCP 主機（FQDN 或 IP）
    port: Mapped[int] = mapped_column(Integer, default=5986, nullable=False)
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)   # WinRM over HTTPS
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)   # DOMAIN\\user 或本機帳號

    # 密碼：AES-GCM 雙欄加密（比照 Wazuh / AdGuard）
    password_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    password_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    sync_scopes: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)   # 發放範圍
    sync_leases: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)   # 租約

    # 限定子網路範圍（留空＝全域比對；重疊網段建議設定）
    scope_subnet_ids: Mapped[list[Any] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
