"""phpIPAM 遷移對映表。

每次同步把 phpIPAM legacy_id ↔ jt-ipam UUID 記下來，並存當下這筆資料的
SHA-256 hash；下次同步比對 hash 就能判斷有無變化，避免重做。

於是工具可以：
- 多次執行（idempotent）
- 偵測 phpIPAM 端的更新並覆寫 jt-ipam（同步模式）
- 偵測 phpIPAM 端的刪除（mapping 存在但抓不到）
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PhpIPAMMigrationMapping(Base):
    __tablename__ = "phpipam_migration_mapping"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    object_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    legacy_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    jt_ipam_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # 上次同步時 phpIPAM row 的 sha256 hash（canonical JSON）
    last_synced_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # phpIPAM 端最後出現時間（未抓到 → 上次出現）；用於偵測刪除
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("object_type", "legacy_id", name="phpipam_mapping_unique"),
    )
