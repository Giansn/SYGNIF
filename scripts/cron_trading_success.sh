#!/usr/bin/env bash
# Sygnif trading success + strategy paths — run from ~/SYGNIF (6-hour cron).
set -euo pipefail
REPO_DIR="${REPO_DIR:-/home/ubuntu/SYGNIF}"
cd "$REPO_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

LOG="${LOG:-user_data/logs/trading_success_cron.log}"

python3 trade_overseer/trading_success.py --days 1 --telegram --no-print 2>>"$LOG"
python3 trade_overseer/trading_success.py --days 7 --no-print 2>>"$LOG"
python3 trade_overseer/strategy_paths.py --days 0 --telegram --no-print 2>>"$LOG"
python3 trade_overseer/strategy_paths.py --days 7 --no-print 2>>"$LOG"
