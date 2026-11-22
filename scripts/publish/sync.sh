#!/bin/sh
# Copy exactly the publishable files into the publish worktree, then run the publication checks there.
#
#   scripts/publish/sync.sh SRC PUB
#
# SRC: a clean checkout of what is being released (a git worktree of the dev HEAD, never the working directory:
#      untracked files, build output and notes must not be able to slip in).
# PUB: the publish worktree (a worktree of origin/main on the "publish" branch).
#
# Only files that git tracks in SRC *and* that are on SRC's PUBLISH.txt are copied. Files the public tree has but
# SRC no longer publishes are removed. Nothing is rewritten: a private name found by the checks is fixed in the
# source and synced again, so what is published is what was tested.
set -eu
SRC=$(cd "$1" && pwd); PUB=$(cd "$2" && pwd)
[ -e "$PUB/.git" ] || { echo "$PUB is not a git worktree" >&2; exit 1; }
[ "$(git -C "$PUB" rev-parse --abbrev-ref HEAD)" = publish ] || { echo "$PUB is not on the publish branch" >&2; exit 1; }
LIST=$(mktemp)
trap 'rm -f "$LIST"' EXIT
python3 - "$SRC" > "$LIST" <<'PY'
import fnmatch, subprocess, sys
src = sys.argv[1]
pats = [l.split("#", 1)[0].strip() for l in open(f"{src}/PUBLISH.txt", encoding="utf-8") if l.split("#", 1)[0].strip()]
ok = lambda p: any(p.startswith(x) if x.endswith("/") else fnmatch.fnmatch(p, x) for x in pats)
files = subprocess.run(["git", "-C", src, "ls-files", "-z"], capture_output=True, check=True).stdout.decode().split("\0")
print("\n".join(f for f in files if f and ok(f)))
PY
# --checksum: a file whose size and time did not change (a version bump) is still compared by content
rsync -a --checksum --files-from="$LIST" "$SRC/" "$PUB/"
git -C "$PUB" ls-files -z | tr '\0' '\n' | while read -r f; do
    if ! grep -qxF "$f" "$LIST"; then
        git -C "$PUB" rm -q -- "$f"
    fi
done
git -C "$PUB" add -A
echo "synced $(wc -l < "$LIST") files; changes against origin/main:"
git -C "$PUB" diff --cached --stat origin/main | tail -3
exec python3 "$SRC/scripts/publish/preflight.py" tree "$PUB"
