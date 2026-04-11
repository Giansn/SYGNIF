#!/usr/bin/env bash
# Daily pull: ErcinDedeoglu/crypto-market-data → btc_specialist/data/
# (btc_crypto_market_data.json + crypto_market_data_daily_analysis.md)
#
# Schedule (00:00 *your* local time, DST-safe on UTC servers):
#   0 * * * * [ "$(TZ=${CRYPTO_MARKET_DATA_TZ:-Europe/Berlin} date +\%H)" = "00" ] && /path/to/SYGNIF/scripts/cron_crypto_market_data_daily.sh
#
# If `finance_agent` lives outside this clone (e.g. service uses ~/finance_agent), set:
#   CRYPTO_MARKET_DATA_RUN_SCRIPT=/home/ubuntu/finance_agent/btc_specialist/scripts/run_crypto_market_data_daily.py
#
# If your server clock is already in your zone, use: 0 0 * * * /path/to/this/script.sh
#
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${CRYPTO_MARKET_DATA_RUN_SCRIPT:-$REPO_ROOT/finance_agent/btc_specialist/scripts/run_crypto_market_data_daily.py}"
LOG_DIR="${CRYPTO_MARKET_DATA_LOG_DIR:-$REPO_ROOT/user_data/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/crypto_market_data_daily.log"
{
  echo "===== $(date -u '+%Y-%m-%dT%H:%M:%SZ') UTC | local $(TZ=${CRYPTO_MARKET_DATA_TZ:-Europe/Berlin} date '+%Y-%m-%d %H:%M %Z') ====="
  echo "===== script: $PY ====="
  /usr/bin/python3 "$PY"
} >>"$LOG" 2>&1
