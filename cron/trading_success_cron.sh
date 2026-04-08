#!/usr/bin/env bash
# Sygnif trading success + strategy path cron job.
#
# Runs three reports:
#   1. Trading success — 24h rolling (Telegram + JSONL log)
#   2. Trading success — 7d rolling (JSONL log only, trend tracking)
#   3. Strategy path tracker — all-time (Telegram + JSONL log)
#      Tracks entry→exit tag combinations and scores each path's worthiness.
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

LOG=user_data/logs/trading_success_cron.log

# 24-hour rolling report (sent to Telegram)
python3 trade_overseer/trading_success.py \
    --days 1 \
    --telegram \
    --no-print \
    2>> "$LOG"

# 7-day summary (log only, no Telegram — for trend tracking)
python3 trade_overseer/trading_success.py \
    --days 7 \
    --no-print \
    2>> "$LOG"

# Strategy path tracker — all-time worthiness scoring (Telegram + log)
python3 trade_overseer/strategy_paths.py \
    --days 0 \
    --telegram \
    --no-print \
    2>> "$LOG"

# 7-day path snapshot (log only — for trend comparison)
python3 trade_overseer/strategy_paths.py \
    --days 7 \
    --no-print \
    2>> "$LOG"
