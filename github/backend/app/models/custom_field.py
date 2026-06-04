"""自訂欄位定義。

存取流程：
- admin 在 /api/v1/custom-fields 定義欄位（object_type, name, field_type, ...)
- Section/Subnet/IPAddress/Device 寫入時，custom_fields jsonb 經 service 驗證
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CustomFieldDefinition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "custom_field_definitions"

    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    label_zh_tw: Mapped[str | None] = mapped_column(Text)
    label_en_us: Mapped[str | None] = mapped_column(Text)
    field_type: Mapped[str] = mapped_column(String(16), nullable=False)
    options: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    validation_regex: Mapped[str | None] = mapped_column(Text)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "object_type IN ('subnet','ip','device')",
            name="cf_object_type_valid",
        ),
        CheckConstraint(
            "field_type IN ('text','int','float','bool','date','select','multi_select','regex')",
            name="cf_field_type_valid",
        ),
        UniqueConstraint("object_type", "name", name="cf_object_name_uq"),
    )
