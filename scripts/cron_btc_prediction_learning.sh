#!/usr/bin/env bash
# Refresh Bybit BTC OHLCV bundle, run hourly ML (btc_predict_runner), then horizon check.
# Uses the same flock file as scripts/btc_predict_runner_host.sh so systemd timer + cron do not double-train.
#
# Env:
#   SYGNIF_PYTHON — default REPO/.venv/bin/python3 then /usr/bin/python3
#   SYGNIF_ML_LOCK_FILE / RULEPREDICTION_PIPELINE_LOG_DIR — same as btc_predict_runner_host.sh
#
# Install: cron/crontab.txt (hourly; offset from OnCalendar=hourly timer).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONUNBUFFERED=1
PY="${SYGNIF_PYTHON:-$REPO/.venv/bin/python3}"
if [[ ! -x "$PY" ]]; then
  PY="/usr/bin/python3"
fi
LOG_DIR="${RULEPREDICTION_PIPELINE_LOG_DIR:-$REPO/user_data/logs}"
mkdir -p "$LOG_DIR"
LOCK_FILE="${SYGNIF_ML_LOCK_FILE:-$LOG_DIR/sygnif_ml_pipeline.lock}"

{
  flock -n 200 || exit 0
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) cron_btc_prediction_learning start"
  cd "$REPO"
  set +e
  "$PY" "$REPO/finance_agent/btc_specialist/scripts/pull_btc_context.py"
  pull_rc=$?
  set -e
  if [[ "$pull_rc" -ne 0 ]]; then
    echo "warn: pull_btc_context exit=${pull_rc} (continuing with on-disk OHLCV)" >&2
  fi
  cd "$REPO/prediction_agent"
  "$PY" "$REPO/prediction_agent/btc_predict_runner.py"
  cd "$REPO"
  "$PY" "$REPO/scripts/prediction_horizon_check.py" check --symbol BTC || true
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) cron_btc_prediction_learning done"
} 200>>"$LOCK_FILE"
