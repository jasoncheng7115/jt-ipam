"""套用匯出包的 inner payload 到目標機。

- 保留來源 UUID → 外鍵與機密 AAD 自動成立。
- merge：依主鍵 upsert（ON CONFLICT DO UPDATE），冪等。
- replace：先反相依序清空 in-scope 表（保護目前登入的 admin 那列，避免自斷 session），再 upsert。
- 每列包 SAVEPOINT（begin_nested）做錯誤隔離，單列失敗只計數不中斷整批。
- dry_run：全程照跑但最後 rollback，不落地（回預覽計數）。
- 向下相容：只寫「目標表存在的欄位」；匯出包多出的未知欄位忽略、缺的欄位吃預設。
"""

from __future__ import annotations

import base64
import datetime as _dt
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Date, DateTime, LargeBinary, Time, delete, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Uuid as GenericUUID

from app.services.system_transfer import registry, secrets


@dataclass
class TableResult:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errored: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted, "updated": self.updated,
            "skipped": self.skipped, "errored": self.errored,
            "errors": self.errors[:20],
        }


def _coerce(table, row: dict[str, Any]) -> dict[str, Any]:
    """依目標表欄位型別轉換值；只保留目標表存在的欄位（向下相容關鍵）。"""
    cols = table.columns
    out: dict[str, Any] = {}
    for name, val in row.items():
        if name not in cols:
            continue  # 未知欄位（新版匯出→舊實例）忽略
        col = cols[name]
        if val is None:
            out[name] = None
            continue
        ctype = col.type
        if isinstance(ctype, LargeBinary):
            out[name] = base64.b64decode(val) if isinstance(val, str) else val
        elif isinstance(ctype, (DateTime, Date, Time)):
            out[name] = _parse_temporal(val)
        elif isinstance(ctype, (PGUUID, GenericUUID)):
            out[name] = uuid.UUID(val) if isinstance(val, str) else val
        else:
            out[name] = val
    return out


def _parse_temporal(val: Any) -> Any:
    if not isinstance(val, str):
        return val
    try:
        return _dt.datetime.fromisoformat(val)
    except ValueError:
        try:
            return _dt.date.fromisoformat(val)
        except ValueError:
            return val


def _pk_cols(table) -> list[str]:
    return [c.name for c in table.primary_key.columns]


async def _existing_pks(session: AsyncSession, table) -> set:
    pk = list(table.primary_key.columns)
    if not pk:
        return set()
    result = await session.execute(select(*pk))
    if len(pk) == 1:
        return {r[0] for r in result.all()}
    return {tuple(r) for r in result.all()}


def _pk_value(table, coerced: dict[str, Any]):
    pk = _pk_cols(table)
    vals = tuple(coerced.get(c) for c in pk)
    return vals[0] if len(vals) == 1 else vals


async def _import_table(
    session: AsyncSession, name: str, rows: list[dict[str, Any]], *, mode: str,
) -> TableResult:
    table = registry.table_by_name(name)
    res = TableResult()
    existing = set() if mode == "replace" else await _existing_pks(session, table)
    pk_cols = _pk_cols(table)
    for raw in rows:
        raw = dict(raw)
        sec = raw.pop("__secrets__", None)
        try:
            coerced = _coerce(table, raw)
            if name == "system_settings":
                coerced["value"] = secrets.transform_settings_in(coerced.get("key"), coerced.get("value"))
            secrets.apply_column_secrets(name, coerced, sec)
            secrets.apply_envelope_secrets(name, coerced, sec)
            pkv = _pk_value(table, coerced)
            is_update = pkv in existing
            async with session.begin_nested():
                stmt = pg_insert(table).values(**coerced)
                update_cols = {c: stmt.excluded[c] for c in coerced if c not in pk_cols}
                if update_cols:
                    stmt = stmt.on_conflict_do_update(index_elements=pk_cols, set_=update_cols)
                else:
                    stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
                await session.execute(stmt)
            if is_update:
                res.updated += 1
            else:
                res.inserted += 1
        except Exception as exc:
            res.errored += 1
            if len(res.errors) < 20:
                res.errors.append(f"{name} pk={raw.get('id', raw.get('key', '?'))}: {type(exc).__name__}: {exc}")
    return res


async def _wipe(session: AsyncSession, names: list[str], *, protect_user_id: uuid.UUID | None) -> None:
    """反相依序清空 in-scope 表；users 表保留目前登入 admin 那列。每表獨立 SAVEPOINT。"""
    for name in reversed(names):
        table = registry.table_by_name(name)
        try:
            async with session.begin_nested():
                stmt = delete(table)
                if name == "users" and protect_user_id is not None:
                    stmt = stmt.where(table.c.id != protect_user_id)
                await session.execute(stmt)
        except Exception:
            pass


async def apply_import(
    session: AsyncSession,
    inner: dict[str, Any],
    *,
    mode: str = "merge",
    dry_run: bool = False,
    scope: list[str] | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """套用匯入。回 report dict（各表計數 + 中央機密計數 + mode/dry_run）。"""
    if mode not in ("merge", "replace"):
        raise ValueError(f"unknown import mode: {mode!r}")
    tables_in = inner.get("tables") or {}
    central_in = inner.get("central_secrets") or []
    # 決定要處理哪些表：交集（匯出包有的 ∩ 相依序）；scope 未給則用匯出包內全部表
    ordered = registry.all_tablenames()
    present = [n for n in ordered if n in tables_in]

    report: dict[str, Any] = {"mode": mode, "dry_run": dry_run, "tables": {}}

    if mode == "replace":
        await _wipe(session, present, protect_user_id=actor_user_id)

    for name in present:
        res = await _import_table(session, name, tables_in[name], mode=mode)
        report["tables"][name] = res.as_dict()

    # 中央機密（encrypted_secrets）：以目標金鑰重加密後 upsert
    if central_in:
        report["central_secrets"] = await _import_central(session, central_in, mode=mode)

    if dry_run:
        await session.rollback()
    else:
        await session.commit()
    return report


async def _import_central(session: AsyncSession, entries: list[dict[str, Any]], *, mode: str) -> dict[str, Any]:
    table = registry.table_by_name(registry.ENCRYPTED_SECRETS_TABLE)
    res = TableResult()
    pk_cols = ["object_type", "object_id", "field", "key_id"]  # 業務唯一鍵（UniqueConstraint）
    for entry in entries:
        built = secrets.import_central_row(entry)
        if built is None:
            res.skipped += 1
            continue
        try:
            built["object_id"] = uuid.UUID(str(built["object_id"]))
            async with session.begin_nested():
                stmt = pg_insert(table).values(**built)
                stmt = stmt.on_conflict_do_update(
                    index_elements=pk_cols,
                    set_={"ciphertext": stmt.excluded.ciphertext, "nonce": stmt.excluded.nonce},
                )
                await session.execute(stmt)
            res.inserted += 1
        except Exception as exc:
            res.errored += 1
            if len(res.errors) < 20:
                res.errors.append(f"encrypted_secrets {entry.get('object_type')}: {type(exc).__name__}: {exc}")
    return res.as_dict()
