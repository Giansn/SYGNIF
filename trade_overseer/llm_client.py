"""Ollama LLM client for Plutus-3B trade commentary with conversation history."""
import logging
from collections import deque

import requests

from config import OLLAMA_URL, OLLAMA_MODEL, EVAL_HISTORY_SIZE

logger = logging.getLogger("overseer.llm")

SYSTEM_PROMPT = """Freqtrade bot monitor (spot [s] + futures [f], Bybit).
Input: TA data (trend/RSI/MACD/support/resistance) then trade lines with P&L delta.
Output format — one line per flagged (*) trade:

EDGE[f] +3.4% (was +1.8%): TRAIL — RSI 78 overbought, lock +2%.
NIGHT[s] -2.2% (was -1.5%): CUT — downtrend, broke support $0.42.
ADA[s] +0.3% (new): HOLD — uptrend, RSI 55, room to run.
FART[f] -2.4% (was -2.1%): CUT — RSI 38 weak, no support nearby."""

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


def get_history_summary() -> str:
    """Return brief summary of recent evaluations for debugging."""
    if not _history:
        return "No previous evaluations."
    return f"{len(_history)} previous eval(s), latest calls: {_history[-1][1][:100]}..."


def is_available() -> bool:
    """Check if Ollama is running."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def force_unload():
    """Force unload model from RAM (watchdog use)."""
    try:
        requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "keep_alive": 0},
            timeout=5,
        )
    except Exception:
        pass
