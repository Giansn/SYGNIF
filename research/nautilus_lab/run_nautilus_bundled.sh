#!/usr/bin/env bash
# Run **Bybit sink loop** + **sidecar strategy loop** in one container (one restart policy).
set -euo pipefail
python3 /lab/workspace/bybit_nautilus_spot_btc_training_feed.py --loop &
PID_FEED=$!
python3 /lab/workspace/nautilus_sidecar_strategy.py --loop &
PID_SIDE=$!
_cleanup() {
  kill "${PID_FEED}" "${PID_SIDE}" 2>/dev/null || true
}
trap _cleanup EXIT INT TERM
# Exit non-zero if either child dies so Docker restarts the bundle.
wait -n
exit 1
