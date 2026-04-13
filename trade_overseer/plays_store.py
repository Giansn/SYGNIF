"""Plays storage — reads plays.json written by finance_agent, cross-references with trades."""
import json
import logging
import os
import re
from datetime import datetime, timezone
from config import PLAYS_FILE

logger = logging.getLogger("overseer.plays")

# Set ``SYGNIF_PLAYS_BTC_USDT_ONLY=1`` to keep only BTC/USDT play sections + BTC lines in
# ``market_context``, and to match open trades to plays only for ``BTC/...`` pairs.
def _btc_usdt_only_enabled() -> bool:
    return os.environ.get("SYGNIF_PLAYS_BTC_USDT_ONLY", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


_PLAY_CHUNK_START = re.compile(r"(?=\*\*Play\s*#\d+\s*:)", re.IGNORECASE)


def _play_title_from_chunk(chunk: str) -> str:
    m = re.match(r"\*\*Play\s*#\d+\s*:\s*([^*\n]+)", chunk.strip(), re.IGNORECASE)
    if not m:
        return ""
    return re.sub(r"\*+$", "", m.group(1)).strip()


def _play_chunk_is_btc_usdt(title: str, chunk: str) -> bool:
    t = title.upper()
    if t == "BTC" or t.startswith("BTC ") or t.startswith("BTC/"):
        return True
    if "BTC/USDT" in chunk.upper():
        return True
    return False


def filter_raw_text_btc_usdt_only(raw_text: str) -> str:
    """Keep preamble with BTC context and only **Play #* sections whose title is BTC/USDT."""
    if not raw_text.strip():
        return raw_text
    chunks = _PLAY_CHUNK_START.split(raw_text)
    out: list[str] = []
    pre = (chunks[0] or "").strip()
    if pre and re.search(r"\bBTC\b|BTC/USDT|bitcoin", pre, re.I):
        out.append(pre)
    btc_play_blocks = 0
    for ch in chunks[1:]:
        ch = ch.strip()
        if not ch:
            continue
        title = _play_title_from_chunk(ch)
        if _play_chunk_is_btc_usdt(title, ch):
            out.append(ch)
            btc_play_blocks += 1
    if not out:
        note = "\n\n_(SYGNIF: SYGNIF_PLAYS_BTC_USDT_ONLY — no BTC/USDT play block in this batch.)_"
        return (pre + note).strip() if pre else note.strip()
    if pre and btc_play_blocks == 0:
        out.append(
            "\n\n_(SYGNIF: no **Play #…: BTC** in this batch — alt-only plays were filtered out.)_"
        )
    return "\n\n".join(out)


def filter_market_context_btc_usdt_only(market_context: str) -> str:
    """Keep scanner lines that mention BTC (volume / TA lines)."""
    if not market_context.strip():
        return market_context
    lines = [
        ln
        for ln in market_context.splitlines()
        if re.search(r"\bBTC\b|BTC/USDT", ln, re.I)
    ]
    return "\n".join(lines) if lines else market_context


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
        if _btc_usdt_only_enabled():
            data = dict(data)
            data["raw_text"] = filter_raw_text_btc_usdt_only(data.get("raw_text") or "")
            data["market_context"] = filter_market_context_btc_usdt_only(
                data.get("market_context") or ""
            )
            data["symbols"] = [s for s in extract_symbols(data["raw_text"]) if s == "BTC"]
            data["levels"] = extract_price_levels(data["raw_text"])
            data["btc_usdt_only"] = True
            logger.info("Plays filtered to BTC/USDT only (%d symbols)", len(data["symbols"]))
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
        pair = trade.get("pair") or ""
        if _btc_usdt_only_enabled():
            if not (pair.startswith("BTC/") or pair.startswith("BTC:")):
                continue
        # Extract base symbol from pair (e.g., "LINK/USDT" -> "LINK")
        base = pair.split("/")[0] if "/" in pair else pair.replace("USDT", "")

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
