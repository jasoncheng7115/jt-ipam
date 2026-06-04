"""CLI：phpIPAM 一鍵遷移 / 持續同步。

用法：
    # 第一次（dry-run 預覽）
    python -m app.cli.phpipam_migrate sync \
        --mysql-url "mysql://user:pass@phpipam-host:3306/phpipam" \
        --dry-run

    # 正式同步（衝突時跳過）
    python -m app.cli.phpipam_migrate sync \
        --mysql-url "mysql://..." \
        --on-conflict skip

    # 平行使用期：phpIPAM 是 source of truth，每次都覆寫
    python -m app.cli.phpipam_migrate sync \
        --mysql-url "mysql://..." \
        --on-conflict overwrite

    # 排程：systemd timer 每 10 分鐘跑一次（mysql_url 由 /etc/jt-ipam/backend.env 額外提供）

OWASP A04：不從命令列接受 password；建議用 mysql:///path-to-config 或
.my.cnf；若必須在命令列，使用者要清楚理解 shell history 風險。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from app.core.db import SessionLocal
from app.services.phpipam_migration import run_migration


async def _run(args: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        report = await run_migration(
            session,
            mysql_url=args.mysql_url,
            on_conflict=args.on_conflict,
            dry_run=args.dry_run,
        )
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    if report.error:
        return 2
    total_errors = sum(t.errored for t in report.tables.values())
    return 1 if total_errors > 0 else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jt-ipam-phpipam-migrate")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="Sync data from phpIPAM into jt-ipam")
    p_sync.add_argument(
        "--mysql-url",
        default=os.environ.get("PHPIPAM_MYSQL_URL"),
        help="phpIPAM MySQL DSN (mysql://user:pass@host:port/dbname). "
             "Defaults to env PHPIPAM_MYSQL_URL.",
    )
    p_sync.add_argument(
        "--on-conflict",
        choices=["skip", "overwrite"],
        default="skip",
        help="Action when an already-mapped row has changed in phpIPAM. "
             "Use 'overwrite' during parallel-use phase where phpIPAM remains "
             "the source of truth.",
    )
    p_sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the report without writing to jt-ipam DB.",
    )

    args = parser.parse_args(argv)

    if args.cmd == "sync":
        if not args.mysql_url:
            print("[error] --mysql-url is required (or set PHPIPAM_MYSQL_URL)",
                  file=sys.stderr)
            return 2
        return asyncio.run(_run(args))

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
