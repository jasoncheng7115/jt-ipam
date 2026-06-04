#!/usr/bin/env bash
# 向後相容 shim — 邏輯已整併進 jt-ipam.sh install
exec "$(dirname "$0")/jt-ipam.sh" install "$@"
