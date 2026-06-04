"""共用 schema base：嚴格輸入驗證（A03）。"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """禁止額外欄位（A03 — 防止使用者塞奇怪欄位繞過驗證）。"""

    model_config = ConfigDict(
        extra="forbid",
        strict=False,  # strict=True 會讓 "1" → 1 失敗；用個別欄位的 Annotated 控制
        str_strip_whitespace=True,
        from_attributes=True,
    )


T = TypeVar("T")


class Paginated(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
