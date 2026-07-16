"""系統匯出／匯入 API schema。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.base import StrictModel


class ExportRequest(StrictModel):
    scope: list[str] = Field(..., min_length=1, description="要匯出的分類（見 /schema 的 scopes）")
    passphrase: str = Field(..., min_length=8, description="保護匯出檔的密碼（匯入時需輸入同一組）")


class ImportApplyRequest(StrictModel):
    token: str = Field(..., description="/import/analyze 回傳的暫存 token")
    passphrase: str = Field(..., min_length=1)
    mode: Literal["merge", "replace"] = "merge"
    dry_run: bool = False
