#!/usr/bin/env python3
"""
HTTP-only Finance Agent for Docker: briefing + Sygnif sentiment + overseer commentary.
Does not start Telegram. Binds FINANCE_AGENT_HTTP_HOST:PORT (use 0.0.0.0 in containers).

Usage:
  PYTHONPATH=/app:/app/.. python3 /app/finance_agent/http_main.py
"""
from __future__ import annotations

import logging
import os
import sys

# Docker: /app/sentiment_constants.py, /app/live_market_snapshot.py, /app/finance_agent/bot.py
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_FA = os.path.dirname(os.path.abspath(__file__))
if _FA not in sys.path:
    sys.path.insert(0, _FA)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("finance_agent.http_main")

import bot  # noqa: E402


if __name__ == "__main__":
    logger.info(
        "Starting finance-agent HTTP on %s:%s",
        bot.FINANCE_AGENT_HTTP_HOST,
        bot.FINANCE_AGENT_HTTP_PORT,
    )
    bot.start_finance_agent_http_server(block=True)
