"""升級前後逐筆核對（交接包 physical-v1.2 UPGRADE.md）。

    python -m app.cli.upgrade_verify snapshot --out /var/backups/jt-ipam/upgrade-<ts>-before.json
    python -m app.cli.upgrade_verify compare --before <before.json> --report <report.json> [--allow 表.欄 ...]

compare 會再拍一次目前的資料（after）與基準逐筆比；有缺、有改、平面圖變了、或出現新的資料問題 → 結束碼 3，
升級腳本就不重新開放寫入（後端維持停止），並保留報告。清冊與報告含資料內容，一律存成 0600。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _write_private(path: str, data: dict) -> None:  # type: ignore[type-arg]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.chmod(p, 0o600)


async def _snap() -> dict:  # type: ignore[type-arg]
    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.services.upgrade_verify import snapshot
    async with SessionLocal() as s:
        return await snapshot(s, upload_dir=get_settings().upload_dir)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jt-ipam-upgrade-verify")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("snapshot")
    s1.add_argument("--out", required=True)
    s2 = sub.add_parser("compare")
    s2.add_argument("--before", required=True)
    s2.add_argument("--after-out", help="也把升級後的清冊存下來")
    s2.add_argument("--report", required=True)
    s2.add_argument("--allow", nargs="*", default=[], help="這次升級刻意轉換、已另行驗證的 表.欄")
    args = ap.parse_args(argv)

    if args.cmd == "snapshot":
        data = asyncio.run(_snap())
        _write_private(args.out, data)
        n = sum(len(t["rows"]) for t in data["tables"].values())
        print(f"snapshot: {len(data['tables'])} tables, {n} rows, alembic {data['alembic']} -> {args.out}")
        return 0

    from app.services.upgrade_verify import compare, summary_lines
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = asyncio.run(_snap())
    if args.after_out:
        _write_private(args.after_out, after)
    report = compare(before, after, allow=set(args.allow))
    _write_private(args.report, report)
    print(f"upgrade data check: alembic {report['before_alembic']} -> {report['after_alembic']}")
    for line in summary_lines(report):
        print(line)
    print("RESULT: " + ("PASS" if report["ok"] else f"FAIL (details: {args.report})"))
    return 0 if report["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
