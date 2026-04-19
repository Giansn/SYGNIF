#!/usr/bin/env bash
# Bounded ML refresh: wider-window btc_predict_runner passes, channel_training (skip runner),
# proof + horizon check. Shares SYGNIF_ML_LOCK_FILE with ruleprediction-pipeline + btc_predict_runner_host.
# Last 1h `btc_predict_runner` pass writes `prediction_agent/btc_prediction_output.json` — consumed by
# Host bar node / strategies reading `btc_prediction_output.json` when prediction gate is on (no Nautilus compose service).
#
# Modes:
#   (default)  one shot
#   --loop     run forever: acquire lock → finetune → sleep FINETUNE_LOOP_SLEEP_SEC (default 3600);
#              if lock busy, sleep FINETUNE_LOCK_BUSY_SLEEP_SEC (default 90) and retry.
#
# Install (loop via timer → starts long-running service once after boot):
#   sudo cp systemd/sygnif-finetune-automation.service systemd/sygnif-finetune-automation.timer /etc/systemd/system/
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now sygnif-finetune-automation.timer
#
# Optional hourly duplicate ML: btc-predict-runner.timer (uses scripts/btc_predict_runner_host.sh).
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
LOOP_SLEEP="${FINETUNE_LOOP_SLEEP_SEC:-3600}"
BUSY_SLEEP="${FINETUNE_LOCK_BUSY_SLEEP_SEC:-90}"

LOOP=0
if [[ "${1:-}" == "--loop" ]]; then
  LOOP=1
fi

run_finetune_inner() {
  echo "===== $(date -u '+%Y-%m-%dT%H:%M:%SZ') sygnif-finetune-automation =====" >>"$LOG"
  # Daily pass first; 1h last → canonical btc_prediction_output matches 1h for channel defaults.
  "$PY" "$PA/btc_predict_runner.py" --timeframe daily --window "$WD" --test-ratio "$TR" >>"$LOG" 2>&1
  "$PY" "$PA/btc_predict_runner.py" --timeframe 1h --window "$W1H" --test-ratio "$TR" >>"$LOG" 2>&1
  SKIP_PREDICT_RUNNER=1 RUNNER_TIMEFRAME=1h WINDOW="$W1H" TEST_RATIO="$TR" \
    "$PY" "$REPO_ROOT/training_pipeline/channel_training.py" >>"$LOG" 2>&1
  "$PY" "$PA/btc_prediction_proof.py" >>"$LOG" 2>&1 || true
  "$PY" "$REPO_ROOT/scripts/prediction_horizon_check.py" check --symbol BTC >>"$LOG" 2>&1 || true
  echo "===== finetune done =====" >>"$LOG"
}

run_finetune_locked() {
  if (
    flock -n 200 || exit 1
    run_finetune_inner
  ) 200>>"$LOCK_FILE"; then
    return 0
  fi
  return 1
}

if [[ "$LOOP" == 1 ]]; then
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') sygnif-finetune-automation loop start (sleep ${LOOP_SLEEP}s, busy ${BUSY_SLEEP}s)" | tee -a "$LOG"
  while true; do
    if run_finetune_locked; then
      sleep "$LOOP_SLEEP"
    else
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') finetune: lock busy, retry in ${BUSY_SLEEP}s" >>"$LOG"
      sleep "$BUSY_SLEEP"
    fi
  done
fi

if run_finetune_locked; then
  exit 0
fi
echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') finetune: skip (lock held)" >>"$LOG"
exit 0
