"""IP hostname 多來源觀測（feature A）。

每個來源（manual/scanner/librenms/dns/proxmox/opnsense）對同一個 IP 各存一筆
hostname；IPAddress.hostname 是依「全域優先序 + 單 IP pin」解析後的有效值。
解析邏輯與優先序設定在 app/services/hostname.py。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin

# 主機名稱觀測來源（此表 source 欄 String(16)、無 CHECK；非 IPAddress.discovery_source）。
# netbios / mdns 由掃描代理分別以 nmblookup / avahi-resolve 取得，是獨立於 scanner(rDNS) 的來源。
# ⚠️ 新整合要加進來：apply_observation 以前把不認得的來源一律當成 manual，MikroTik 就這樣
# 把租約名稱寫成「手動輸入」，蓋過使用者真正填的值、而且永遠清不掉（2026-09-26 稽核）。
HOSTNAME_SOURCES = ("manual", "scanner", "librenms", "dns", "proxmox", "opnsense", "pfsense", "wazuh", "adguard", "netbios", "mdns", "windows_dhcp", "fortigate", "paloalto", "checkpoint", "zabbix", "checkmk", "ocs", "mikrotik", "kea_dhcp", "isc_dhcp", "isoinsight", "technitium", "rustdesk", "hyperv", "openstack")


class IPHostnameObservation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ip_hostname_observations"

    ip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_addresses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    hostname: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("ip_id", "source", name="uq_ip_hostname_obs_ip_source"),
    )


class IPHostnameReport(Base, UUIDPrimaryKeyMixin):
    """某個來源的**某一台實例**（origin）對某個 IP 回報的主機名稱。

    `ip_hostname_observations` 一個來源只有一筆，多台同類整合（兩台 OPNsense、兩個 PVE 叢集、
    兩台 DNS）共用；沒有這一層就無法安全地清掉「上游已經不再回報」的名稱 —— 逐台清會刪到別台的，
    不清就永遠留著（2026-09-26：.139 換了主機、DNS 記錄已刪，仍顯示舊名）。
    觀測表的值由這裡推導（app/services/hostname_reports.py），讀取端不必改。

    origin：`<來源>:<實例 id>[:<子來源>]`；`legacy` 是遷移前就存在、還沒被任何實例認領的舊觀測。
    last_seen_at：**每次看到都更新**（observed_at 只在名稱改變時才動，不能拿來判斷過期）。
    """
    __tablename__ = "ip_hostname_reports"

    ip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ip_addresses.id", ondelete="CASCADE"),
        nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(96), nullable=False)
    hostname: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("ip_id", "source", "origin", name="uq_ip_hostname_reports_ip_source_origin"),
        Index("ix_ip_hostname_reports_source_origin", "source", "origin"),
    )
