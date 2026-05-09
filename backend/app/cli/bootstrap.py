"""CLI：bootstrap 第一個 admin 帳號。

用法：
    python -m app.cli.bootstrap create-admin --username admin --email admin@example.com
    # 互動式輸入密碼；或用 --password-stdin

OWASP A07：
- 密碼從 TTY / stdin 讀，不接受 --password 命令列參數（會留在 shell history）
- 密碼最少 12 字
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.user import User


async def _create_admin(username: str, email: str, password: str, force: bool) -> int:
    async with SessionLocal() as session:
        existing = (
            await session.execute(
                select(User).where((User.username == username) | (User.email == email))
            )
        ).scalar_one_or_none()
        if existing is not None:
            if not force:
                print(
                    f"[error] user already exists: id={existing.id} username={existing.username} "
                    f"email={existing.email}",
                    file=sys.stderr,
                )
                print("        use --force-update to reset password and grant admin", file=sys.stderr)
                return 1
            existing.password_hash = hash_password(password)
            existing.is_admin = True
            existing.is_active = True
            existing.failed_login_count = 0
            existing.locked_until = None
            await session.commit()
            print(f"[ok] updated existing user → admin: {existing.username} ({existing.email})")
            return 0

        user = User(
            username=username,
            email=email,
            display_name=username,
            password_hash=hash_password(password),
            auth_provider="local",
            is_active=True,
            is_admin=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        print(f"[ok] created admin: id={user.id} username={user.username} email={user.email}")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jt-ipam-bootstrap")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_admin = sub.add_parser("create-admin", help="Create the first administrator user")
    p_admin.add_argument("--username", required=True)
    p_admin.add_argument("--email", required=True)
    p_admin.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read password from stdin (one line). Otherwise prompt interactively.",
    )
    p_admin.add_argument(
        "--force-update",
        action="store_true",
        help="If user exists, reset password and grant admin",
    )

    args = parser.parse_args(argv)

    if args.cmd == "create-admin":
        if args.password_stdin:
            password = sys.stdin.readline().rstrip("\n")
        else:
            pw1 = getpass.getpass("Password: ")
            pw2 = getpass.getpass("Confirm:  ")
            if pw1 != pw2:
                print("[error] passwords do not match", file=sys.stderr)
                return 1
            password = pw1
        if len(password) < 12:
            print("[error] password must be ≥ 12 characters", file=sys.stderr)
            return 1
        return asyncio.run(
            _create_admin(args.username, args.email, password, args.force_update)
        )

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
