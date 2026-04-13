#!/usr/bin/env bash
# Host hourly ML: btc_predict_runner with same single-flight lock as ruleprediction + finetune.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${RULEPREDICTION_PIPELINE_LOG_DIR:-$REPO_ROOT/user_data/logs}"
mkdir -p "$LOG_DIR"
LOCK_FILE="${SYGNIF_ML_LOCK_FILE:-$LOG_DIR/sygnif_ml_pipeline.lock}"
export PYTHONUNBUFFERED=1
PY="${SYGNIF_PYTHON:-/usr/bin/python3}"

{
  flock -n 200 || exit 0
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') btc_predict_runner_host start"
  cd "$REPO_ROOT/prediction_agent"
  exec "$PY" "$REPO_ROOT/prediction_agent/btc_predict_runner.py" "$@"
} 200>>"$LOCK_FILE"
