"""裝置匯入（issue #46：裝置清單只能匯出、不能匯入）。

目標是「匯出改完能直接匯回去」：
- 吃 CSV（自動判斷分隔符號、容忍 BOM）與 .xlsx（第一張工作表）
- 欄位看檔頭，順序不重要；認得標準欄名，也認得**清單匯出的中／英／日檔頭**
  （例如「名稱／製造商／地點／機櫃／單位」），不管匯出時介面是哪種語言
- 類型、安裝方向同樣接受代碼或三種語言的顯示文字
- 地點、機櫃、單位用**名稱**對應（匯出與範本都寫名稱）；機櫃名稱只在同一地點內唯一，
  沒寫地點又同名時不猜，要求補上地點；只寫機櫃時地點由機櫃推出來
- IP 只對應 IPAM 既有的位址（唯一才算），不新建；已經屬於別台裝置的 IP 不搶過來
- 已存在的裝置（名稱相同，不分大小寫）：`skip` 略過、`update` 只更新有填的欄位（空白＝不變）
- 驗證與 API 新增／編輯同一套（schema 長度與類型、機櫃 U 位不可重疊），預覽也跑完整流程，
  最後還原不寫入 —— 預覽說可以的，實際匯入就可以

回報的訊息是代碼＋參數（前端 `device_import.msg.<code>` 翻譯）。
"""
from __future__ import annotations

import csv
import io
import ipaddress
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.device import Device
from app.models.location import Location, Rack
from app.schemas.device import DeviceCreate, DeviceUpdate

MAX_ROWS = 5000
FIELDS = ("name", "fqdn", "ip", "type", "vendor", "model", "serial", "location", "rack",
          "u_position", "u_size", "rack_face", "unit", "description")

# 檔頭 → 欄位。標準欄名＋清單匯出在三種語言下的檔頭（與前端語言檔一致，有守門測試比對）。
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "device", "device_name", "名稱", "名称"),
    "fqdn": ("fqdn",),
    "ip": ("ip", "primary_ip", "ip_address", "ip address"),
    "type": ("type", "類型", "種別"),
    "vendor": ("vendor", "manufacturer", "製造商", "ベンダー"),
    "model": ("model", "型號", "型番"),
    "serial": ("serial", "serial_number", "序號", "シリアル番号"),
    "location": ("location", "location_id", "site", "地點", "設置場所"),
    "rack": ("rack", "rack_id", "機櫃", "ラック"),
    "u_position": ("u_position", "u", "u 位 (起始)", "u position (start)", "u 位置（開始）"),
    "u_size": ("u_size", "佔用 u 數", "u occupied", "占有 u 数"),
    "rack_face": ("rack_face", "安裝方向", "mount face", "取り付け面"),
    "unit": ("unit", "customer", "customer_id", "customers", "單位", "単位", "顧客"),
    "description": ("description", "說明", "説明", "描述", "備註", "note"),
}
# 清單匯出會帶、但不是可以寫回去的欄位（虛實是推算出來的）：認得、略過，不當成「不認得的欄位」
IGNORED_HEADERS = ("is_virtual", "虛實", "kind", "区分", "id")

TYPE_LABELS: dict[str, tuple[str, ...]] = {
    "server": ("伺服器", "サーバー"), "switch": ("交換器", "スイッチ"), "router": ("路由器", "ルーター"),
    "firewall": ("防火牆", "ファイアウォール"),
    "ap": ("無線基地台 (ap)", "access point (ap)", "アクセスポイント（ap）", "access point"),
    "storage": ("儲存設備", "ストレージ"), "ipmi": ("ipmi / bmc", "bmc"),
    "patch_panel": ("配線架", "patch panel", "パッチパネル"),
    "workstation": ("工作站", "workstation", "ワークステーション", "pc", "desktop", "laptop", "notebook",
                    "電腦", "桌機", "筆電"),
    "pdu": ("電源分配器 (pdu)",), "ups": ("不斷電系統 (ups)",), "other": ("其他", "その他"),
}
FACE_LABELS: dict[str, tuple[str, ...]] = {
    "front": ("front", "機櫃前面", "rack front", "ラック前面", "前面", "前"),
    "rear": ("rear", "back", "機櫃後面", "rack rear", "ラック背面", "後面", "背面", "後"),
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("　", " ")).strip().lower()


_HEADER_MAP = {_norm(a): k for k, aliases in HEADER_ALIASES.items() for a in aliases}
_IGNORED = {_norm(h) for h in IGNORED_HEADERS}
_TYPE_MAP = {**{t: t for t in TYPE_LABELS}, **{_norm(lab): t for t, labs in TYPE_LABELS.items() for lab in labs}}
_FACE_MAP = {_norm(lab): f for f, labs in FACE_LABELS.items() for lab in labs}
_EMPTY = {"", "—", "-", "–"}


@dataclass
class RowResult:
    line: int
    name: str | None
    action: str = "error"              # create / update / skip / error
    messages: list[dict[str, Any]] = field(default_factory=list)

    def msg(self, code: str, **params: Any) -> None:
        self.messages.append({"code": code, "params": params})

    def to_dict(self) -> dict[str, Any]:
        return {"line": self.line, "name": self.name, "action": self.action, "messages": self.messages}


@dataclass
class ImportOutcome:
    rows: list[RowResult] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)
    fatal: dict[str, Any] | None = None
    created_ids: list[uuid.UUID] = field(default_factory=list)
    updated_ids: list[uuid.UUID] = field(default_factory=list)

    def count(self, action: str) -> int:
        return sum(1 for r in self.rows if r.action == action)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": len(self.rows), "created": self.count("create"), "updated": self.count("update"),
            "skipped": self.count("skip"), "errored": self.count("error"),
            "warnings": sum(1 for r in self.rows for m in r.messages if m["code"].startswith("warn_")),
            "ignored_columns": self.ignored_columns, "fatal": self.fatal,
            "rows": [r.to_dict() for r in self.rows[:1000]],
        }


# ── 讀檔 ────────────────────────────────────────────────────────────────────

def read_table(raw: bytes) -> list[list[str]]:
    """CSV 或 .xlsx → 每列一個字串清單（第一列是檔頭）。"""
    from app.services.xlsx_read import is_xlsx, read_first_sheet
    if is_xlsx(raw):
        return read_first_sheet(raw, max_rows=MAX_ROWS + 1)
    text = raw.decode("utf-8-sig")
    sample = text[:4096]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)]


def _cell(values: dict[str, str], key: str) -> str | None:
    v = values.get(key)
    if v is None:
        return None
    v = v.strip()
    # 匯出時的公式注入防護會在 = + - @ 開頭的值前面加單引號：匯回來時拿掉
    if len(v) > 1 and v[0] == "'" and v[1] in "=+-@":
        v = v[1:]
    return None if v in _EMPTY else v


# ── 對應名稱 ────────────────────────────────────────────────────────────────

class _Lookup:
    """一次匯入裡重複查的東西先讀進來（地點、機櫃、單位都不會多）。"""

    def __init__(self) -> None:
        self.locations: dict[str, list[Location]] = {}
        self.racks: dict[str, list[Rack]] = {}
        self.customers: dict[str, list[Customer]] = {}

    async def load(self, session: AsyncSession) -> None:
        for loc in (await session.execute(select(Location))).scalars():
            self.locations.setdefault(_norm(loc.name), []).append(loc)
        for rk in (await session.execute(select(Rack))).scalars():
            self.racks.setdefault(_norm(rk.name), []).append(rk)
        for c in (await session.execute(select(Customer))).scalars():
            self.customers.setdefault(_norm(c.name), []).append(c)
            if c.title and _norm(c.title) != _norm(c.name):
                self.customers.setdefault(_norm(c.title), []).append(c)


async def _resolve_ip(session: AsyncSession, text: str, res: RowResult) -> Any | None:
    from app.services.ip_autocreate import match_existing
    try:
        ip_text = str(ipaddress.ip_address(text.split("/")[0].strip()))
    except ValueError:
        res.msg("bad_ip", value=text)
        return None
    ipa, ambiguous = await match_existing(session, ip_text)
    if ipa is None:
        res.msg("warn_ip_ambiguous" if ambiguous else "warn_ip_not_found", ip=ip_text)
    return ipa


def _int(text: str | None, field_name: str, res: RowResult) -> int | None:
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        res.msg("bad_number", field=field_name, value=text)
        return None


# ── 匯入 ────────────────────────────────────────────────────────────────────

async def import_devices(session: AsyncSession, rows: list[list[str]], *, on_existing: str = "skip",
                         dry_run: bool = True, audit: Any = None) -> ImportOutcome:
    """匯入裝置。`dry_run=True` 走完整流程（含寫入與 U 位檢查）後還原，不留任何變更。

    `audit(obj, action, diff)`：實際匯入時每台裝置寫一筆稽核（由端點提供，帶操作者資訊）。
    """
    from app.services.device_write import PlacementError, check_placement, link_primary_ip

    out = ImportOutcome()
    if not rows or not any(c.strip() for c in rows[0]):
        out.fatal = {"code": "empty_file"}
        return out
    header = rows[0]
    col_of: dict[int, str] = {}
    for i, h in enumerate(header):
        key = _HEADER_MAP.get(_norm(h))
        if key and key not in col_of.values():
            col_of[i] = key
        elif _norm(h) and _norm(h) not in _IGNORED and not key:
            out.ignored_columns.append(h.strip())
    if "name" not in col_of.values():
        out.fatal = {"code": "no_name_column"}
        return out
    body = rows[1:]
    if len(body) > MAX_ROWS:
        out.fatal = {"code": "too_many_rows", "params": {"max": MAX_ROWS}}
        return out

    lk = _Lookup()
    await lk.load(session)
    seen_names: set[str] = set()

    for n, raw_row in enumerate(body, start=2):
        if not any((c or "").strip() for c in raw_row):
            continue
        values = {col_of[i]: v for i, v in enumerate(raw_row) if i in col_of}
        name = _cell(values, "name")
        res = RowResult(line=n, name=name)
        out.rows.append(res)
        if not name:
            res.msg("missing_name")
            continue
        if _norm(name) in seen_names:
            res.msg("duplicate_in_file")
            continue
        seen_names.add(_norm(name))

        existing = list((await session.execute(select(Device).where(
            func.lower(Device.name) == name.lower()).limit(2))).scalars().all())
        if len(existing) > 1:
            res.msg("name_ambiguous")
            continue
        dev = existing[0] if existing else None
        if dev is not None and on_existing != "update":
            res.action = "skip"
            res.msg("exists_skipped")
            continue

        data: dict[str, Any] = {}
        for key in ("fqdn", "vendor", "model", "serial", "description"):
            v = _cell(values, key)
            if v is not None:
                data[key] = v
        if dev is None:
            data["name"] = name
        if (t := _cell(values, "type")) is not None:
            code = _TYPE_MAP.get(_norm(t))
            if code is None:
                res.msg("bad_type", value=t)
            else:
                data["type"] = code
        if (f := _cell(values, "rack_face")) is not None:
            face = _FACE_MAP.get(_norm(f))
            if face is None:
                res.msg("bad_rack_face", value=f)
            else:
                data["rack_face"] = face
        for key in ("u_position", "u_size"):
            num = _int(_cell(values, key), key, res)
            if num is not None:
                data[key] = num

        loc_name, rack_name = _cell(values, "location"), _cell(values, "rack")
        loc: Location | None = None
        if loc_name is not None:
            cands = lk.locations.get(_norm(loc_name), [])
            if not cands:
                res.msg("location_not_found", value=loc_name)
            else:
                loc = cands[0]
                data["location_id"] = loc.id
        if rack_name is not None:
            cands = lk.racks.get(_norm(rack_name), [])
            if loc is not None:
                cands = [r for r in cands if r.location_id == loc.id]
            if not cands:
                res.msg("rack_not_in_location" if loc is not None and lk.racks.get(_norm(rack_name))
                        else "rack_not_found", value=rack_name, location=loc_name or "")
            elif len(cands) > 1:
                res.msg("rack_ambiguous", value=rack_name)
            else:
                data["rack_id"] = cands[0].id
                if loc is None and cands[0].location_id:
                    data["location_id"] = cands[0].location_id   # 只寫機櫃：地點由機櫃推出來
        if (u := _cell(values, "unit")) is not None:
            cands = lk.customers.get(_norm(u), [])
            uniq = {c.id: c for c in cands}
            if not uniq:
                res.msg("unit_not_found", value=u)
            elif len(uniq) > 1:
                res.msg("unit_ambiguous", value=u)
            else:
                data["customer_id"] = next(iter(uniq))
        ipa = None
        if (ip := _cell(values, "ip")) is not None:
            ipa = await _resolve_ip(session, ip, res)
            if ipa is not None:
                if ipa.device_id is not None and (dev is None or ipa.device_id != dev.id):
                    res.msg("warn_ip_linked_elsewhere", ip=str(ipa.ip).split("/")[0])
                    ipa = None
                else:
                    data["primary_ip_id"] = ipa.id
        if any(not m["code"].startswith("warn_") for m in res.messages):
            continue                         # 有錯就整列不寫（不做半套）

        # 跟 API 一樣的 schema 驗證（長度、類型、範圍）
        try:
            if dev is None:
                DeviceCreate(**data)
            else:
                DeviceUpdate(**data)
        except ValidationError as exc:
            err = exc.errors()[0]
            res.msg("invalid", field=".".join(str(x) for x in err.get("loc", ())) or "?",
                    reason=str(err.get("msg", ""))[:200])
            continue

        obj = dev or Device(name=name, type=data.get("type", "other"))
        if "type" in data and data["type"] != "other":
            obj.type_source = "import"     # 檔案裡明確給了類型 → 自動判斷不碰
        before = None if dev is None else {k: getattr(dev, k) for k in data}
        for k, v in data.items():
            setattr(obj, k, v)
        try:
            await check_placement(session, obj, exclude_device_id=obj.id if dev is not None else None)
        except PlacementError as exc:
            if dev is not None:
                for k, v in (before or {}).items():
                    setattr(dev, k, v)
            res.msg("placement", reason=str(exc)[:200])
            continue
        if dev is None:
            session.add(obj)
        await session.flush()                # 後面的列做 U 位檢查時要看得到這一台
        from app.services.device_groups import GroupError
        try:
            await link_primary_ip(session, obj)
        except GroupError as exc:
            # 主要 IP 是別的裝置群組共用的：不搶，這一台不設主要 IP，列在結果裡
            obj.primary_ip_id = None
            res.msg("ip_owned_by_group", ip=exc.params.get("ip", ""), group=exc.params.get("group", ""))
        res.action = "create" if dev is None else "update"
        (out.created_ids if dev is None else out.updated_ids).append(obj.id)
        if audit is not None and not dry_run:
            await audit(obj, res.action, {
                "source": "device_import",
                **({"after": {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in data.items()}}
                   if dev is None else
                   {"changes": {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in data.items()}}),
            })

    if dry_run:
        await session.rollback()
    return out


# ── 範本（含現有裝置）───────────────────────────────────────────────────────

def _csv_safe(value: str) -> str:
    """CSV 公式注入防護：= + - @ 開頭的值在 Excel 會被當公式 —— 前面加單引號（匯入時會拿掉）。"""
    if value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


async def template_csv(session: AsyncSession, *, include_devices: bool) -> str:
    """匯入範本：標準欄名；`include_devices` 時帶出全部現有裝置（改完用「更新」模式匯回來）。"""
    from app.models.address import IPAddress
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(FIELDS)
    if include_devices:
        locs = {loc.id: loc.name for loc in (await session.execute(select(Location))).scalars()}
        racks = {r.id: r.name for r in (await session.execute(select(Rack))).scalars()}
        custs = {c.id: c.name for c in (await session.execute(select(Customer))).scalars()}
        ips = {i.id: str(i.ip).split("/")[0] for i in (await session.execute(
            select(IPAddress).where(IPAddress.id.in_(select(Device.primary_ip_id).where(
                Device.primary_ip_id.is_not(None)))))).scalars()}
        for d in (await session.execute(select(Device).order_by(Device.name))).scalars():
            w.writerow([_csv_safe(str(x)) if x is not None else "" for x in (
                d.name, d.fqdn, ips.get(d.primary_ip_id) if d.primary_ip_id else None, d.type, d.vendor,
                d.model, d.serial, locs.get(d.location_id) if d.location_id else None,
                racks.get(d.rack_id) if d.rack_id else None, d.u_position, d.u_size, d.rack_face,
                custs.get(d.customer_id) if d.customer_id else None, d.description)])
    return buf.getvalue()
