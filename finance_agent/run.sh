#!/bin/bash
# Finance Agent launcher
export FINANCE_BOT_TOKEN="${FINANCE_BOT_TOKEN:-}"
export TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-1134139785}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

# Source env file if exists
[ -f /home/ubuntu/finance_agent/.env ] && source /home/ubuntu/finance_agent/.env

exec python3 /home/ubuntu/finance_agent/bot.py
