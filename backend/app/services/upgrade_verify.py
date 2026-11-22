"""升級資料保全：逐筆核對（交接包 physical-v1.2 UPGRADE.md 的 P0 發布門檻，2026-11-21）。

升級前拍一份基準清冊（snapshot），遷移後用**同一份欄位定義**逐筆比對（compare）：

- 每筆原始紀錄都要能依原 id 找回；基準裡有的欄位，值一個都不能變（新增的欄位不在基準裡，另行驗證）
- 不能只比總數：兩條線的對端互換、未知瓦數被補成 0，筆數都一樣，也要抓到
- 關聯一起比（A/B 側、對端、外鍵都是欄位）；權限列也比，原本看得到的人升級後還看得到
- 資料庫引用的外部檔案（機房平面圖）記內容摘要
- 原本就有的資料問題（physical_check 的重複占用、不對稱配對…）跟升級新造成的分開：只有「新出現的」才算失敗

只讀；清冊與報告含資料內容，呼叫端要存成只有授權人員看得到的檔案（0600）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

FORMAT = 1

# 要逐筆核對的資料表：機房、機櫃、電力、佈線、裝置與 IP 的關聯、單位、權限。
# where：只取跟實體資料有關聯的列（ip_addresses 可能有幾百萬筆，只取掛在裝置上的）。
TABLES: dict[str, dict[str, Any]] = {
    "locations": {}, "racks": {}, "customers": {},
    "devices": {}, "device_ports": {}, "device_power_ports": {}, "device_power_profiles": {},
    "power_panels": {}, "power_feeds": {}, "power_outlets": {},
    "cables": {}, "cable_terminations": {},
    "ip_addresses": {"where": "device_id IS NOT NULL"},
    "permissions": {},
}


def _norm(v: Any) -> Any:
    """比對用的標準形式：JSON 可序列化、同值同表示（Decimal / UUID / 時間 / INET 都轉字串）。"""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in sorted(v.items())}
    if isinstance(v, datetime):
        return v.astimezone(UTC).isoformat()
    if isinstance(v, (bytes, memoryview)):
        return "sha256:" + hashlib.sha256(bytes(v)).hexdigest()
    return str(v)


async def _columns(session: AsyncSession, table: str) -> list[str]:
    """資料表目前的欄位（依定義順序）。向量欄位（語意搜尋的 embedding）是衍生資料、可重算，不列入比對。"""
    rows = (await session.execute(text(
        "SELECT column_name, udt_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = :t ORDER BY ordinal_position"), {"t": table})).all()
    return [c for c, udt in rows if udt != "vector"]


async def _alembic_rev(session: AsyncSession) -> str | None:
    # 先確認表存在：在 PostgreSQL 查不存在的表會讓整個交易中止，後面的查詢全部失敗
    if not await _columns(session, "alembic_version"):
        return None
    return (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()


def _file_digests(upload_dir: str | None, rows: dict[str, list[Any]], fields: list[str]) -> dict[str, str | None]:
    """機房平面圖：依 floor_plan_path 找檔案、記 sha256；找不到記 None（之後找得到也算變動）。"""
    out: dict[str, str | None] = {}
    if "floor_plan_path" not in fields:
        return out
    i = fields.index("floor_plan_path")
    base = Path(upload_dir or "")
    for oid, vals in rows.items():
        rel = vals[i]
        if not rel:
            continue
        candidates = [base / rel, base / "floorplans" / Path(str(rel)).name]
        f = next((c for c in candidates if c.is_file()), None)
        out[oid] = hashlib.sha256(f.read_bytes()).hexdigest() if f else None
    return out


async def snapshot(session: AsyncSession, *, upload_dir: str | None = None) -> dict[str, Any]:
    from app.services.physical_check import physical_check
    tables: dict[str, Any] = {}
    for name, opt in TABLES.items():
        cols = await _columns(session, name)
        if not cols or "id" not in cols:
            continue
        fields = [c for c in cols if c != "id"]
        where = f" WHERE {opt['where']}" if opt.get("where") else ""
        q = text(f'SELECT {", ".join(chr(34) + c + chr(34) for c in ["id", *fields])} FROM "{name}"{where} ORDER BY id')  # noqa: S608 —— 表名與欄名來自上面的常數與資料庫自己的 schema
        rows = {str(r[0]): [_norm(v) for v in r[1:]] for r in (await session.execute(q)).all()}
        tables[name] = {"fields": fields, "rows": rows}
    files = _file_digests(upload_dir, tables.get("locations", {}).get("rows", {}),
                          tables.get("locations", {}).get("fields", []))
    anomalies = await physical_check(session)
    return {"format": FORMAT, "created_at": datetime.now(UTC).isoformat(), "alembic": await _alembic_rev(session),
            "tables": tables, "files": {"floor_plans": files},
            "anomalies": {k: sorted(_anomaly_keys(v)) for k, v in anomalies.items() if isinstance(v, dict)}}


def _anomaly_keys(section: dict[str, Any]) -> set[str]:
    keys = set()
    for it in section.get("items", []):
        k = next((it[f] for f in ("port_id", "outlet_id", "cable_id") if it.get(f)), None)
        keys.add(str(k) if k else json.dumps(it, sort_keys=True, default=str))
    return keys


def compare(before: dict[str, Any], after: dict[str, Any], *, allow: set[str] | None = None) -> dict[str, Any]:
    """逐筆比對。allow：這次升級刻意改動、已另行驗證的「表.欄」（例如遷移明確轉換的欄位），不列為差異。"""
    allow = allow or set()
    report: dict[str, Any] = {"before_alembic": before.get("alembic"), "after_alembic": after.get("alembic"),
                              "tables": {}, "files": {}, "new_anomalies": {}, "ok": True}
    for name, b in before["tables"].items():
        a = after["tables"].get(name)
        t: dict[str, Any] = {"baseline": len(b["rows"]), "matched": 0, "missing": [], "changed": [], "missing_fields": []}
        if a is None:
            t["missing"] = sorted(b["rows"])
            report["tables"][name] = t
            report["ok"] = False
            continue
        idx = {f: i for i, f in enumerate(a["fields"])}
        t["missing_fields"] = [f for f in b["fields"] if f not in idx and f"{name}.{f}" not in allow]
        for oid, vals in b["rows"].items():
            av = a["rows"].get(oid)
            if av is None:
                t["missing"].append(oid)
                continue
            diffs = []
            for f, bv in zip(b["fields"], vals, strict=True):
                if f"{name}.{f}" in allow or f not in idx:
                    continue
                if av[idx[f]] != bv:
                    diffs.append({"field": f, "before": bv, "after": av[idx[f]]})
            if diffs:
                t["changed"].append({"id": oid, "diffs": diffs})
            else:
                t["matched"] += 1
        if t["missing"] or t["changed"] or t["missing_fields"]:
            report["ok"] = False
        report["tables"][name] = t
    bf, af = before.get("files", {}).get("floor_plans", {}), after.get("files", {}).get("floor_plans", {})
    changed_files = [oid for oid, d in bf.items() if af.get(oid) != d]
    report["files"] = {"floor_plans": {"baseline": len(bf), "changed": changed_files}}
    if changed_files:
        report["ok"] = False
    for k, keys in after.get("anomalies", {}).items():
        new = sorted(set(keys) - set(before.get("anomalies", {}).get(k, [])))
        if new:
            report["new_anomalies"][k] = new
            report["ok"] = False
    report["preexisting_anomalies"] = {k: len(v) for k, v in before.get("anomalies", {}).items() if v}
    return report


def summary_lines(report: dict[str, Any]) -> list[str]:
    """給人看的摘要（中英對照，升級腳本印出來）。"""
    out = []
    for name, t in report["tables"].items():
        bad = len(t["missing"]) + len(t["changed"]) + len(t["missing_fields"])
        mark = "OK " if not bad else "ERR"
        out.append(f"  [{mark}] {name}: baseline {t['baseline']}, matched {t['matched']}, missing {len(t['missing'])}, "
                   f"changed {len(t['changed'])}" + (f", missing fields {t['missing_fields']}" if t["missing_fields"] else ""))
    fp = report["files"]["floor_plans"]
    out.append(f"  [{'OK ' if not fp['changed'] else 'ERR'}] floor plan files: {fp['baseline']}, changed {len(fp['changed'])}")
    for k, v in report["new_anomalies"].items():
        out.append(f"  [ERR] new {k}: {len(v)}")
    for k, n in report.get("preexisting_anomalies", {}).items():
        out.append(f"  [pre] {k}: {n} (existed before the upgrade)")
    return out
