#!/usr/bin/env bash
# Self-organized prediction horizon check-in: frequent fluid ``check``, occasional ``save``.
# No ML flock (lightweight Bybit HTTP + JSON); complements ruleprediction-pipeline (:07).
#
# Env (optional):
#   CHECKIN_SYMBOL=BTC
#   CHECKIN_CHECK_INTERVAL_SEC=300       base sleep after each cycle
#   CHECKIN_FLUID_FAST_SEC=120          sleep when btc_1h_ohlcv.json mtime < CHECKIN_FLUID_OHLCV_MAX_AGE_SEC
#   CHECKIN_FLUID_OHLCV_MAX_AGE_SEC=600  “fresh data” window for faster checks
#   CHECKIN_SNAPSHOT_STALE_SEC=14400     auto ``save`` if latest snapshot older than this (4h)
#   CHECKIN_SAVE_MIN_INTERVAL_SEC=10800  minimum gap between ``save`` runs (3h), unless no snapshot
#   CHECKIN_LOG_DIR=…/user_data/logs
#
# Install:
#   sudo cp systemd/sygnif-checkin.service systemd/sygnif-checkin.timer /etc/systemd/system/
#   sudo systemctl daemon-reload && sudo systemctl enable --now sygnif-checkin.timer
#
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONUNBUFFERED=1
PY="${SYGNIF_PYTHON:-/usr/bin/python3}"
HC="$REPO_ROOT/scripts/prediction_horizon_check.py"

SYM="${CHECKIN_SYMBOL:-BTC}"
BASE_SLEEP="${CHECKIN_CHECK_INTERVAL_SEC:-180}"
FAST_SLEEP="${CHECKIN_FLUID_FAST_SEC:-90}"
OHLCV_FRESH="${CHECKIN_FLUID_OHLCV_MAX_AGE_SEC:-900}"
STALE="${CHECKIN_SNAPSHOT_STALE_SEC:-14400}"
SAVE_MIN="${CHECKIN_SAVE_MIN_INTERVAL_SEC:-10800}"

LOG_DIR="${CHECKIN_LOG_DIR:-$REPO_ROOT/user_data/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/sygnif_checkin.log"
DATA_DIR="${CHECKIN_PREDICTIONS_DIR:-$HOME/.local/share/sygnif-agent/predictions}"
OHLCV="${CHECKIN_OHLCV_FILE:-$REPO_ROOT/finance_agent/btc_specialist/data/btc_1h_ohlcv.json}"

snap_path() {
  local u="${SYM^^}"
  if [[ "$u" == *USDT ]]; then
    echo "$DATA_DIR/${u}_latest.json"
  else
    echo "$DATA_DIR/${u}USDT_latest.json"
  fi
}

snapshot_age_sec() {
  local p="$1"
  [[ -f "$p" ]] || { echo 999999999; return; }
  echo $(($(date +%s) - $(stat -c %Y "$p")))
}

last_save_epoch() {
  local p="$1"
  [[ -f "$p" ]] && stat -c %Y "$p" || echo 0
}

pick_sleep() {
  local now oage
  now=$(date +%s)
  if [[ -f "$OHLCV" ]]; then
    oage=$((now - $(stat -c %Y "$OHLCV")))
    if (( oage < OHLCV_FRESH )); then
      echo "$FAST_SLEEP"
      return
    fi
  fi
  echo "$BASE_SLEEP"
}

echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') sygnif-checkin loop start symbol=$SYM base=${BASE_SLEEP}s fast=${FAST_SLEEP}s stale=${STALE}s save_min=${SAVE_MIN}s" | tee -a "$LOG"

SNAP="$(snap_path)"
last_save_ts="$(last_save_epoch "$SNAP")"

while true; do
  now=$(date +%s)
  age="$(snapshot_age_sec "$SNAP")"
  need_save=0
  if [[ ! -f "$SNAP" ]]; then
    need_save=1
  elif (( age > STALE )); then
    need_save=1
  fi

  if (( need_save )); then
    if [[ ! -f "$SNAP" ]] || (( now - last_save_ts >= SAVE_MIN )); then
      {
        echo "===== $(date -u '+%Y-%m-%dT%H:%M:%SZ') checkin save ($SYM) ====="
        "$PY" "$HC" save --symbol "$SYM"
      } >>"$LOG" 2>&1 || true
      last_save_ts=$(date +%s)
      SNAP="$(snap_path)"
    fi
  fi

  {
    echo "----- $(date -u '+%Y-%m-%dT%H:%M:%SZ') checkin check ($SYM) -----"
    "$PY" "$HC" check --symbol "$SYM"
  } >>"$LOG" 2>&1 || true

  sleep "$(pick_sleep)"
done
