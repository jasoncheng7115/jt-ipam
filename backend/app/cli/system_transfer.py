"""CLI：全系統匯出／匯入（跨機搬移）。

與管理區 UI 共用同一 service 層（app.services.system_transfer），供 headless 搬移／自動化用。

用法：
    # 匯出（互動式輸入密碼；或 --passphrase-stdin 從 stdin 讀一行）
    python -m app.cli.system_transfer export \
        --scope settings,users_rbac,core,integrations \
        --out /path/backup.json --passphrase-stdin

    # 匯入（先預覽再套用；replace 會清空 in-scope 表）
    python -m app.cli.system_transfer import --file backup.json --mode merge --dry-run
    python -m app.cli.system_transfer import --file backup.json --mode merge

OWASP A07：密碼只從 TTY / stdin 讀，不接受命令列參數（避免留在 shell history）。

註：終端輸出一律英文（比照 app.cli.bootstrap，客戶終端可能非中文環境）。
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import secrets as _rng
import sys
from datetime import UTC, datetime

from sqlalchemy import text

from app.core.db import SessionLocal
from app.services.system_transfer import crypto, exporter, importer, registry
from app.version import __version__


def _read_passphrase(from_stdin: bool, *, confirm: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass("Passphrase: ")
    if confirm:
        pw2 = getpass.getpass("Confirm:    ")
        if pw != pw2:
            print("[error] passphrases do not match", file=sys.stderr)
            raise SystemExit(1)
    return pw


async def _schema_version() -> str | None:
    async with SessionLocal() as session:
        try:
            row = (await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))).first()
            return row[0] if row else None
        except Exception:
            return None


async def _build_env(scope: list[str], passphrase: str) -> tuple[dict, dict[str, int]]:
    schema_version = await _schema_version()
    async with SessionLocal() as session:
        inner = await exporter.build_export(session, scope)
    env = crypto.seal(
        inner, passphrase,
        metadata={
            "app_version": __version__, "schema_version": schema_version,
            "scope": scope, "exported_at": datetime.now(UTC).isoformat(),
        },
        rng=_rng,
    )
    return env, inner["counts"]


def _export(scope: list[str], out: str, passphrase: str) -> int:
    bad = [s for s in scope if s not in registry.SCOPES]
    if bad:
        print(f"[error] unknown scope(s): {', '.join(bad)}", file=sys.stderr)
        print(f"        valid: {', '.join(registry.SCOPES)}", file=sys.stderr)
        return 1
    env, counts = asyncio.run(_build_env(scope, passphrase))
    data = json.dumps(env, ensure_ascii=False).encode("utf-8")
    with open(out, "wb") as f:
        f.write(data)
    try:
        import os
        os.chmod(out, 0o600)
    except OSError:
        pass
    print(f"[ok] exported {sum(counts.values())} rows across {len(counts)} tables "
          f"→ {out} ({len(data)} bytes)")
    for name, n in sorted(counts.items()):
        if n:
            print(f"       {name}: {n}")
    return 0


async def _apply(inner: dict, mode: str, dry_run: bool) -> dict:
    async with SessionLocal() as session:
        return await importer.apply_import(session, inner, mode=mode, dry_run=dry_run)


def _import(file: str, mode: str, dry_run: bool, passphrase: str) -> int:
    with open(file, "rb") as f:
        raw = f.read()
    try:
        env = json.loads(raw.decode("utf-8"))
        meta = crypto.read_metadata(env)
        inner = crypto.open_envelope(env, passphrase)
    except crypto.TransferCryptoError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except (ValueError, UnicodeDecodeError) as exc:
        print(f"[error] not a valid export file: {exc}", file=sys.stderr)
        return 1

    print(f"[info] source app_version={meta.get('app_version')} schema={meta.get('schema_version')} "
          f"scope={','.join(meta.get('scope') or [])}")
    report = asyncio.run(_apply(inner, mode, dry_run))

    tag = "DRY-RUN (nothing written)" if dry_run else "APPLIED"
    print(f"[ok] import {tag}  mode={mode}")
    tot = {"inserted": 0, "updated": 0, "skipped": 0, "errored": 0}
    for name, r in sorted(report["tables"].items()):
        for k in tot:
            tot[k] += r.get(k, 0)
        if any(r.get(k) for k in tot):
            print(f"       {name}: +{r['inserted']} ~{r['updated']} skip{r['skipped']} err{r['errored']}")
    cs = report.get("central_secrets")
    if cs:
        print(f"       encrypted_secrets: +{cs['inserted']} skip{cs['skipped']} err{cs['errored']}")
    print(f"[total] inserted={tot['inserted']} updated={tot['updated']} "
          f"skipped={tot['skipped']} errored={tot['errored']}")
    return 1 if tot["errored"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jt-ipam-transfer")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="Export system config + data to an encrypted file")
    p_exp.add_argument("--scope", default=",".join(registry.DEFAULT_SCOPE),
                       help=f"comma-separated categories (default: {','.join(registry.DEFAULT_SCOPE)}; "
                            f"valid: {','.join(registry.SCOPES)})")
    p_exp.add_argument("--out", required=True, help="output file path")
    p_exp.add_argument("--passphrase-stdin", action="store_true",
                       help="read passphrase from stdin (one line); otherwise prompt")

    p_imp = sub.add_parser("import", help="Import a previously exported file into this instance")
    p_imp.add_argument("--file", required=True, help="export file path")
    p_imp.add_argument("--mode", choices=("merge", "replace"), default="merge",
                       help="merge=upsert by id (default); replace=wipe in-scope tables first")
    p_imp.add_argument("--dry-run", action="store_true", help="preview counts, write nothing")
    p_imp.add_argument("--passphrase-stdin", action="store_true",
                       help="read passphrase from stdin (one line); otherwise prompt")

    args = parser.parse_args(argv)

    if args.cmd == "export":
        pw = _read_passphrase(args.passphrase_stdin, confirm=True)
        if len(pw) < 8:
            print("[error] passphrase must be ≥ 8 characters", file=sys.stderr)
            return 1
        scope = [s.strip() for s in args.scope.split(",") if s.strip()]
        return _export(scope, args.out, pw)

    if args.cmd == "import":
        pw = _read_passphrase(args.passphrase_stdin, confirm=False)
        return _import(args.file, args.mode, args.dry_run, pw)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
