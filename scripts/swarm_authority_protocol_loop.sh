#!/usr/bin/env bash
# **Swarm authority** + prediction protocol (Bybit API demo): same as ``swarm_auto_predict_protocol_loop.py`` —
# gate each iteration with ``compute_swarm()`` + ``write_fused_sidecar`` (research / Nautilus + ML + btc_future),
# optional Hivemind **hm**, Nautilus non-contradiction; **portfolio authority** holds the venue leg when Swarm
# blocks a flip into the opposite model target.
#
# Requires: BYBIT_DEMO_* ; live orders: SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES and --execute
#
# Env overrides: SWARM_PORTFOLIO_AUTHORITY=0 to allow flip-close when opposite entry is gated off.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export SWARM_PORTFOLIO_AUTHORITY="${SWARM_PORTFOLIO_AUTHORITY:-1}"
exec python3 scripts/swarm_auto_predict_protocol_loop.py "$@"
