#!/usr/bin/env bash
# Daily: crypto-market-data README bundle + finance-agent / Cursor Cloud dashboard JSON.
#
# 1) Fetches all upstream data/daily/*.json + crypto_market_data_daily_analysis.md
# 2) Regenerates btc_specialist_dashboard.json (llm_analyze + KB when CRYPTO_CONTEXT_LLM=1)
#
# Env (optional):
#   CRYPTO_CONTEXT_LLM            — unset or 1 = LLM; 0/false = heuristic only (see report._try_llm_crypto_sections)
#   CRYPTO_MARKET_DATA_TZ         — hour gate for hourly crontab (default Europe/Berlin)
#   CRYPTO_MARKET_DATA_RUN_SCRIPT — override path to run_crypto_market_data_daily.py
#   BTC_CONTEXT_SYNC_TARGET       — if set, rsync key JSON/md into this dir after success
#                                   (e.g. legacy dashboard tree ~/xrp_claude_bot/finance_agent/btc_specialist/data)
#
# Schedule (00:00 local, DST-safe on UTC hosts — same pattern as cron_crypto_market_data_daily.sh):
#   0 * * * * [ "$(TZ=${CRYPTO_MARKET_DATA_TZ:-Europe/Berlin} date +\%H)" = "00" ] && /home/ubuntu/SYGNIF/scripts/cron_finance_agent_btc_context.sh
#
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${CRYPTO_MARKET_DATA_LOG_DIR:-$REPO_ROOT/user_data/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/finance_agent_btc_context.log"
DAILY_PY="${CRYPTO_MARKET_DATA_RUN_SCRIPT:-$REPO_ROOT/finance_agent/btc_specialist/scripts/run_crypto_market_data_daily.py}"
REFRESH_PY="$REPO_ROOT/finance_agent/btc_specialist/scripts/refresh_btc_dashboard_json.py"
DATA_DIR="$REPO_ROOT/finance_agent/btc_specialist/data"

# CRYPTO_CONTEXT_LLM defaults to on in report._try_llm_crypto_sections; set CRYPTO_CONTEXT_LLM=0 in .env to skip LLM.
# CURSOR_* / ANTHROPIC_*: loaded by refresh_btc_dashboard_json.py via pull_btc_context._load_repo_env.

{
  echo "===== $(date -u '+%Y-%m-%dT%H:%M:%SZ') UTC | local $(TZ=${CRYPTO_MARKET_DATA_TZ:-Europe/Berlin} date '+%Y-%m-%d %H:%M %Z') ====="
  echo "===== CRYPTO_CONTEXT_LLM=${CRYPTO_CONTEXT_LLM:-<unset>} ====="
  echo "===== step 1: $DAILY_PY ====="
  /usr/bin/python3 "$DAILY_PY"
  echo "===== step 2: $REFRESH_PY ====="
  /usr/bin/python3 "$REFRESH_PY"
  if [ -n "${BTC_CONTEXT_SYNC_TARGET:-}" ] && [ -d "$BTC_CONTEXT_SYNC_TARGET" ]; then
    echo "===== sync → $BTC_CONTEXT_SYNC_TARGET ====="
    install -m 0644 -D "$DATA_DIR/btc_specialist_dashboard.json" "$BTC_CONTEXT_SYNC_TARGET/btc_specialist_dashboard.json"
    install -m 0644 -D "$DATA_DIR/btc_crypto_market_data.json" "$BTC_CONTEXT_SYNC_TARGET/btc_crypto_market_data.json" 2>/dev/null || true
    install -m 0644 -D "$DATA_DIR/crypto_market_data_daily_analysis.md" "$BTC_CONTEXT_SYNC_TARGET/crypto_market_data_daily_analysis.md" 2>/dev/null || true
  fi
  echo "===== done ====="
} >>"$LOG" 2>&1
