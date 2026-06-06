#!/usr/bin/env bash
# Backward-compatibility shim — logic merged into jt-ipam.sh install
exec "$(dirname "$0")/jt-ipam.sh" install "$@"
