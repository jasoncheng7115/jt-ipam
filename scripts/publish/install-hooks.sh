#!/bin/sh
# Install the publication hooks into this repository (shared by every worktree). Keeps a copy of any hook it replaces.
set -eu
HOOKS=$(git rev-parse --git-common-dir)/hooks
HERE=$(cd "$(dirname "$0")" && pwd)
for h in pre-push commit-msg pre-commit; do
    if [ -f "$HOOKS/$h" ] && ! cmp -s "$HOOKS/$h" "$HERE/hooks/$h"; then cp -p "$HOOKS/$h" "$HOOKS/$h.before-publish-checks"; fi
    install -m 755 "$HERE/hooks/$h" "$HOOKS/$h"
done
echo "installed: pre-push commit-msg pre-commit -> $HOOKS"
