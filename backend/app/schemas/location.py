"""Location / Rack schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from app.schemas.base import StrictModel


class LocationBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    address: Annotated[str | None, Field(max_length=512)] = None
    latitude: Annotated[float | None, Field(ge=-90, le=90)] = None
    longitude: Annotated[float | None, Field(ge=-180, le=180)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    customer_id: uuid.UUID | None = None   # 所屬單位


class LocationCreate(LocationBase):
    pass


class LocationUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    address: Annotated[str | None, Field(max_length=512)] = None
    latitude: Annotated[float | None, Field(ge=-90, le=90)] = None
    longitude: Annotated[float | None, Field(ge=-180, le=180)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    customer_id: uuid.UUID | None = None


class LocationRead(LocationBase):
    id: uuid.UUID
    floor_plan_path: str | None = None
    created_at: datetime
    updated_at: datetime
    rack_count: int = 0      # 其下機櫃數（清單顯示用）
    device_count: int = 0    # 其下裝置數（清單顯示用）
    customer_name: str | None = None   # 所屬單位名稱（清單顯示用）


RackNumbering = Literal["top-down", "bottom-up"]
RackFace = Literal["front", "rear"]
RackKind = Literal["rack", "industrial", "shelf", "wire_shelf", "wood_shelf",
                   "angle_shelf", "kallax", "lackrack"]
#: 表面顏色（各型態可用的見 services/rack.py 的 FINISHES；不適用的值畫圖時用該型態預設色）
RackFinish = Literal["black", "white", "galvanized", "black_brown", "oak", "brown"]


class RackBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    location_id: uuid.UUID | None = None
    u_height: Annotated[int, Field(ge=1, le=99)] = 42
    # 實體尺寸（mm）；機房平面圖用真實腳印按比例畫機櫃方框
    # issue #30：rack＝標準 19" 機櫃（以 U 計）、shelf＝層架（以「層」計）
    kind: RackKind = "rack"
    finish: RackFinish | None = None
    width_mm: Annotated[int | None, Field(ge=100, le=2000)] = None
    row_height_mm: Annotated[int | None, Field(ge=10, le=1000)] = None
    # 逐層高度（mm），由第 1 層起算。層架的層板一層一層各自可調；
    # 不給就整台用 row_height_mm。長度不必剛好等於層數，少的用 row_height_mm 補。
    level_heights: Annotated[list[Annotated[int, Field(ge=10, le=1000)]] | None,
                             Field(max_length=99)] = None
    # 層板厚度（mm）。層高填的是**淨空高**（不含板），總高要另外把板算進去。
    # null = 依 kind 取預設（木質層架 18mm＝IKEA IVAR 官方規格）。
    board_mm: Annotated[int | None, Field(ge=0, le=200)] = None
    # 最下面那片層板離地多高（mm）。null = 0。
    floor_mm: Annotated[int | None, Field(ge=0, le=1000)] = None
    depth_mm: Annotated[int | None, Field(ge=100, le=3000)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    seq: Annotated[int | None, Field(ge=0, le=9999)] = None   # 排序編號（小的排左邊）
    numbering: RackNumbering = "top-down"
    face: RackFace = "front"
    #: 對外公開這個機櫃的示意圖（給別的系統嵌入）。預設關 —— 機櫃圖會揭露裝置名稱與位置
    expose_svg: bool = False


class RackCreate(RackBase):
    pass


class RackUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    location_id: uuid.UUID | None = None
    u_height: Annotated[int | None, Field(ge=1, le=99)] = None
    kind: RackKind | None = None
    finish: RackFinish | None = None
    width_mm: Annotated[int | None, Field(ge=100, le=2000)] = None
    row_height_mm: Annotated[int | None, Field(ge=10, le=1000)] = None
    # 逐層高度（mm），由第 1 層起算。層架的層板一層一層各自可調；
    # 不給就整台用 row_height_mm。長度不必剛好等於層數，少的用 row_height_mm 補。
    level_heights: Annotated[list[Annotated[int, Field(ge=10, le=1000)]] | None,
                             Field(max_length=99)] = None
    # 層板厚度（mm）。層高填的是**淨空高**（不含板），總高要另外把板算進去。
    # null = 依 kind 取預設（木質層架 18mm＝IKEA IVAR 官方規格）。
    board_mm: Annotated[int | None, Field(ge=0, le=200)] = None
    # 最下面那片層板離地多高（mm）。null = 0。
    floor_mm: Annotated[int | None, Field(ge=0, le=1000)] = None
    depth_mm: Annotated[int | None, Field(ge=100, le=3000)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    seq: Annotated[int | None, Field(ge=0, le=9999)] = None
    numbering: RackNumbering | None = None
    face: RackFace | None = None
    expose_svg: bool | None = None
    pos_x: Annotated[float | None, Field(ge=0, le=1)] = None
    pos_y: Annotated[float | None, Field(ge=0, le=1)] = None


class RackRead(RackBase):
    id: uuid.UUID
    pos_x: float | None = None
    pos_y: float | None = None
    pos_rot: int = 0
    pos_w: float | None = None
    pos_h: float | None = None
    device_count: int = 0    # 其下裝置數（清單顯示用）
    location_name: str | None = None   # 所屬機房/地點名稱（清單顯示用）
    created_at: datetime
    updated_at: datetime


class RackPosition(StrictModel):
    """機房平面圖上單一機櫃的座標（0..1 比例）+ 旋轉角度 + 方框大小。"""

    id: uuid.UUID
    pos_x: Annotated[float, Field(ge=0, le=1)]
    pos_y: Annotated[float, Field(ge=0, le=1)]
    pos_rot: Annotated[int, Field(ge=0, le=359)] = 0
    pos_w: Annotated[float | None, Field(ge=0.01, le=1)] = None
    pos_h: Annotated[float | None, Field(ge=0.01, le=1)] = None


class RackPositionsUpdate(StrictModel):
    positions: Annotated[list[RackPosition], Field(max_length=500)]
    # 載入時拿到的 /layout revision：跟目前不一致（別人剛存過）→ 409 floorplan_conflict。不帶＝舊用戶端，照常存
    revision: Annotated[str | None, Field(max_length=64)] = None
