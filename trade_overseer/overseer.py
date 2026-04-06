#!/usr/bin/env python3
"""
Sygnif Trade Overseer — monitors open trades, cross-references plays,
sends LLM-powered commentary via Telegram.

Runs as a daemon with a 5-minute poll loop + HTTP server on :8090.
"""
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

# Add project dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import ft_client
import llm_client
import plays_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s - %(message)s",
)
logger = logging.getLogger("overseer")

# --- State ---
trade_state: dict[int, dict] = {}  # trade_id -> {pair, instance, last_profit_pct, peak_profit, last_eval_time, stale_alerted}
last_commentary: str = ""
last_eval_time: str = ""


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def tg_send(text: str):
    """Send message to Telegram."""
    if not config.TG_TOKEN or not config.TG_CHAT:
        logger.warning("Telegram not configured, printing to stdout")
        print(text)
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{config.TG_TOKEN}/sendMessage",
            json={
                "chat_id": config.TG_CHAT,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


# ---------------------------------------------------------------------------
# Duration formatting
# ---------------------------------------------------------------------------
def duration_str(seconds: float) -> str:
    if not seconds:
        return "--"
    h, remainder = divmod(int(seconds), 3600)
    m = remainder // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


# ---------------------------------------------------------------------------
# Rules engine — decides when LLM eval is warranted
# ---------------------------------------------------------------------------
def check_events(trades: list[dict]) -> list[dict]:
    """Compare current trades against state, return notable events."""
    global trade_state
    now = time.time()
    events = []
    current_ids = set()

    plays = plays_store.load_plays()
    play_matches = plays_store.match_trades_to_plays(trades, plays)
    play_match_ids = {m["trade"]["trade_id"] for m in play_matches}

    for trade in trades:
        tid = trade["trade_id"]
        current_ids.add(tid)
        prev = trade_state.get(tid, {})
        reasons = []

        # New trade
        if tid not in trade_state:
            reasons.append("NEW")

        # Profit thresholds
        pct = trade["profit_pct"]
        if pct >= config.PROFIT_ALERT_HIGH:
            reasons.append(f"HIGH_PROFIT({pct:+.1f}%)")
        if pct <= config.PROFIT_ALERT_LOW:
            reasons.append(f"LOW_PROFIT({pct:+.1f}%)")

        # Significant change since last eval
        prev_pct = prev.get("last_profit_pct", 0)
        if abs(pct - prev_pct) >= config.SIGNIFICANT_CHANGE_PCT:
            reasons.append(f"CHANGE({prev_pct:+.1f}%→{pct:+.1f}%)")

        # Stale trade
        duration_h = trade["trade_duration"] / 3600
        if duration_h >= config.STALE_TRADE_HOURS and not prev.get("stale_alerted"):
            reasons.append(f"STALE({duration_h:.0f}h)")

        # Play match
        if tid in play_match_ids:
            match = next(m for m in play_matches if m["trade"]["trade_id"] == tid)
            if match["approaching_tp"]:
                reasons.append(f"NEAR_PLAY_TP({match['play_symbol']})")
            if match["approaching_sl"]:
                reasons.append(f"NEAR_PLAY_SL({match['play_symbol']})")

        # Cooldown check
        if reasons and prev.get("last_eval_time"):
            if now - prev["last_eval_time"] < config.EVAL_COOLDOWN_SEC:
                # Only skip if the only reason is a repeat
                if not any(r.startswith("NEW") or r.startswith("NEAR_PLAY") for r in reasons):
                    continue

        if reasons:
            events.append({"trade": trade, "reasons": reasons})

        # Update state
        trade_state[tid] = {
            "pair": trade["pair"],
            "instance": trade["instance"],
            "last_profit_pct": pct,
            "peak_profit": max(pct, prev.get("peak_profit", pct)),
            "last_eval_time": now if reasons else prev.get("last_eval_time", 0),
            "stale_alerted": prev.get("stale_alerted", False) or "STALE" in " ".join(reasons),
        }

    # Detect closed trades
    for tid in list(trade_state.keys()):
        if tid not in current_ids:
            closed = trade_state.pop(tid)
            events.append({
                "trade": {"trade_id": tid, "pair": closed["pair"], "instance": closed["instance"],
                           "profit_pct": closed["last_profit_pct"], "closed": True},
                "reasons": ["CLOSED"],
            })

    return events


# ---------------------------------------------------------------------------
# Finance Agent briefing
# ---------------------------------------------------------------------------
FA_BRIEFING_URL = "http://127.0.0.1:8091/briefing"


def _fetch_briefing(symbols: list[str]) -> str:
    """Fetch TA briefing from finance agent."""
    try:
        import requests as _req
        resp = _req.get(FA_BRIEFING_URL, params={"symbols": ",".join(symbols)}, timeout=15)
        if resp.status_code == 200 and resp.text.strip():
            return resp.text.strip()
    except Exception as e:
        logger.debug(f"Briefing fetch failed: {e}")
    return ""


# ---------------------------------------------------------------------------
# Build LLM prompt
# ---------------------------------------------------------------------------
def build_prompt(trades: list[dict], events: list[dict]) -> str:
    """Build compact prompt for Plutus-3B with TA context from finance agent."""
    plays = plays_store.load_plays()
    event_ids = {ev["trade"]["trade_id"] for ev in events}

    # Flagged trades first, then top movers for context
    flagged = [t for t in trades if t["trade_id"] in event_ids]
    others = sorted(
        [t for t in trades if t["trade_id"] not in event_ids],
        key=lambda x: abs(x["profit_pct"]),
        reverse=True,
    )[:max(0, 6 - len(flagged))]

    # Collect traded symbols for briefing
    all_shown = flagged + others
    symbols = list({t["pair"].replace("/USDT:USDT", "").replace("/USDT", "") for t in all_shown})

    # TA briefing from finance agent
    briefing = _fetch_briefing(symbols)

    lines = []
    if briefing:
        lines.append(briefing)
        lines.append("")

    for t in all_shown:
        pair = t["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
        inst = t["instance"][0]  # s or f
        dur = duration_str(t["trade_duration"])
        flag = " *" if t["trade_id"] in event_ids else ""
        # Include delta from last eval
        prev = trade_state.get(t["trade_id"], {})
        prev_pct = prev.get("last_profit_pct")
        delta = f" (was {prev_pct:+.1f}%)" if prev_pct is not None else " (new)"
        lines.append(f"{pair}[{inst}] {t['profit_pct']:+.2f}%{delta} {dur} ${t['current_rate']:.4g}{flag}")

    prompt = "\n".join(lines)
    if len(trades) > len(all_shown):
        prompt += f"\n(+{len(trades) - len(all_shown)} more, mostly flat)"

    # Append play levels if available
    if plays and not plays.get("stale"):
        levels = plays.get("levels", {})
        if levels:
            play_lines = []
            for sym, lvl in levels.items():
                parts = []
                if lvl.get("tp"):
                    parts.append(f"TP=${lvl['tp']:,.0f}")
                if lvl.get("sl"):
                    parts.append(f"SL=${lvl['sl']:,.0f}")
                play_lines.append(f"{sym}: {' '.join(parts)}")
            prompt += "\nPlays: " + " | ".join(play_lines)

    # Events summary
    if events:
        alerts = []
        for ev in events[:5]:
            pair = ev["trade"]["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
            alerts.append(f"{pair}: {', '.join(ev['reasons'])}")
        prompt += "\nAlerts: " + "; ".join(alerts)

    prompt += "\n\nCall each flagged (*) trade: HOLD/TRAIL/CUT + reason. Use the TA data above."
    return prompt


# ---------------------------------------------------------------------------
# Build rules-only summary (fallback when LLM unavailable)
# ---------------------------------------------------------------------------
def build_rules_summary(trades: list[dict], events: list[dict]) -> str:
    """Generate a simple rules-based summary without LLM."""
    lines = [f"*Overseer* | {datetime.now(timezone.utc).strftime('%H:%M UTC')}"]

    if not trades:
        lines.append("No open trades.")
        return "\n".join(lines)

    lines.append(f"{len(trades)} open trade(s):\n")
    for t in trades:
        dur = duration_str(t["trade_duration"])
        emoji = "+" if t["profit_pct"] >= 0 else ""
        lines.append(f"`{t['pair']}` [{t['instance']}] {emoji}{t['profit_pct']:.2f}% | {dur}")

    if events:
        lines.append("\n*Alerts:*")
        for ev in events:
            t = ev["trade"]
            reasons = ", ".join(ev["reasons"])
            lines.append(f"  {t['pair']}: {reasons}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------
def run_evaluation(force: bool = False) -> str:
    """Run one evaluation cycle. Returns the commentary text."""
    global last_commentary, last_eval_time

    trades = ft_client.get_all_trades()
    if force:
        # For forced eval, only flag the most notable trades (top 5 by |P&L|)
        ranked = sorted(trades, key=lambda x: abs(x["profit_pct"]), reverse=True)[:5]
        events = [{"trade": t, "reasons": ["FORCED"]} for t in ranked]
    else:
        events = check_events(trades)

    # If no events and not forced, skip
    if not events and not force:
        return ""

    prompt = build_prompt(trades, events)
    commentary = llm_client.evaluate(prompt)

    if commentary:
        msg = f"*Overseer* | {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n\n{commentary}"
    else:
        msg = build_rules_summary(trades, events)

    last_commentary = msg
    last_eval_time = datetime.now(timezone.utc).isoformat()

    # Send alert if there are notable events
    if events:
        tg_send(msg)
        logger.info(f"Alert sent: {len(events)} events")

    return msg


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def save_state():
    """Persist trade state to disk."""
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(config.STATE_FILE, "w") as f:
            json.dump(trade_state, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")


def load_state():
    """Load trade state from disk."""
    global trade_state
    if os.path.exists(config.STATE_FILE):
        try:
            with open(config.STATE_FILE) as f:
                raw = json.load(f)
                trade_state = {int(k): v for k, v in raw.items()}
            logger.info(f"Loaded state: {len(trade_state)} trades")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")


# ---------------------------------------------------------------------------
# HTTP server for /overseer and /evaluate integration
# ---------------------------------------------------------------------------
class OverseerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Silence access logs

    def do_GET(self):
        if self.path == "/overview":
            body = json.dumps({
                "last_commentary": last_commentary,
                "last_eval_time": last_eval_time,
                "open_trades": len(trade_state),
                "state": {str(k): v for k, v in trade_state.items()},
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        elif self.path == "/trades":
            trades = ft_client.get_all_trades()
            profits = ft_client.get_all_profits()
            body = json.dumps({"trades": trades, "profits": profits})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        elif self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/evaluate":
            # Run evaluation in a thread to not block HTTP
            result = run_evaluation(force=True)
            body = json.dumps({"commentary": result or "No trades to evaluate."})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        elif self.path == "/plays":
            # Receive plays from finance_agent
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length)) if length else {}
            plays_store.save_plays(data.get("raw_text", ""), data.get("market_context", ""))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()


def start_http_server():
    """Start HTTP server in background thread."""
    server = HTTPServer(("127.0.0.1", config.HTTP_PORT), OverseerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"HTTP server on :{config.HTTP_PORT}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    logger.info("Trade Overseer starting...")

    # Load persisted state
    load_state()

    # Start HTTP server
    start_http_server()

    # Check dependencies
    claude_ok = llm_client.is_available()
    logger.info(f"Claude Haiku: {'OK' if claude_ok else 'UNAVAILABLE (will use rules-only fallback)'}")

    for inst in config.FT_INSTANCES:
        ok = ft_client.is_available(inst)
        logger.info(f"Freqtrade {inst['name']}: {'OK' if ok else 'UNAVAILABLE'}")

    tg_send("*Overseer online* | Claude Haiku trade monitor active")

    # Offset from candle boundary by 30s
    time.sleep(30)

    while True:
        try:
            run_evaluation()
            save_state()
        except KeyboardInterrupt:
            logger.info("Shutting down.")
            save_state()
            break
        except Exception as e:
            logger.error(f"Poll error: {e}")

        time.sleep(config.POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
