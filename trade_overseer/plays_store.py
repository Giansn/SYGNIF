"""Plays storage — reads plays.json written by finance_agent, cross-references with trades."""
import json
import logging
import os
import re
from datetime import datetime, timezone
from config import PLAYS_FILE

logger = logging.getLogger("overseer.plays")

# Common crypto symbols for extraction
KNOWN_SYMBOLS = {
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LINK", "AVAX", "DOT", "MATIC",
    "ATOM", "UNI", "AAVE", "OP", "ARB", "LTC", "BCH", "FIL", "NEAR", "APT",
    "SUI", "SEI", "TIA", "JUP", "WIF", "BONK", "PEPE", "HYPE", "MNT", "TON",
    "BNB", "EDGE", "INJ", "TRX", "SHIB", "FET", "RENDER", "TAO", "WLD",
}


def load_plays() -> dict | None:
    """Load the latest plays from JSON file."""
    if not os.path.exists(PLAYS_FILE):
        return None
    try:
        with open(PLAYS_FILE) as f:
            data = json.load(f)
        # Check staleness — plays older than 24h are stale
        ts = data.get("timestamp", "")
        if ts:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
            if age > 86400:
                logger.info("Plays are >24h old, marking stale")
                data["stale"] = True
        return data
    except Exception as e:
        logger.error(f"Failed to load plays: {e}")
        return None


def save_plays(raw_text: str, market_context: str = ""):
    """Save plays (called by finance_agent integration)."""
    os.makedirs(os.path.dirname(PLAYS_FILE), exist_ok=True)
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "raw_text": raw_text,
        "market_context": market_context,
        "symbols": extract_symbols(raw_text),
        "levels": extract_price_levels(raw_text),
    }
    with open(PLAYS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Saved plays: {data['symbols']}")


def extract_symbols(text: str) -> list[str]:
    """Extract crypto ticker symbols from plays text."""
    found = set()
    # Match TICKER/USDT patterns
    for m in re.finditer(r"([A-Z]{2,10})/USDT", text):
        found.add(m.group(1))
    # Match known symbols mentioned standalone
    words = set(re.findall(r"\b([A-Z]{2,10})\b", text))
    found.update(words & KNOWN_SYMBOLS)
    return sorted(found)


def extract_price_levels(text: str) -> dict:
    """Extract TP/SL price levels per symbol from plays text.

    Returns: {"BTC": {"tp": 68500.0, "sl": 66400.0}, ...}
    """
    levels = {}
    # Split by play sections
    plays = re.split(r"(?:Play\s*#?\d|PLAY\s*#?\d)", text, flags=re.IGNORECASE)

    for play_text in plays:
        # Find which symbol this play is about
        syms = extract_symbols(play_text)
        if not syms:
            continue
        sym = syms[0]  # Primary symbol

        tp = None
        sl = None

        # Look for TP patterns: "TP: $68,500" or "Target: $68500" or "TP $68.5k"
        tp_match = re.search(
            r"(?:TP|target|take.?profit)[:\s]*\$?([\d,]+\.?\d*)", play_text, re.IGNORECASE
        )
        if tp_match:
            tp = float(tp_match.group(1).replace(",", ""))

        # Look for SL patterns
        sl_match = re.search(
            r"(?:SL|stop.?loss)[:\s]*\$?([\d,]+\.?\d*)", play_text, re.IGNORECASE
        )
        if sl_match:
            sl = float(sl_match.group(1).replace(",", ""))

        if tp or sl:
            levels[sym] = {}
            if tp:
                levels[sym]["tp"] = tp
            if sl:
                levels[sym]["sl"] = sl

    return levels


def match_trades_to_plays(trades: list[dict], plays: dict | None) -> list[dict]:
    """Find trades that match active plays and add play context.

    Returns list of dicts: {trade, play_symbol, tp, sl, approaching_tp, approaching_sl}
    """
    if not plays or plays.get("stale"):
        return []

    levels = plays.get("levels", {})
    matches = []

    for trade in trades:
        # Extract base symbol from pair (e.g., "LINK/USDT" -> "LINK")
        base = trade["pair"].split("/")[0] if "/" in trade["pair"] else trade["pair"].replace("USDT", "")

        if base in levels:
            lvl = levels[base]
            current = trade.get("current_rate", 0)
            match = {
                "trade": trade,
                "play_symbol": base,
                "tp": lvl.get("tp"),
                "sl": lvl.get("sl"),
                "approaching_tp": False,
                "approaching_sl": False,
            }

            # Check proximity to TP (within 2%)
            if lvl.get("tp") and current > 0:
                distance_to_tp = abs(lvl["tp"] - current) / current * 100
                match["approaching_tp"] = distance_to_tp < 2.0

            # Check proximity to SL (within 1.5%)
            if lvl.get("sl") and current > 0:
                distance_to_sl = abs(lvl["sl"] - current) / current * 100
                match["approaching_sl"] = distance_to_sl < 1.5

            matches.append(match)

    return matches
