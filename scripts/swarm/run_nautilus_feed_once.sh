#!/usr/bin/env bash
# One-shot Nautilus Bybit spot BTC sink → btc_specialist/data (needs lab .venv + nautilus_trader).
set -euo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${R}/research/nautilus_lab/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
exec "$PY" "${R}/research/nautilus_lab/bybit_nautilus_spot_btc_training_feed.py"
