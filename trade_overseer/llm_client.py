"""Ollama LLM client for Plutus-3B trade commentary with conversation history."""
import logging
from collections import deque

import requests

from config import OLLAMA_URL, OLLAMA_MODEL, EVAL_HISTORY_SIZE

logger = logging.getLogger("overseer.llm")

SYSTEM_PROMPT = """You are a crypto trade analyst monitoring a live Freqtrade bot (spot + futures on Bybit).

You receive open positions with P&L data and must give brief, actionable calls.

Rules:
- For each flagged (*) trade: state P&L, call HOLD/TRAIL/CUT, one reason
- Reference previous evaluations when relevant ("was -1.5%, now -2.2% — deteriorating")
- If a trade matches an active play, note proximity to TP/SL
- Be direct, use numbers, no filler

Example output:
EDGE[f] +3.4%: HOLD — strong momentum, trail stop to entry.
NIGHT[s] -2.2%: CUT — was -1.5% last eval, deteriorating. No recovery signal.
ADA[s] -2.1%: HOLD — oversold on daily, near support $0.24.
BTC[s] +0.05%: HOLD — near Play TP $67,800 ($67,530 now). Wait for breakout.
FARTCOIN[f] -2.4%: CUT — meme coin, no thesis, bleeding."""

# Rolling conversation history: list of (user_prompt, assistant_response)
_history: deque[tuple[str, str]] = deque(maxlen=EVAL_HISTORY_SIZE)


def evaluate(prompt: str, timeout: int = 120) -> str | None:
    """Send prompt to Plutus-3B with conversation history.

    Returns None if Ollama is unavailable — caller should fall back to rules-only.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Add previous evaluations as context
    for prev_prompt, prev_response in _history:
        messages.append({"role": "user", "content": prev_prompt})
        messages.append({"role": "assistant", "content": prev_response})

    messages.append({"role": "user", "content": prompt})

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
