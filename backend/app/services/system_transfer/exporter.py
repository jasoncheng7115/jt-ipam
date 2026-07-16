"""組出匯出包的 inner payload。

以 SQLAlchemy Core 逐表 select，把每列轉成 JSON-safe dict（bytes→b64、其餘型別→str/原樣），
機密欄位交給 secrets 模組解密成明文。system_settings 值內嵌機密另做 transform。
"""

from __future__ import annotations

import base64
import datetime as _dt
import ipaddress
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import LargeBinary, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.system_transfer import registry, secrets


def _json_safe(value: Any) -> Any:
    """把單一欄位值轉成 JSON 可序列化型別（bytes 由呼叫端另行 b64，不進這裡）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (uuid.UUID,)):
        return str(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address,
                          ipaddress.IPv4Network, ipaddress.IPv6Network,
                          ipaddress.IPv4Interface, ipaddress.IPv6Interface)):
        return str(value)
    if isinstance(value, (list, tuple)):
        # 遞迴正規化：array 欄位（如 uuid[] scope_subnet_ids）元素是 UUID 物件，
        # JSONB 內也可能巢狀非 JSON-native 值 —— 一律轉成 JSON-native，讓匯出檔可正確 round-trip。
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return str(value)  # MACADDR 等 → 字串


async def _dump_table(session: AsyncSession, name: str) -> list[dict[str, Any]]:
    table = registry.table_by_name(name)
    binary_cols = {c.name for c in table.columns if isinstance(c.type, LargeBinary)}
    result = await session.execute(select(table))
    rows: list[dict[str, Any]] = []
    for m in result.mappings():
        row: dict[str, Any] = {}
        for col_name, val in m.items():
            if val is not None and col_name in binary_cols:
                row[col_name] = base64.b64encode(secrets._as_bytes(val)).decode("ascii")
            else:
                row[col_name] = _json_safe(val)
        # 機密欄位：解密成明文、移除密文欄位
        col_sec = secrets.strip_column_secrets(name, row)
        env_sec = secrets.strip_envelope_secrets(name, row)
        merged = {**(col_sec or {}), **(env_sec or {})}
        if merged:
            row["__secrets__"] = merged
        # system_settings 內嵌機密
        if name == "system_settings":
            row["value"] = secrets.transform_settings_out(row.get("key"), row.get("value"))
        rows.append(row)
    return rows


async def build_export(session: AsyncSession, scope: list[str]) -> dict[str, Any]:
    """回 inner dict：{tables:{name:[rows]}, central_secrets:[...], counts:{name:n}}。"""
    names = registry.tables_for_scope(scope)
    tables: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    central: list[dict[str, Any]] = []
    for name in names:
        if name == registry.ENCRYPTED_SECRETS_TABLE:
            table = registry.table_by_name(name)
            result = await session.execute(select(table))
            central = [secrets.export_central_row(dict(m)) for m in result.mappings()]
            counts[name] = len(central)
            continue
        rows = await _dump_table(session, name)
        tables[name] = rows
        counts[name] = len(rows)
    return {"tables": tables, "central_secrets": central, "counts": counts}
