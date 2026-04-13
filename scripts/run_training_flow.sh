#!/usr/bin/env bash
# Execute Sygnif **BTC training flow**: OHLCV → ``btc_predict_runner`` → ``channel_training`` → R01–R03 monitor.
#
# Prerequisites: ``finance_agent/btc_specialist/data/btc_1h_ohlcv.json`` (Bybit / Nautilus refresh).
# Optional journal line per monitor run: ``export RULE_TAG_JOURNAL_MONITOR=YES``.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}/prediction_agent:${PYTHONPATH:-}"

echo "[flow] btc_predict_runner (1h)"
python3 prediction_agent/btc_predict_runner.py --timeframe "${RUNNER_TIMEFRAME:-1h}"

echo "[flow] channel_training"
python3 training_pipeline/channel_training.py

echo "[flow] monitor R01–R03 gates"
ARGS=("$@")
if [ "${#ARGS[@]}" -eq 0 ]; then ARGS=(--json); fi
PYTHONPATH="${ROOT}:${ROOT}/prediction_agent:${ROOT}/user_data/strategies" \
  python3 scripts/monitor_r01_r03_gate.py "${ARGS[@]}"
