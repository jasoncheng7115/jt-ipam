"""防火牆整合 model（目前：OPNsense）。

- OPNsenseFirewall：OPNsense 實例（API key/secret 加密儲存，aad 綁 id）
- OPNsenseAliasMapping：把 jt-ipam 的 Section/Subnet/標籤 → OPNsense alias
  的對映；同步時把選定範圍的 IP 列表推到 alias content
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OPNsenseFirewall(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """OPNsense 防火牆實例。"""

    __tablename__ = "opnsense_firewalls"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    api_url: Mapped[str] = mapped_column(Text, nullable=False)

    api_key_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_key_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_secret_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_secret_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    # 擴展同步開關（除了原本的 alias mapping 出向同步外）
    sync_dhcp: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_arp: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_openvpn: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_rules: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_nat: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_aliases: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False)
    # 開啟後：每輪同步另抓 pf_statistics 規則 label，並對外提供 Graylog DSV
    # （規則 label→alias、alias→成員 兩支查表，token 沿用 graylog_dsv）
    expose_dsv: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False)

    description: Mapped[str | None] = mapped_column(Text)

    # 關聯範圍（NAT 對應）：多台防火牆共用 RFC1918 子網時，限定此防火牆的 NAT
    # 規則只對應到範圍內子網的 IP。全部 nullable；留空 = 沿用全域 IP 字串比對。
    scope_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="SET NULL"),
        nullable=True,
    )
    scope_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    scope_subnet_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True,
    )
    iface_subnet_map: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]


class OPNsenseAliasMapping(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """jt-ipam 範圍 → OPNsense alias 的同步規則。

    `selector` 描述要送哪些 IP 進 alias，例如：
      {"type": "section", "section_id": "..."}
      {"type": "subnet", "subnet_id": "..."}
      {"type": "tag", "tag": "wifi-guest"}
      {"type": "custom_field", "field": "role", "value": "monitoring"}
    """

    __tablename__ = "opnsense_alias_mappings"

    firewall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opnsense_firewalls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias_name: Mapped[str] = mapped_column(String(64), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(32), default="host", nullable=False)
    selector: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]

    direction: Mapped[str] = mapped_column(String(8), default="push", nullable=False)
    # push  = jt-ipam 寫到 OPNsense
    # pull  = 把 OPNsense alias 讀回 jt-ipam（紀錄用）
    # both

    last_alias_uuid: Mapped[str | None] = mapped_column(String(64))   # OPNsense alias uuid
    last_synced_count: Mapped[int | None] = mapped_column(Integer)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "firewall_id", "alias_name", name="opnsense_alias_mapping_unique",
        ),
    )


class OPNsenseSyncedAlias(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """從 OPNsense 拉回來的 alias 定義（唯讀檢視用）。

    與 OPNsenseAliasMapping（jt-ipam→OPNsense 的推送規則）不同，這張表是把
    OPNsense 上「實際存在的 alias」同步回來，方便在 jt-ipam 內查閱。
    """

    __tablename__ = "opnsense_synced_aliases"

    firewall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opnsense_firewalls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    alias_type: Mapped[str | None] = mapped_column(String(32))   # host / network / port / url …
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    content: Mapped[list[str] | None] = mapped_column(JSONB)      # 成員列表
    member_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opn_uuid: Mapped[str | None] = mapped_column(String(64))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("firewall_id", "name", name="opnsense_synced_alias_unique"),
    )


class OPNsenseRuleLabel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """從 OPNsense pf_statistics 解析出的「規則 label → 引用的 alias」對照。

    filterlog 每筆封包帶穩定的 `rid`（=pf 規則 label，使用者規則為 UUID、外掛/自動
    規則為 md5）。把 label→alias 名做成 Graylog DSV，log 就能由 rid 反查出是哪個
    alias / 規則命中。資料來源：/api/diagnostics/firewall/pf_statistics/rules。
    """

    __tablename__ = "opnsense_rule_labels"

    firewall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opnsense_firewalls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)   # rid（uuid/md5）
    action: Mapped[str | None] = mapped_column(String(8))            # pass / block
    interface: Mapped[str | None] = mapped_column(String(64))
    alias_names: Mapped[list[str] | None] = mapped_column(JSONB)     # 引用的 alias 名
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("firewall_id", "label", name="opnsense_rule_label_unique"),
    )
