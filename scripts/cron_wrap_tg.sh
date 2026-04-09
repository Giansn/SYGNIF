#!/bin/bash
# Run a cron command, append to log file, send stdout+stderr to Sygnif Agent Telegram.
# Usage: cron_wrap_tg.sh "Job title" /path/to.log command [args...]
set -euo pipefail
TITLE="${1:?title}"
LOG="${2:?log path}"
shift 2
set +e
OUT="$("$@" 2>&1)"
CODE=$?
set -e
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
{
  echo "=== ${TS} ${TITLE} exit=${CODE} ==="
  echo "${OUT}"
  echo ""
} >> "${LOG}"
printf '%s\n' "${OUT}" | /usr/bin/python3 /home/ubuntu/xrp_claude_bot/scripts/cron_tg_notify.py "${TITLE} · exit ${CODE}"
exit "${CODE}"
