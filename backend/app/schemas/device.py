"""Device schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from app.schemas.base import StrictModel

_VALID_TYPES = {"server", "switch", "router", "firewall", "ap", "storage", "ipmi",
                "patch_panel", "pdu", "ups", "workstation", "other"}


class DeviceBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    fqdn: Annotated[str | None, Field(max_length=255)] = None
    type: str = "other"
    vendor: Annotated[str | None, Field(max_length=64)] = None
    model: Annotated[str | None, Field(max_length=64)] = None
    serial: Annotated[str | None, Field(max_length=128)] = None
    location_id: uuid.UUID | None = None
    rack_id: uuid.UUID | None = None
    u_position: Annotated[int | None, Field(ge=1, le=99)] = None
    u_size: Annotated[int | None, Field(ge=1, le=99)] = None
    rack_face: Literal["front", "rear"] | None = None
    # 橫向格位（issue #31）：起始格 + 跨幾格，網格 12 格。預設 (0,12) 即整 U 全寬。
    rack_slot: int = Field(0, ge=0, le=59)
    rack_slot_span: int = Field(60, ge=1, le=60)
    # 一層之內的垂直位置（層架才用得到）：0 貼著層板，往上長。整層佔滿＝(0, 60)
    rack_vslot: int = Field(0, ge=0, le=59)
    rack_vslot_span: int = Field(60, ge=1, le=60)
    description: Annotated[str | None, Field(max_length=1024)] = None
    customer_id: uuid.UUID | None = None
    custom_fields: dict[str, Any] | None = None

    @field_validator("type")
    @classmethod
    def _type_valid(cls, v: str) -> str:
        if v not in _VALID_TYPES:
            raise ValueError(f"type must be one of {sorted(_VALID_TYPES)}")
        return v


class DeviceCreate(DeviceBase):
    primary_ip_id: uuid.UUID | None = None


class DeviceUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    fqdn: Annotated[str | None, Field(max_length=255)] = None
    type: str | None = None
    vendor: Annotated[str | None, Field(max_length=64)] = None
    model: Annotated[str | None, Field(max_length=64)] = None
    serial: Annotated[str | None, Field(max_length=128)] = None
    location_id: uuid.UUID | None = None
    rack_id: uuid.UUID | None = None
    u_position: Annotated[int | None, Field(ge=1, le=99)] = None
    u_size: Annotated[int | None, Field(ge=1, le=99)] = None
    rack_face: Literal["front", "rear"] | None = None
    rack_slot: int | None = Field(None, ge=0, le=59)
    rack_slot_span: int | None = Field(None, ge=1, le=60)
    rack_vslot: int | None = Field(None, ge=0, le=59)
    rack_vslot_span: int | None = Field(None, ge=1, le=60)
    description: Annotated[str | None, Field(max_length=1024)] = None
    primary_ip_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    custom_fields: dict[str, Any] | None = None

    @field_validator("type")
    @classmethod
    def _type_valid(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in _VALID_TYPES:
            raise ValueError(f"type must be one of {sorted(_VALID_TYPES)}")
        return v


class DeviceRead(DeviceBase):
    """讀取用 —— **刻意放寬長度與範圍限制**。

    由來（2026-09-05 客戶回報）：裝置清單整頁 Internal Server Error、一筆都不顯示，
    儀表板卻數得出 55 台。原因是這個 schema 繼承了 `DeviceBase` 的寫入限制
    （vendor/model ≤ 64 字、u_position 1–99），但資料庫裡這些欄位是 **text 與無約束的
    integer** —— 整合（LibreNMS／Proxmox 等）同步進來的值本來就可能更長或超出範圍。
    於是**一列不合規就讓整頁 500**，而且畫面上完全看不出是哪一筆。

    寫入端的限制留著（表單該擋就擋）；讀取端則接受資料庫容得下的東西 ——
    已經在資料庫裡的資料，讀不出來不是資料的錯，是我們的問題。
    型別允許清單維持嚴格：`type` 在資料庫有 CHECK 約束，不可能出現非法值。
    """

    # 這些欄位在資料庫是 text / 無約束 integer：讀取時不再套寫入用的上限
    name: str
    fqdn: str | None = None
    vendor: str | None = None
    model: str | None = None
    serial: str | None = None
    description: str | None = None
    u_position: int | None = None
    u_size: int | None = None

    id: uuid.UUID
    # 類型是誰定的（auto＝依作業系統／機殼自動判斷）；畫面可據此標「自動判斷」
    type_source: str | None = None
    primary_ip_id: uuid.UUID | None
    ip: str | None = None   # 由 endpoint 解析 primary_ip_id 後填入（清單顯示用）
    ip_address_id: str | None = None   # 有對應的 IPAddress → IP 欄可點進該位址
    ip_match_id: str | None = None   # 有相符但未連結的 IPAddress → 可一鍵關聯
    # 裝置群組（堆疊／HA／叢集）：{id, name, kind, role, order, member_count, representative}
    group: dict[str, Any] | None = None
    # 這台是虛擬機還是實體機。**推導而非欄位**：虛擬機清單本來就是各平台同步進來的，
    # 多開一個欄位只會多一份要人維護、而且會過期的真相。名稱對得到 virtual_machines
    # 就是虛擬 —— 與關係圖用來接 VM 的判斷同一套。
    is_virtual: bool = False
    # 虛擬化對應明細（顯示用）：{vm, cluster, platform}；None＝比對不到，
    # **不代表實體機**（整合可能沒涵蓋）
    virt_vm: dict | None = None
    created_at: datetime
    updated_at: datetime
