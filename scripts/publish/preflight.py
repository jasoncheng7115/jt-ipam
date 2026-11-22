#!/usr/bin/env python3
"""Publication checks: nothing private leaves this machine. Exits non-zero on any finding.

  preflight.py tree [DIR]     the files of a tree that would be published (default: the current directory)
  preflight.py msg FILE       a commit or tag message (the commit-msg hook)
  preflight.py push           the pre-push hook: every commit and annotated tag being pushed (stdin of the hook)

What is checked:
- only paths on the PUBLISH.txt allowlist are published (an opt-in list: a new file is private until listed);
- private names from a denylist kept outside the project (JT_IPAM_DENYLIST, default
  /root/.config/jt-ipam/publish-denylist.txt): lab and customer host names, domains, mailboxes, address prefixes.
  The list is required: without it nothing is published (generic checks alone are not enough);
- keys and tokens, mailboxes other than the documentation / public ones, session links, words that close a GitHub
  issue by themselves;
- screenshots: PNG text chunks and JPEG EXIF / comments (tool names, paths, user names); other binary files are
  refused;
- pushed commits and tags: author, committer and tagger are the public identity.

Nothing is rewritten: a finding is fixed in the source, so the published tree is the tested tree.
"""
from __future__ import annotations

import fnmatch
import os
import re
import struct
import subprocess
import sys
import zlib

DENYLIST = os.environ.get("JT_IPAM_DENYLIST", "/root/.config/jt-ipam/publish-denylist.txt")
PUBLIC_IDENTITY = "Jason Cheng <jasoncheng7115@users.noreply.github.com>"
ZERO = "0" * 40
failures: list[str] = []


def fail(kind: str, msg: str) -> None:
    failures.append(f"[{kind}] {msg}")


# ── allowlist ──

def allowlist(text: str) -> list[str]:
    return [ln.split("#", 1)[0].strip() for ln in text.splitlines() if ln.split("#", 1)[0].strip()]


def listed(path: str, pats: list[str]) -> bool:
    """A pattern ending in "/" is a directory (everything below it); others are file names or globs."""
    return any(path.startswith(p) if p.endswith("/") else fnmatch.fnmatch(path, p) for p in pats)


# ── denylist ──

def load_denylist() -> tuple[list[tuple[str, re.Pattern[str]]], list[str]] | None:
    """(entries, exceptions). Lines starting with "!" are public names that a broader entry would hit
    (a public blog under a private domain). Entries ending in "." or "@" or ":" are prefixes (an address
    range, a mailbox, a MAC vendor prefix); others match as whole words."""
    try:
        with open(DENYLIST, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return None
    deny, allow = [], []
    for d in lines:
        if d.startswith("!"):
            allow.append(d[1:].lower())
            continue
        d = d.lower()
        if re.fullmatch(r"(?:[0-9a-f]{2}:)+", d):
            # a MAC vendor prefix, in any of the usual spellings (aa:bb:cc, AA-BB-CC, aabbcc)
            rx = re.compile(r"(?<![0-9a-f])" + r"[:.-]?".join(d.rstrip(":").split(":")))
        elif d[-1] in ".@:":
            rx = re.compile(r"(?<![\w.])" + re.escape(d))
        elif re.search(r"[a-z]\.[a-z]", d):
            # a domain: its subdomains count too (sso.<domain>)
            rx = re.compile(r"(?<![\w-])" + re.escape(d) + r"(?![\w-])")
        else:
            rx = re.compile(r"(?<![\w.-])" + re.escape(d) + r"(?![\w-]|\.\d)")
        deny.append((d, rx))
    return deny, allow


# a key is its header followed by key material; a hint showing only the header line is not a key
KEY_HEADER = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")
KEY_BODY = re.compile(r"^\s*[\"']?[A-Za-z0-9+/=]{40,}")
SECRET = re.compile(
    r"\bgh[pousr]_[A-Za-z0-9]{30,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}|\bAKIA[0-9A-Z]{16}\b|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bsk-[A-Za-z0-9_-]{32,}"
    r"|\bjt_[a-z]{2,10}_[A-Za-z0-9_-]{40,}")
MAILBOX = re.compile(r"\b[\w.+-]*[\w+-]@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b", re.I)
PUBLIC_MAIL = re.compile(r"@(?:(?:[\w-]+\.)*example\.(?:com|net|org)|users\.noreply\.github\.com|localhost"
                         r"|anthropic\.com|[\w-]+\.(?:test|invalid|local|example)|your[-.][\w.-]+|yourdomain(?:\.[a-z]+)?)$", re.I)
SESSION = re.compile(r"claude\.ai/code/session_|^Claude-Session:", re.I | re.M)
CLOSING = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s+(?:[\w.-]+/[\w.-]+)?#\d+", re.I)
# a line marked like this may show a key-looking placeholder or mailbox on purpose (a test of these checks);
# denylisted names are refused on every line
ALLOW_MARKER = "publish" + ":allow"


def scan_text(name: str, text: str, deny_allow: tuple[list[tuple[str, re.Pattern[str]]], list[str]],
              *, message: bool = False) -> None:
    deny, allow = deny_allow
    # only the entries whose text occurs in this file at all are matched line by line (a plain substring test
    # is far cheaper than the word-boundary patterns, and most files contain none)
    whole = text.lower()
    deny = [(d, rx) for d, rx in deny if d in whole or (d.endswith(":") and rx.search(whole))]
    lines = text.splitlines()
    for n, line in enumerate(lines, 1):
        low = line.lower()
        masked = low
        for a in allow:
            if a in masked:
                masked = masked.replace(a, " " * len(a))
        for d, rx in deny:
            if (d in masked or d.endswith(":")) and rx.search(masked):
                fail("privacy", f"{name}:{n} names a private item ({d[:4]}…)")
        if not message and ALLOW_MARKER in line:
            continue
        if SECRET.search(line):
            fail("secret", f"{name}:{n} looks like a token")
        if KEY_HEADER.search(line):
            rest = line[KEY_HEADER.search(line).end():]
            nxt = lines[n] if n < len(lines) else ""
            if KEY_BODY.match(rest.replace("\\n", "\n").split("\n")[-1] if "\\n" in rest else nxt):
                fail("secret", f"{name}:{n} a private key")
        for m in MAILBOX.finditer(line):
            addr = m.group(0)
            if re.search(r"@[\w-]+\.(?:service|socket|timer|target|path|mount)$", addr):
                continue                    # a systemd unit instance (serial-getty@ttyS1.service)
            if not PUBLIC_MAIL.search(addr) and addr.lower() not in allow:
                fail("privacy", f"{name}:{n} mailbox {addr}")
        if SESSION.search(line):
            fail("privacy", f"{name}:{n} session link")
    if message and CLOSING.search(text):
        fail("message", f'{name}: "{CLOSING.search(text).group(0)}" closes a GitHub issue; write "GitHub issue #N"')


# ── binary files ──

def check_png(name: str, data: bytes, da) -> None:  # type: ignore[no-untyped-def]
    i = 8
    while i + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[i:i + 8])
        if kind in (b"tEXt", b"iTXt", b"zTXt", b"eXIf"):
            body = data[i + 8:i + 8 + n]
            if kind == b"zTXt":
                try:
                    body = body.split(b"\0", 1)[0] + b" " + zlib.decompress(body.split(b"\0", 1)[1][1:])
                except (zlib.error, IndexError):
                    pass
            fail("privacy", f"{name}: PNG {kind.decode()} chunk (strip screenshot metadata)")
            scan_text(f"{name}:{kind.decode()}", body.decode("latin-1"), da)
        i += 12 + n


def check_jpeg(name: str, data: bytes, da) -> None:  # type: ignore[no-untyped-def]
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker in (0xD9, 0xDA):          # end of image / start of the compressed data
            break
        n = struct.unpack(">H", data[i + 2:i + 4])[0]
        body = data[i + 4:i + 2 + n]
        if marker == 0xE1 or marker == 0xFE:  # EXIF / XMP, comment
            fail("privacy", f"{name}: JPEG {'EXIF/XMP' if marker == 0xE1 else 'comment'} segment (strip metadata)")
            scan_text(f"{name}:meta", body.decode("latin-1"), da)
        i += 2 + n


def check_blob(name: str, data: bytes, da) -> None:  # type: ignore[no-untyped-def]
    if b"\0" not in data[:8192]:
        scan_text(name, data.decode("utf-8", "replace"), da)
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        check_png(name, data, da)
    elif data[:3] == b"\xff\xd8\xff":
        check_jpeg(name, data, da)
    else:
        fail("privacy", f"{name}: a binary file that cannot be checked (only PNG and JPEG are published)")


# ── modes ──

def git(*args: str, cwd: str | None = None, binary: bool = False):  # type: ignore[no-untyped-def]
    r = subprocess.run(("git", *args), cwd=cwd, capture_output=True, check=True)  # noqa: S603, S607 -- fixed git subcommands
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def require_denylist():  # type: ignore[no-untyped-def]
    da = load_denylist()
    if da is None:
        print(f"refused: no private denylist at {DENYLIST} (set JT_IPAM_DENYLIST); nothing is checked without it",
              file=sys.stderr)
        sys.exit(2)
    return da


def mode_tree(top: str) -> int:
    da = require_denylist()
    pats = allowlist(open(os.path.join(top, "PUBLISH.txt"), encoding="utf-8").read())
    files = [f for f in git("ls-files", "-z", cwd=top).split("\0") if f]
    on_disk = set()
    for d, dirs, fs in os.walk(top):
        dirs[:] = [x for x in dirs if x != ".git"]
        for f in fs:
            on_disk.add(os.path.relpath(os.path.join(d, f), top))
    on_disk.discard(".git")
    for rel in sorted(on_disk | set(files)):
        if not listed(rel, pats):
            fail("allowlist", f"{rel} is not on the PUBLISH.txt allowlist")
            continue
        p = os.path.join(top, rel)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                check_blob(rel, fh.read(), da)
    print(f"{len(on_disk | set(files))} files checked")
    return report()


def mode_msg(path: str) -> int:
    da = require_denylist()
    text = "\n".join(ln for ln in open(path, encoding="utf-8", errors="replace").read().splitlines()
                     if not ln.startswith("#"))
    scan_text("message", text, da, message=True)
    return report()


def mode_push() -> int:
    da = require_denylist()
    local = allowlist(open(os.path.join(git("rev-parse", "--show-toplevel").strip(), "PUBLISH.txt"),
                           encoding="utf-8").read()) if os.path.exists("PUBLISH.txt") else None
    for line in sys.stdin:
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:
            continue
        _lref, lsha, _rref, rsha = parts
        if git("cat-file", "-t", lsha).strip() == "tag":
            head, _, body = git("cat-file", "-p", lsha).partition("\n\n")
            tagger = re.search(r"^tagger (.+?) \d+ [+-]\d{4}$", head, re.M)
            if tagger and tagger.group(1) != PUBLIC_IDENTITY:
                fail("identity", f"tag {lsha[:10]}: tagger is not {PUBLIC_IDENTITY}")
            scan_text(f"tag {lsha[:10]}", body, da, message=True)
            lsha = git("rev-parse", f"{lsha}^{{commit}}").strip()
        rng = [lsha, "--not", "--remotes"] if rsha == ZERO else [f"{rsha}..{lsha}"]
        for c in [x for x in git("rev-list", *rng).split() if x]:
            for role, who in zip(("author", "committer"),
                                 git("log", "-1", "--format=%an <%ae>%n%cn <%ce>", c).splitlines(), strict=False):
                if who != PUBLIC_IDENTITY:
                    fail("identity", f"commit {c[:10]}: {role} is not {PUBLIC_IDENTITY}")
            scan_text(f"commit {c[:10]} message", git("log", "-1", "--format=%B", c), da, message=True)
            try:
                pats = allowlist(git("show", f"{c}:PUBLISH.txt"))
            except subprocess.CalledProcessError:
                fail("allowlist", f"commit {c[:10]}: no PUBLISH.txt")
                pats = []
            out = git("diff-tree", "-m", "--root", "--no-commit-id", "-r", "-z", "--diff-filter=d", "--name-only", c)
            for path in sorted({p for p in out.split("\0") if p}):
                # a commit cannot widen its own allowlist: the path must also be on the checked-out one
                if not listed(path, pats) or (local is not None and not listed(path, local)):
                    fail("allowlist", f"commit {c[:10]}: {path} is not on the PUBLISH.txt allowlist")
                    continue
                check_blob(f"{c[:10]}:{path}", git("cat-file", "blob", f"{c}:{path}", binary=True), da)
    return report()


def report() -> int:
    if failures:
        print(f"\nFAILED ({len(failures)}):", file=sys.stderr)
        for f in failures[:400]:
            print("  " + f, file=sys.stderr)
        if len(failures) > 400:
            print(f"  … {len(failures) - 400} more", file=sys.stderr)
        return 1
    print("publication checks OK")
    return 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "tree":
        return mode_tree(os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else "."))
    if len(sys.argv) == 3 and sys.argv[1] == "msg":
        return mode_msg(sys.argv[2])
    if len(sys.argv) >= 2 and sys.argv[1] == "push":
        return mode_push()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
