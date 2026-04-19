"""
Sygnif Notification Handler — Webhook-based Freqtrade notifications.

Receives Freqtrade webhook events via HTTP POST and sends formatted
Telegram messages with TP/SL targets (entry) and Claude trade reviews (exit).

**Exchange webhook (generic HTTP ingest):** ``POST /webhook/exchange`` with JSON body.
Bybit does **not** ship a classic per-user HTTP trade callback; use this path when a
**forwarder** (n8n, Cloudflare Worker, custom WS bridge) posts normalized events.
Requires ``SYGNIF_EXCHANGE_WEBHOOK_TOKEN`` (``Authorization: Bearer`` or
``X-Sygnif-Exchange-Webhook-Token``). Appends one line to
``${SYGNIF_PREDICTION_AGENT_DIR:-<repo>/prediction_agent}/exchange_webhook_events.jsonl``.

Usage:
  python3 notification_handler.py              # Start on port 8089
  python3 notification_handler.py --port 9000  # Custom port

Configure in Freqtrade config.json:
  "webhook": {
    "enabled": true,
    "url": "http://notification-handler:8089/webhook",
    "webhookentry": { ... },
    "webhookentrycancel": { ... },
    "webhookexit": { ... },
    "webhookexitfill": { ... },
    "webhookstatus": { ... }
  }
"""

import argparse
import json
import logging
import os
import secrets
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_NL_URL = (os.environ.get("SYGNIF_NEUROLINKED_HTTP_URL") or "http://host.docker.internal:8888").rstrip("/")

def _nl_feed(text: str) -> None:
    if not _NL_URL:
        return
    try:
        requests.post(f"{_NL_URL}/api/input/text", json={"text": text}, timeout=2)
    except Exception:
        pass

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_TOKEN_FUTURES = os.environ.get("TELEGRAM_FUTURES_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Load .env if present
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", TG_TOKEN)
TG_TOKEN_FUTURES = os.environ.get("TELEGRAM_FUTURES_BOT_TOKEN", TG_TOKEN_FUTURES)
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", TG_CHAT)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)

# Aliases when env_file injects keys used elsewhere on the host (no /app/.env in the image).
if not (TG_TOKEN or "").strip():
    TG_TOKEN = os.environ.get("FINANCE_BOT_TOKEN", "").strip()
if not (TG_CHAT or "").strip():
    TG_CHAT = (
        os.environ.get("AGENT_CHAT_ID", "")
        or os.environ.get("TELEGRAM_FUTURES_CHAT_ID", "")
        or ""
    ).strip()


# ---------------------------------------------------------------------------
# Telegram sender
# ---------------------------------------------------------------------------

def tg_send(text, parse_mode="Markdown", is_futures=False):
    token = (TG_TOKEN_FUTURES or TG_TOKEN) if is_futures else TG_TOKEN
    if not token or not TG_CHAT:
        logger.warning("Telegram credentials not set, skipping send")
        return None
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": TG_CHAT,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        return resp.json()
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return None


# ---------------------------------------------------------------------------
# Exit reason mapping
# ---------------------------------------------------------------------------

EXIT_REASON_MAP = {
    "stoploss_on_exchange": "Exchange stoploss (doom)",
    "trailing_stop_loss": "Trailing exit (ratchet)",
    "sf_ema_tp": "Swing TP \u2014 EMA target hit",
    "sf_vol_sl": "Swing SL \u2014 volatility stop",
    "sf_short_ema_tp": "Swing short TP \u2014 EMA target hit",
    "sf_short_vol_sl": "Swing short SL \u2014 volatility stop",
    "momentum_fade": "Momentum fade",
    "rsi3_spike": "RSI momentum",
    "extreme_rsi": "Extreme RSI",
    "multi_tf": "Multi-TF",
    "bb_stretch": "Bollinger",
    "secure_profit": "Profit secured",
    "willr": "Williams %R signal",
    "profit_rsi": "RSI profit lock",
    "btc_risk": "BTC risk-off (guard)",
    "overbought": "Overbought",
    "oversold": "Oversold",
    "bounce": "Bounce",
    "conditional": "Conditional stoploss",
    "force_exit": "Manual force exit",
    "emergency": "Emergency exit",
    "liquidation": "Liquidation",
    "roi": "ROI target",
    "trail": "Trailing exit",
    "doom": "Max loss threshold",
    "_sl": "Hard stoploss",
}


def map_exit_reason(reason):
    if not reason:
        return "unknown"
    reason_lower = reason.lower()
    for key, desc in EXIT_REASON_MAP.items():
        if key in reason_lower:
            return desc
    return reason


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def fmt_coin(value, currency="USDT"):
    return f"{value:.4f} {currency}"


def fmt_price(value, currency="USDT"):
    if value >= 100:
        return f"{value:.2f} {currency}"
    elif value >= 1:
        return f"{value:.4f} {currency}"
    else:
        return f"{value:.6f} {currency}"


# ---------------------------------------------------------------------------
# Entry message
# ---------------------------------------------------------------------------

def format_entry_msg(msg):
    is_fill = msg.get("type", "") in ("entry_fill", "EntryFill")
    is_futures = msg.get("trading_mode", "") == "futures"
    rate = msg.get("open_rate", 0)
    stake = msg.get("stake_amount", 0)
    qc = msg.get("quote_currency", "USDT")
    tag = msg.get("enter_tag", "") or ""
    leverage = msg.get("leverage", 1) or 1
    direction = msg.get("direction", "Long") or "Long"
    is_short = msg.get("is_short", False)
    pair = msg.get("pair", "???")
    tid = msg.get("trade_id", "?")
    reason = tag if tag else "unknown"

    if not is_fill:
        lines = [f"\U0001f4cb *Order Placed* #{tid} `{pair}`"]
        lines.append(f"*Entry:* `{fmt_price(rate, qc)}`")
        if is_futures:
            exposure = stake * leverage
            lines.append(f"*Amount:* `{fmt_coin(stake, qc)}` ({leverage:.0f}x \u2192 `{fmt_coin(exposure, qc)}`)")
            de = "\U0001f4c8" if not is_short else "\U0001f4c9"
            lines.append(f"*Direction:* {de} {direction.upper()}")
        else:
            lines.append(f"*Amount:* `{fmt_coin(stake, qc)}`")
        lines.append(f"*Reason:* {reason}")
        return "\n".join(lines)

    # Order Filled
    if tag in (
        "swing_failure",
        "swing_failure_short",
        "fa_swing",
        "fa_swing_short",
        "sygnif_swing",
        "sygnif_swing_short",
        "claude_swing",
        "claude_swing_short",
    ):
        tp_pcts = [0.02, 0.03, 0.05, 0.08]
        sl_pct = 0.04
    else:
        tp_pcts = [0.01, 0.02, 0.05, 0.10]
        sl_pct = 0.10

    position = stake * leverage
    tp_lines = ""
    for p in tp_pcts:
        tp_price = rate * (1 - p) if is_short else rate * (1 + p)
        tp_usd = position * p
        tp_lines += f"  +{p*100:g}% \u2192 `{fmt_price(tp_price, qc)}` (+`{tp_usd:.2f}` {qc})\n"

    sl_price = rate * (1 + sl_pct) if is_short else rate * (1 - sl_pct)
    sl_usd = position * sl_pct
    min_win = position * tp_pcts[0]
    max_win = position * tp_pcts[-1]

    hdr = f"\u2705 *Filled* #{tid} `{pair}`"
    if is_futures:
        de = "\U0001f4c8" if not is_short else "\U0001f4c9"
        hdr += f" \u00b7 {de} {direction.upper()} {leverage:.0f}x"

    return (
        f"{hdr}\n"
        f"*Rate:* `{fmt_price(rate, qc)}` \u00b7 *Stake:* `{fmt_coin(stake, qc)}`\n"
        f"*Strategy:* {reason}\n"
        f"\n"
        f"*TP targets:*\n"
        f"{tp_lines}"
        f"*SL:* -{sl_pct*100:g}% \u2192 `{fmt_price(sl_price, qc)}` (-`{sl_usd:.2f}` {qc})\n"
        f"\n"
        f"*Expected win:* `+{min_win:.2f}` to `+{max_win:.2f}` {qc}\n"
        f"*Possible loss:* `-{sl_usd:.2f}` {qc}"
    )


# ---------------------------------------------------------------------------
# Claude trade review
# ---------------------------------------------------------------------------

def claude_review(msg, desc, dur_str, pnl_str, pct_str):
    if not ANTHROPIC_API_KEY:
        return _fallback_review(msg, desc)

    pair = msg.get("pair", "???")
    direction = msg.get("direction", "Long") or "Long"
    leverage = msg.get("leverage", 1) or 1
    strategy = msg.get("enter_tag", "") or "unknown"
    open_rate = msg.get("open_rate", 0)
    close_rate = msg.get("close_rate", 0)

    prompt = (
        f"You are a trade analyst. Review this closed crypto trade in 2-3 short sentences. "
        f"Be direct, use numbers, no disclaimers.\n\n"
        f"Pair: {pair}, Direction: {direction}, Leverage: {leverage:.0f}x\n"
        f"Strategy: {strategy}\n"
        f"Entry: ${open_rate:.4g} \u2192 Exit: ${close_rate:.4g}\n"
        f"P/L: {pct_str} ({pnl_str})\n"
        f"Duration: {dur_str}\n"
        f"Exit reason: {desc}\n"
        f"Was this a good trade? What went right or wrong?"
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 150,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=8,
        )
        if resp.ok:
            return resp.json()["content"][0]["text"]
    except Exception:
        pass

    return _fallback_review(msg, desc)


def _fallback_review(msg, desc):
    profit_ratio = msg.get("profit_ratio", 0)
    if profit_ratio > 0:
        return f"\u2705 {desc} \u2014 clean exit."
    elif "doom" in (msg.get("exit_reason", "") or "").lower():
        return f"\u274c {desc} \u2014 hit max loss limit."
    else:
        return f"\u274c {desc} \u2014 position went against thesis."


# ---------------------------------------------------------------------------
# Exit message
# ---------------------------------------------------------------------------

def format_exit_msg(msg):
    is_fill = msg.get("type", "") in ("exit_fill", "ExitFill")
    if not is_fill:
        return None  # non-fill exits not formatted

    qc = msg.get("quote_currency", "USDT")
    profit_amount = msg.get("profit_amount", 0)
    profit_ratio = msg.get("profit_ratio", 0)
    pnl_str = fmt_coin(profit_amount, qc)
    pct_str = f"{profit_ratio:+.2%}"
    exit_reason = msg.get("exit_reason", "unknown")
    open_rate = msg.get("open_rate", 0)
    close_rate = msg.get("close_rate", 0)
    leverage = msg.get("leverage", 1) or 1
    direction = msg.get("direction", "Long") or "Long"
    is_short = msg.get("is_short", False)
    is_futures = msg.get("trading_mode", "") == "futures"
    enter_tag = msg.get("enter_tag", "") or "unknown"

    # Duration
    open_date = msg.get("open_date", "")
    close_date = msg.get("close_date", "")
    dur_str = _calc_duration(open_date, close_date)

    # Emoji
    emoji = "\U0001f7e2" if profit_ratio >= 0 else "\U0001f534"

    # Header
    hdr = f"{emoji} *Closed* #{msg.get('trade_id', '?')} `{msg.get('pair', '???')}`"
    if is_futures:
        de = "\U0001f4c8" if not is_short else "\U0001f4c9"
        hdr += f" \u00b7 {de} {direction.upper()} {leverage:.0f}x"

    desc = map_exit_reason(exit_reason)
    review = claude_review(msg, desc, dur_str, pnl_str, pct_str)

    return (
        f"{hdr}\n"
        f"*P/L:* `{pct_str}` (`{pnl_str}`)\n"
        f"*Entry:* `{fmt_price(open_rate, qc)}` \u2192 *Exit:* `{fmt_price(close_rate, qc)}`\n"
        f"*Duration:* `{dur_str}`\n"
        f"\n"
        f"\U0001f4c8 *Strategy:* {enter_tag}\n"
        f"\U0001f6aa *Exit:* {desc}\n"
        f"\n"
        f"\U0001f4ac *Review:*\n"
        f"{review}"
    )


def _calc_duration(open_date, close_date):
    try:
        if isinstance(open_date, str):
            open_dt = datetime.fromisoformat(open_date.replace("Z", "+00:00"))
        else:
            open_dt = open_date
        if isinstance(close_date, str):
            close_dt = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
        else:
            close_dt = close_date
        dur_s = int((close_dt - open_dt).total_seconds())
        if dur_s >= 3600:
            return f"{dur_s // 3600}h{(dur_s % 3600) // 60:02d}m"
        elif dur_s >= 60:
            return f"{dur_s // 60}m"
        else:
            return f"{dur_s}s"
    except Exception:
        return "--"


# ---------------------------------------------------------------------------
# Status message
# ---------------------------------------------------------------------------

def format_status_msg(msg):
    status = msg.get("status", "")
    if status == "running":
        return "\u2705 *System up.*"
    elif "died" in status or "stop" in status:
        return "\U0001f6ab *System down.*"
    return None  # suppress other statuses


# ---------------------------------------------------------------------------
# Exchange webhook (generic JSON ingest)
# ---------------------------------------------------------------------------


def _prediction_agent_dir() -> str:
    for key in ("SYGNIF_PREDICTION_AGENT_DIR", "PREDICTION_AGENT_DIR"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return os.path.expanduser(raw)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "prediction_agent")


def _exchange_webhook_token_from_headers(handler: BaseHTTPRequestHandler) -> str:
    auth = (handler.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (handler.headers.get("X-Sygnif-Exchange-Webhook-Token") or "").strip()


def _exchange_webhook_auth_ok(handler: BaseHTTPRequestHandler) -> tuple[bool, str]:
    expected = (os.environ.get("SYGNIF_EXCHANGE_WEBHOOK_TOKEN") or "").strip()
    if not expected:
        return False, "SYGNIF_EXCHANGE_WEBHOOK_TOKEN_unset"
    got = _exchange_webhook_token_from_headers(handler)
    if not got:
        return False, "missing_Authorization_Bearer_or_X-Sygnif-Exchange-Webhook-Token"
    if not secrets.compare_digest(got, expected):
        return False, "invalid_token"
    return True, ""


def _append_exchange_webhook_event(envelope: dict[str, Any]) -> None:
    d = _prediction_agent_dir()
    os.makedirs(d, mode=0o755, exist_ok=True)
    path = os.path.join(d, "exchange_webhook_events.jsonl")
    line = json.dumps(envelope, ensure_ascii=False, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


def _handle_exchange_webhook(handler: BaseHTTPRequestHandler, body: bytes) -> None:
    ok, why = _exchange_webhook_auth_ok(handler)
    if not ok:
        status = 503 if why == "SYGNIF_EXCHANGE_WEBHOOK_TOKEN_unset" else 401
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        err = json.dumps({"ok": False, "error": why}).encode("utf-8")
        handler.send_header("Content-Length", str(len(err)))
        handler.end_headers()
        handler.wfile.write(err)
        return
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        handler.send_response(400)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        err = json.dumps({"ok": False, "error": "invalid_json"}).encode("utf-8")
        handler.send_header("Content-Length", str(len(err)))
        handler.end_headers()
        handler.wfile.write(err)
        return
    if not isinstance(payload, dict):
        handler.send_response(400)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        err = json.dumps({"ok": False, "error": "json_must_be_object"}).encode("utf-8")
        handler.send_header("Content-Length", str(len(err)))
        handler.end_headers()
        handler.wfile.write(err)
        return

    client = (handler.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if not client:
        client = handler.client_address[0] if handler.client_address else ""
    envelope = {
        "received_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "remote_ip": client,
        "payload": payload,
    }
    try:
        _append_exchange_webhook_event(envelope)
    except OSError as exc:
        logger.error("exchange webhook append failed: %s", exc)
        handler.send_response(500)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        err = json.dumps({"ok": False, "error": "persist_failed", "detail": str(exc)[:200]}).encode(
            "utf-8"
        )
        handler.send_header("Content-Length", str(len(err)))
        handler.end_headers()
        handler.wfile.write(err)
        return

    out = json.dumps({"ok": True, "persisted": True}).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(out)))
    handler.end_headers()
    handler.wfile.write(out)


# ---------------------------------------------------------------------------
# Webhook HTTP handler
# ---------------------------------------------------------------------------

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        if path == "/webhook/exchange":
            _handle_exchange_webhook(self, body)
            return

        if path != "/webhook":
            self.send_response(404)
            self.end_headers()
            return

        try:
            msg = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

        _process_webhook(msg)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "healthy"}')

    def log_message(self, format, *args):
        logger.debug(format % args)


def _process_webhook(msg):
    msg_type = msg.get("type", "").lower()
    is_futures = msg.get("trading_mode", "") == "futures"
    text = None

    if "entry" in msg_type:
        text = format_entry_msg(msg)
    elif "exit" in msg_type:
        text = format_exit_msg(msg)
    elif "status" in msg_type:
        text = format_status_msg(msg)

    if text:
        tg_send(text, is_futures=is_futures)
        logger.info(f"Sent {msg_type} notification")

    if "entry" in msg_type:
        pair = msg.get("pair", "?")
        side = "SHORT" if msg.get("is_short") else "LONG"
        stake = msg.get("stake_amount", 0)
        _nl_feed(f"TRADE ENTRY {side} {pair} stake={stake:.2f} mode={'futures' if is_futures else 'spot'}")
    elif "exit" in msg_type:
        pair = msg.get("pair", "?")
        pnl = msg.get("profit_percent", msg.get("profit_ratio", 0)) * 100
        reason = msg.get("exit_reason", "?")
        outcome = "WIN" if pnl > 0 else "LOSS"
        _nl_feed(f"TRADE EXIT {outcome} {pair} pnl={pnl:+.2f}% reason={reason} mode={'futures' if is_futures else 'spot'}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Sygnif Notification Handler")
    parser.add_argument("--port", type=int, default=8089, help="HTTP port (default: 8089)")
    args = parser.parse_args()

    if not TG_TOKEN or not TG_CHAT:
        logger.warning(
            "TELEGRAM_BOT_TOKEN (or FINANCE_BOT_TOKEN) and TELEGRAM_CHAT_ID (or AGENT_CHAT_ID) "
            "are unset — webhooks accepted but Telegram sends are skipped until configured."
        )
    if TG_TOKEN_FUTURES:
        logger.info("Futures bot token loaded — routing futures messages to @sygnifuture_bot")
    else:
        logger.warning("TELEGRAM_FUTURES_BOT_TOKEN not set — futures messages will use spot bot")

    server = HTTPServer(("0.0.0.0", args.port), WebhookHandler)
    logger.info(f"Notification handler started on port {args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopped")
        server.server_close()


if __name__ == "__main__":
    main()
