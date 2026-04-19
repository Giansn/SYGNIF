#!/usr/bin/env bash
# Re-run BTC prediction stack + swarm knowledge sidecar (host; no extra HTTP port).
# Part of **strat dev protocol** with swarm_sync-compatible TPSL defaults (see ``scripts/strat_dev_protocol.sh`` for full loop + TA).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}/prediction_agent${PYTHONPATH:+:$PYTHONPATH}"

RUNNER=1
CHANNEL=1
for arg in "$@"; do
  case "$arg" in
    --no-runner) RUNNER=0 ;;
    --no-channel) CHANNEL=0 ;;
  esac
done

if [[ "$RUNNER" == 1 ]]; then
  python3 "${ROOT}/prediction_agent/btc_predict_runner.py"
fi
if [[ "$CHANNEL" == 1 ]]; then
  python3 "${ROOT}/training_pipeline/channel_training.py"
fi
# Swarm JSON before fusion so ``swarm_keypoints`` in fusion match latest ``compute_swarm()``.
python3 "${ROOT}/finance_agent/swarm_knowledge.py" --print-json
python3 "${ROOT}/prediction_agent/nautilus_protocol_fusion.py" sync || true
# Demo linear TP/SL from ``btc_prediction_output.json`` (opt out: SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL=0)
export SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL="${SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL:-1}"
export SYGNIF_SWARM_TPSL_PROFILE="${SYGNIF_SWARM_TPSL_PROFILE:-reward_risk}"
python3 "${ROOT}/finance_agent/swarm_btc_future_tpsl_apply.py" || true
