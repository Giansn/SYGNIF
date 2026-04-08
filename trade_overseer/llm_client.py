"""Overseer commentary client.

Priority:
1) External agent webhook (Cursor Cloud/worker)
2) Anthropic Claude API (legacy fallback)
3) None (rules-only fallback in overseer)
"""
import logging
import os

import requests

logger = logging.getLogger("overseer.llm")

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5-20251001"
AGENT_URL = os.environ.get("OVERSEER_AGENT_URL", "").strip()
AGENT_TOKEN = os.environ.get("OVERSEER_AGENT_TOKEN", "").strip()

SYSTEM_PROMPT = """Freqtrade bot monitor (spot [s] + futures [f], Bybit).
Input: TA briefing lines then trade lines with P&L delta.

TA briefing format (pipe-delimited):
  COIN $price trend|RSI:N WR:N StRSI:N|MACD:dir CMF:N|S:support R:resistance|TA:score signal leverage

Key signals: strong_ta_long (TA>=65+vol), strong_ta_short (TA<=25), sf_long/sf_short (swing failure).
TA score 40-70 = ambiguous (Claude sentiment zone). WR>-5 = overbought exit. WR<-95 = oversold exit.

Output format — one line per flagged (*) trade:
EDGE[f] +3.4% (was +1.8%): TRAIL — RSI 78 WR:-3 overbought, lock +2%.
NIGHT[s] -2.2% (was -1.5%): CUT — TA:28 downtrend, broke S:$0.42.
ADA[s] +0.3% (new): HOLD — TA:62 uptrend, RSI 55 room to run.
FART[f] -2.4% (was -2.1%): CUT — TA:31 RSI 38 weak, no support."""


def evaluate(prompt: str, timeout: int = 30) -> str | None:
    """Send prompt to configured overseer model endpoint.

    Returns None if API unavailable — caller falls back to rules-only.
    """
    # Preferred path: external agent endpoint.
    if AGENT_URL:
        try:
            headers = {"content-type": "application/json"}
            if AGENT_TOKEN:
                headers["authorization"] = f"Bearer {AGENT_TOKEN}"
            resp = requests.post(
                AGENT_URL,
                headers=headers,
                json={"prompt": prompt, "source": "trade_overseer"},
                timeout=timeout,
            )
            if resp.ok:
                data = resp.json() if "application/json" in resp.headers.get("content-type", "") else {}
                text = data.get("commentary") or data.get("text") or (resp.text or "").strip()
                return text.strip() if text else None
            logger.error(f"Agent endpoint error: {resp.status_code} {resp.text[:120]}")
        except Exception as e:
            logger.error(f"Agent endpoint failure: {e}")

    # Legacy fallback: Anthropic direct.
    if not ANTHROPIC_KEY:
        logger.warning("No OVERSEER_AGENT_URL or ANTHROPIC_API_KEY, skipping LLM eval")
        return None

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 500,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        if resp.ok:
            return resp.json()["content"][0]["text"].strip()
        logger.error(f"Claude API error: {resp.status_code} {resp.text[:100]}")
        return None
    except requests.exceptions.Timeout:
        logger.warning("Claude timeout, skipping LLM eval")
        return None
    except Exception as e:
        logger.error(f"Claude error: {e}")
        return None


def is_available() -> bool:
    """Check whether preferred commentary backend is reachable."""
    if AGENT_URL:
        try:
            headers = {}
            if AGENT_TOKEN:
                headers["authorization"] = f"Bearer {AGENT_TOKEN}"
            resp = requests.get(AGENT_URL, headers=headers, timeout=5)
            # Many webhook endpoints may not support GET but still be alive.
            return resp.status_code < 500
        except Exception:
            return False

    if not ANTHROPIC_KEY:
        return False
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "ping"}],
            },
            timeout=10,
        )
        return resp.ok
    except Exception:
        return False
