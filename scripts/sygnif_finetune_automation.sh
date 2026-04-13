#!/usr/bin/env bash
# Weekly bounded ML refresh: wider-window btc_predict_runner passes, then channel
# training without re-invoking the runner, horizon check, single-flight lock shared
# with ruleprediction-pipeline (see SYGNIF_ML_LOCK_FILE).
#
# Install:
#   sudo cp systemd/sygnif-finetune-automation.{service,timer} /etc/systemd/system/
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now sygnif-finetune-automation.timer
#
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR="${SYGNIF_FINETUNE_LOG_DIR:-$REPO_ROOT/user_data/logs}"
mkdir -p "$LOG_DIR"
LOCK_FILE="${SYGNIF_ML_LOCK_FILE:-$LOG_DIR/sygnif_ml_pipeline.lock}"
LOG="$LOG_DIR/sygnif_finetune_automation.log"
export PYTHONUNBUFFERED=1

PY="${SYGNIF_PYTHON:-/usr/bin/python3}"
PA="$REPO_ROOT/prediction_agent"

W1H="${FINETUNE_WINDOW_1H:-7}"
WD="${FINETUNE_WINDOW_DAILY:-5}"
TR="${FINETUNE_TEST_RATIO:-0.2}"

run_locked() {
  exec 200>"$LOCK_FILE"
  if ! flock -n 200; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') finetune: skip (lock held, ruleprediction or other ML job)" >>"$LOG"
    exit 0
  fi
  {
    echo "===== $(date -u '+%Y-%m-%dT%H:%M:%SZ') sygnif-finetune-automation ====="
    # Daily pass first (writes prediction JSON); 1h pass last so channel defaults match 1h snapshot.
    "$PY" "$PA/btc_predict_runner.py" --timeframe daily --window "$WD" --test-ratio "$TR"
    "$PY" "$PA/btc_predict_runner.py" --timeframe 1h --window "$W1H" --test-ratio "$TR"
    SKIP_PREDICT_RUNNER=1 RUNNER_TIMEFRAME=1h WINDOW="$W1H" TEST_RATIO="$TR" \
      "$PY" "$REPO_ROOT/training_pipeline/channel_training.py"
    "$PY" "$PA/btc_prediction_proof.py" || true
    "$PY" "$REPO_ROOT/scripts/prediction_horizon_check.py" check --symbol BTC || true
    echo "===== finetune done ====="
  } >>"$LOG" 2>&1
}

run_locked
