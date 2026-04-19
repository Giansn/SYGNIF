#!/usr/bin/env bash
# **Single-instance** Bybit WS tape (flock). Default lock: ``/run/user/$UID/sygnif-bybit-ws-tape.lock``.
# Override: ``SYGNIF_BYBIT_WS_LOCK_FILE=/path/to/lock``.
#
# Use with systemd ``bybit-stream-monitor.service`` so only one tape feed runs after reboot.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ROOT}/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  echo "bybit_stream_monitor_locked: missing $PY" >&2
  exit 2
fi
LOCK="${SYGNIF_BYBIT_WS_LOCK_FILE:-/run/user/$(id -u)/sygnif-bybit-ws-tape.lock}"
mkdir -p "$(dirname "$LOCK")"
# -n: if another process holds the lock, exit 0 (do not spin under systemd Restart=always).
if ! flock -n "$LOCK" "$PY" "${ROOT}/scripts/bybit_stream_monitor.py"; then
  echo "bybit_stream_monitor_locked: tape already running (lock $LOCK) — exiting" >&2
  exit 0
fi
