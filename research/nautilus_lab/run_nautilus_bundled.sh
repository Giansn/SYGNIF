#!/usr/bin/env bash
# Run **Bybit sink loop** + **sidecar strategy loop** in one process group (Docker or host).
set -euo pipefail
_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$_ROOT"
_PY="${_ROOT}/.venv/bin/python"
[[ -x "$_PY" ]] || _PY="python3"
"$_PY" "${_ROOT}/bybit_nautilus_spot_btc_training_feed.py" --loop &
PID_FEED=$!
"$_PY" "${_ROOT}/nautilus_sidecar_strategy.py" --loop &
PID_SIDE=$!
_cleanup() {
  kill "${PID_FEED}" "${PID_SIDE}" 2>/dev/null || true
}
trap _cleanup EXIT INT TERM
# Exit non-zero if either child dies so Docker restarts the bundle.
wait -n
exit 1
