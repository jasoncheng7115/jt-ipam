"""IP 申請工作流 model。

phpIPAM 缺點：多階段表單繁瑣、狀態追蹤差、無 timeline。
jt-ipam 設計：清楚狀態機 + IPRequestEvent 串成 timeline。

狀態：
  pending → approved (atomic with allocate_ip → fulfilled)
  pending → rejected
  pending → cancelled (by requester)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class IPRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ip_requests"

    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)

    requester_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    approver_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )

    subnet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subnets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_ip: Mapped[str | None] = mapped_column(INET)
    hostname: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    allocated_ip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_addresses.id", ondelete="SET NULL"),
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','cancelled','fulfilled')",
            name="ck_ip_requests_status_valid",
        ),
    )


class IPRequestEvent(Base, UUIDPrimaryKeyMixin):
    """timeline 事件，每次狀態變化記一筆。"""

    __tablename__ = "ip_request_events"

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


class IPRequestStageApproval(Base, UUIDPrimaryKeyMixin):
    """多關卡審核：每個關卡（step_index）被核准時記一筆。

    sequential（stages）模式：依序，step 0,1,2… 全數核准才完成。
    parallel（會簽）模式：不分先後，所有 step 都被核准才完成。
    （admin / designated 單關卡模式不用此表，approve 即配發。）
    """

    __tablename__ = "ip_request_stage_approvals"
    __table_args__ = (UniqueConstraint("request_id", "step_index", name="uq_ip_req_stage_step"),)

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ip_requests.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    approver_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
