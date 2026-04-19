#!/usr/bin/env bash
# Swarm-gated Bybit **API demo** order: prediction protocol + fusion, aligned with **btc_future** (bf).
#
# Prerequisites: BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET (e.g. .env + swarm_operator.env).
# Dry-run:  ./scripts/swarm_btc_future_demo_order.sh
# Live:     SYGNIF_SWARM_PREDICT_ORDER_ACK=YES ./scripts/swarm_btc_future_demo_order.sh --execute
#
# Optional: append --hivemind-off to gate only on Swarm mean + Nautilus fusion + btc_future (no hm vote).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export SYGNIF_SWARM_BTC_FUTURE="${SYGNIF_SWARM_BTC_FUTURE:-1}"
export SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE="${SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE:-1}"
export SWARM_ORDER_REQUIRE_FUSION_ALIGN="${SWARM_ORDER_REQUIRE_FUSION_ALIGN:-1}"
export SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE="${SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE:-1}"
export SWARM_ORDER_BTC_FUTURE_FLAT_PASS="${SWARM_ORDER_BTC_FUTURE_FLAT_PASS:-1}"
export SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS="${SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS:-1}"
exec python3 scripts/swarm_gated_predict_protocol_order.py "$@"
