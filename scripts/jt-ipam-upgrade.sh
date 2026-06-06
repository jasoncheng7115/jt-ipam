#!/usr/bin/env bash
# Backward-compatibility shim — logic merged into jt-ipam.sh upgrade
exec "$(dirname "$0")/jt-ipam.sh" upgrade "$@"
