#!/usr/bin/env bash
# Always-on ruleprediction data loop (host): fresh OHLCV is written by Docker
# ``nautilus-research`` → ``finance_agent/btc_specialist/data/``. This job:
#
# **Cadence (align with Nautilus):** ``ruleprediction-pipeline.timer`` runs **:07** each hour
# (not :00) so the 1h candle exists; Nautilus sink default poll (``NAUTILUS_BYBIT_POLL_SEC=300``
# in compose) is unlikely to overlap the same second as ``channel_training`` — still **one**
# heavy Python job at a time (``ruleprediction-agent`` RAM rule).
#   1) runs ``channel_training`` (includes ``btc_predict_runner``),
#   2) appends a settled-bar proof row when the next 1h candle exists,
#   3) runs ``prediction_horizon_check check`` if a BTC snapshot exists (non-fatal).
#
# Install: sudo cp systemd/ruleprediction-pipeline.{service,timer} /etc/systemd/system/
#          sudo systemctl daemon-reload
#          sudo systemctl disable --now btc-predict-runner.timer   # optional: avoid duplicate hourly ML
#          sudo systemctl enable --now ruleprediction-pipeline.timer
#
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${RULEPREDICTION_PIPELINE_LOG_DIR:-$REPO_ROOT/user_data/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/ruleprediction_pipeline.log"
export PYTHONUNBUFFERED=1

{
  echo "===== $(date -u '+%Y-%m-%dT%H:%M:%SZ') ruleprediction-pipeline ====="
  cd "$REPO_ROOT"
  /usr/bin/python3 training_pipeline/channel_training.py
  /usr/bin/python3 prediction_agent/btc_prediction_proof.py
  /usr/bin/python3 scripts/prediction_horizon_check.py check --symbol BTC || true
  echo "===== done ====="
} >>"$LOG" 2>&1
