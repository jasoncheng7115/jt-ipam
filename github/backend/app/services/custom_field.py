"""自訂欄位驗證服務。

對 Section/Subnet/IPAddress/Device 寫入時呼叫，依 object_type 取出該類別的
所有定義，逐一檢查必填、型別、regex、select 選項。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.custom_field import CustomFieldDefinition

ObjectType = Literal["section", "subnet", "ip", "device"]


class CustomFieldError(ValueError):
    pass


def _coerce(value: Any, field_type: str) -> Any:
    """型別 coerce；不合法直接 raise CustomFieldError。"""
    if field_type == "text":
        if not isinstance(value, str):
            raise CustomFieldError(f"expected text, got {type(value).__name__}")
        return value
    if field_type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise CustomFieldError("expected int") from exc
        return value
    if field_type == "float":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise CustomFieldError("expected float") from exc
        return float(value)
    if field_type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false", "1", "0"}:
            return value.lower() in {"true", "1"}
        raise CustomFieldError("expected bool")
    if field_type == "date":
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, str):
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise CustomFieldError("expected ISO date YYYY-MM-DD") from exc
            return value
        raise CustomFieldError("expected date string")
    if field_type in ("select", "regex"):
        if not isinstance(value, str):
            raise CustomFieldError(f"expected string for {field_type}")
        return value
    if field_type == "multi_select":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise CustomFieldError("expected list of strings")
        return value
    raise CustomFieldError(f"unknown field_type: {field_type}")


async def validate_custom_fields(
    session: AsyncSession,
    *,
    object_type: ObjectType,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """依 object_type 的所有 CustomFieldDefinition 驗證 payload。

    回傳 normalised payload（型別 coerce 過、未知 key 已濾除）。
    A03：不接受未定義 key。
    """
    payload = payload or {}

    rows = list(
        (
            await session.execute(
                select(CustomFieldDefinition).where(
                    CustomFieldDefinition.object_type == object_type
                )
            )
        )
        .scalars()
        .all()
    )
    by_name = {r.name: r for r in rows}

    # 拒絕未定義 key
    unknown = [k for k in payload if k not in by_name]
    if unknown:
        raise CustomFieldError(f"unknown custom fields: {unknown}")

    out: dict[str, Any] = {}
    for defn in rows:
        if defn.name not in payload or payload[defn.name] is None:
            if defn.required:
                raise CustomFieldError(f"required custom field missing: {defn.name}")
            continue

        value = _coerce(payload[defn.name], defn.field_type)

        # select / multi_select 限定 options.choices
        if defn.field_type in ("select", "multi_select"):
            choices = (defn.options or {}).get("choices") or []
            if not isinstance(choices, list):
                raise CustomFieldError(f"{defn.name}: options.choices must be a list")
            if defn.field_type == "select":
                if value not in choices:
                    raise CustomFieldError(f"{defn.name}: {value!r} not in allowed choices")
            else:
                bad = [v for v in value if v not in choices]
                if bad:
                    raise CustomFieldError(f"{defn.name}: {bad!r} not in allowed choices")

        # validation_regex（text / regex / select 等都可掛）
        if defn.validation_regex and defn.field_type in ("text", "regex"):
            try:
                pattern = re.compile(defn.validation_regex)
            except re.error as exc:
                raise CustomFieldError(f"{defn.name}: invalid regex on definition") from exc
            if isinstance(value, str) and not pattern.match(value):
                raise CustomFieldError(f"{defn.name}: value does not match required pattern")

        out[defn.name] = value

    return out
