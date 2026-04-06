"""Ollama LLM client for Plutus-3B trade commentary with conversation history."""
import logging
from collections import deque

import requests

from config import OLLAMA_URL, OLLAMA_MODEL, EVAL_HISTORY_SIZE

logger = logging.getLogger("overseer.llm")

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

# Rolling conversation history: list of (user_prompt, assistant_response)
_history: deque[tuple[str, str]] = deque(maxlen=EVAL_HISTORY_SIZE)


def evaluate(prompt: str, timeout: int = 180) -> str | None:
    """Send prompt to Plutus-3B with conversation history.

    Returns None if Ollama is unavailable — caller should fall back to rules-only.
    """
    # No history — TA data + deltas are more valuable than old evals for 3B model
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {
                    "num_predict": 200,
                    "temperature": 0.4,
                    "num_ctx": 2048,
                },
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        response = resp.json().get("message", {}).get("content", "").strip()

        # Store in history for next evaluation
        if response:
            _history.append((prompt, response))

        return response
    except requests.exceptions.Timeout:
        logger.warning("Ollama timeout, skipping LLM eval")
        return None
    except Exception as e:
        logger.error(f"Ollama error: {e}")
        return None


def is_available() -> bool:
    """Check if Ollama is running."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


