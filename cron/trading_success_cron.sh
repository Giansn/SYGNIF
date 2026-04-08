#!/usr/bin/env bash
# Sygnif trading success cron job.
#
# Extracts trading success metrics from spot + futures databases and:
#   1. Appends JSON to user_data/logs/trading_success.jsonl
#   2. Sends a Telegram summary
#
# Install:
#   crontab -e
#   # Add the line from cron/crontab.txt
#
# Manual run:
#   bash cron/trading_success_cron.sh

set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/ubuntu/xrp_claude_bot}"
cd "$REPO_DIR"

# Load environment (Telegram tokens, etc.)
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# 24-hour rolling report (sent to Telegram)
python3 trade_overseer/trading_success.py \
    --days 1 \
    --telegram \
    --no-print \
    2>> user_data/logs/trading_success_cron.log

# 7-day summary (log only, no Telegram — for trend tracking)
python3 trade_overseer/trading_success.py \
    --days 7 \
    --no-print \
    2>> user_data/logs/trading_success_cron.log
