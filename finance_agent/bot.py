#!/usr/bin/env python3
"""
Sygnif Finance Agent — Telegram bot for crypto research & analysis.
Combines market scanning, technical analysis, and AI-powered insights.

Commands:
  /market          — Top 10 crypto overview
  /movers [1h|24h] — Top gainers & losers
  /ta <TICKER>     — Technical analysis with indicators
  /research <TICK> — Full research (market + TA + news + AI)
  /plays           — AI investment opportunity scan
  /news            — Latest crypto headlines
  /fa_help         — Show commands
"""

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone

import feedparser
import numpy as np
import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("finance_agent")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TG_TOKEN = os.environ.get("FINANCE_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
BYBIT = "https://api.bybit.com/v5"


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
def tg_send(text: str, parse_mode: str = "Markdown"):
    """Send a Telegram message, auto-split if too long."""
    MAX = 4000
    chunks = [text[i : i + MAX] for i in range(0, len(text), MAX)]
    for chunk in chunks:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={
                    "chat_id": TG_CHAT,
                    "text": chunk,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
        except Exception as e:
            logger.error(f"tg_send error: {e}")


def tg_poll(offset: int) -> tuple[list, int]:
    """Poll Telegram for new messages."""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35,
        )
        updates = resp.json().get("result", [])
        for u in updates:
            offset = max(offset, u["update_id"] + 1)
        return updates, offset
    except Exception as e:
        logger.error(f"Poll error: {e}")
        return [], offset


# ---------------------------------------------------------------------------
# Data: Bybit API
# ---------------------------------------------------------------------------
def bybit_tickers() -> list[dict]:
    """Fetch all spot tickers from Bybit."""
    try:
        resp = requests.get(f"{BYBIT}/market/tickers", params={"category": "spot"}, timeout=10)
        return resp.json().get("result", {}).get("list", [])
    except Exception as e:
        logger.error(f"Bybit tickers error: {e}")
        return []


def bybit_kline(symbol: str, interval: str = "60", limit: int = 200) -> pd.DataFrame:
    """Fetch OHLCV from Bybit. interval: 1,3,5,15,30,60,120,240,360,720,D,W."""
    try:
        resp = requests.get(
            f"{BYBIT}/market/kline",
            params={"category": "spot", "symbol": symbol, "interval": interval, "limit": limit},
            timeout=10,
        )
        rows = resp.json().get("result", {}).get("list", [])
        if not rows:
            return pd.DataFrame()
        # Bybit returns [ts, open, high, low, close, volume, turnover] newest first
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
        for c in ["open", "high", "low", "close", "volume", "turnover"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
        df = df.sort_values("ts").reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"Bybit kline error: {e}")
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Data: News (RSS)
# ---------------------------------------------------------------------------
def fetch_news(token: str = "", max_items: int = 7) -> list[str]:
    """Fetch crypto news headlines from RSS feeds."""
    feeds = [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
    ]
    if token:
        feeds.insert(0, f"https://cryptopanic.com/news/{token.lower()}/rss/")

    headlines = []
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = entry.get("title", "").strip()
                source = feed.feed.get("title", url.split("/")[2])
                if title:
                    headlines.append(f"{title} — _{source}_")
        except Exception:
            continue
    # Deduplicate by title prefix
    seen = set()
    unique = []
    for h in headlines:
        key = h[:40].lower()
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique[:max_items]


# ---------------------------------------------------------------------------
# TA: Calculate indicators from OHLCV DataFrame
# ---------------------------------------------------------------------------
def calc_indicators(df: pd.DataFrame) -> dict:
    """Calculate technical indicators. Returns dict of values."""
    if len(df) < 50:
        return {}
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # EMAs
    ema9 = close.ewm(span=9).mean()
    ema21 = close.ewm(span=21).mean()
    ema50 = close.ewm(span=50).mean()
    ema200 = close.ewm(span=200).mean() if len(df) >= 200 else pd.Series(dtype=float)

    # RSI 14
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    # Bollinger Bands 20
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20

    # MACD
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9).mean()
    macd_hist = macd_line - macd_signal

    # Volume SMA
    vol_sma = volume.rolling(20).mean()

    # Support / Resistance (simple: recent swing low/high)
    recent = df.tail(48)
    support = recent["low"].min()
    resistance = recent["high"].max()

    last = close.iloc[-1]
    prev = close.iloc[-2]

    result = {
        "price": last,
        "prev_close": prev,
        "change_pct": (last - prev) / prev * 100 if prev else 0,
        "ema9": ema9.iloc[-1],
        "ema21": ema21.iloc[-1],
        "ema50": ema50.iloc[-1],
        "ema200": ema200.iloc[-1] if len(ema200) > 0 else None,
        "rsi": rsi.iloc[-1],
        "bb_upper": bb_upper.iloc[-1],
        "bb_lower": bb_lower.iloc[-1],
        "bb_mid": sma20.iloc[-1],
        "macd": macd_line.iloc[-1],
        "macd_signal": macd_signal.iloc[-1],
        "macd_hist": macd_hist.iloc[-1],
        "volume": volume.iloc[-1],
        "vol_avg": vol_sma.iloc[-1],
        "support": support,
        "resistance": resistance,
        "high_24": recent["high"].max(),
        "low_24": recent["low"].min(),
    }

    # Trend
    if last > ema9.iloc[-1] > ema21.iloc[-1] > ema50.iloc[-1]:
        result["trend"] = "Strong Uptrend"
    elif last > ema21.iloc[-1]:
        result["trend"] = "Uptrend"
    elif last < ema9.iloc[-1] < ema21.iloc[-1] < ema50.iloc[-1]:
        result["trend"] = "Strong Downtrend"
    elif last < ema21.iloc[-1]:
        result["trend"] = "Downtrend"
    else:
        result["trend"] = "Sideways"

    # RSI interpretation
    r = result["rsi"]
    if r > 70:
        result["rsi_signal"] = "Overbought"
    elif r < 30:
        result["rsi_signal"] = "Oversold"
    else:
        result["rsi_signal"] = "Neutral"

    # MACD signal
    if result["macd_hist"] > 0 and macd_hist.iloc[-2] <= 0:
        result["macd_signal_text"] = "Bullish Cross"
    elif result["macd_hist"] < 0 and macd_hist.iloc[-2] >= 0:
        result["macd_signal_text"] = "Bearish Cross"
    elif result["macd_hist"] > 0:
        result["macd_signal_text"] = "Bullish"
    else:
        result["macd_signal_text"] = "Bearish"

    # BB position
    bb_range = result["bb_upper"] - result["bb_lower"]
    if bb_range > 0:
        bb_pct = (last - result["bb_lower"]) / bb_range
        result["bb_position"] = f"{bb_pct:.0%}"
    else:
        result["bb_position"] = "N/A"

    return result


# ---------------------------------------------------------------------------
# Claude Haiku — AI analysis
# ---------------------------------------------------------------------------
def claude_analyze(prompt: str, max_tokens: int = 1500) -> str:
    """Call Claude Haiku for analysis."""
    if not ANTHROPIC_KEY:
        return "_Claude API key not configured._"
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        if resp.ok:
            return resp.json()["content"][0]["text"]
        logger.error(f"Claude error: {resp.status_code}")
        return "_Analysis unavailable._"
    except Exception as e:
        logger.error(f"Claude error: {e}")
        return "_Analysis unavailable._"


# ---------------------------------------------------------------------------
# Command: /market — Top crypto overview
# ---------------------------------------------------------------------------
def cmd_market():
    tg_send("Fetching market data...")
    tickers = bybit_tickers()
    if not tickers:
        tg_send("Failed to fetch market data.")
        return

    # Filter USDT pairs, sort by turnover
    pairs = []
    exclude = {"USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD", "USDP", "USDS", "USDE"}
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = sym.replace("USDT", "")
        if base in exclude or any(x in base for x in ("2L", "3L", "5L", "2S", "3S", "5S")):
            continue
        try:
            price = float(t.get("lastPrice", 0))
            change = float(t.get("price24hPcnt", 0)) * 100
            turnover = float(t.get("turnover24h", 0))
        except (ValueError, TypeError):
            continue
        if turnover < 1_000_000:
            continue
        pairs.append({"sym": base, "price": price, "change": change, "vol": turnover})

    top = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:15]

    lines = ["*Crypto Market Overview*\n"]
    for p in top:
        arrow = "+" if p["change"] >= 0 else ""
        vol_m = p["vol"] / 1e6
        # Format price based on magnitude
        if p["price"] >= 100:
            pf = f"${p['price']:,.0f}"
        elif p["price"] >= 1:
            pf = f"${p['price']:.2f}"
        else:
            pf = f"${p['price']:.5f}"
        lines.append(f"`{p['sym']:>6}` {pf:>12} `{arrow}{p['change']:.1f}%` Vol `${vol_m:.0f}M`")

    lines.append(f"\n_{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    tg_send("\n".join(lines))


# ---------------------------------------------------------------------------
# Command: /movers — Top gainers & losers
# ---------------------------------------------------------------------------
def cmd_movers():
    tickers = bybit_tickers()
    if not tickers:
        tg_send("Failed to fetch data.")
        return

    pairs = []
    exclude = {"USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD", "USDP", "USDS", "USDE"}
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = sym.replace("USDT", "")
        if base in exclude or any(x in base for x in ("2L", "3L", "5L", "2S", "3S", "5S")):
            continue
        try:
            change = float(t.get("price24hPcnt", 0)) * 100
            turnover = float(t.get("turnover24h", 0))
            price = float(t.get("lastPrice", 0))
        except (ValueError, TypeError):
            continue
        if turnover < 500_000:
            continue
        pairs.append({"sym": base, "price": price, "change": change, "vol": turnover})

    by_change = sorted(pairs, key=lambda x: x["change"], reverse=True)
    gainers = by_change[:5]
    losers = by_change[-5:][::-1]

    lines = ["*Top Movers (24h)*\n"]
    lines.append("*Gainers:*")
    for i, g in enumerate(gainers, 1):
        lines.append(f"  {i}. `{g['sym']}` +{g['change']:.1f}% (${g['price']:.4g})")
    lines.append("\n*Losers:*")
    for i, l in enumerate(losers, 1):
        lines.append(f"  {i}. `{l['sym']}` {l['change']:.1f}% (${l['price']:.4g})")

    lines.append(f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_")
    tg_send("\n".join(lines))


# ---------------------------------------------------------------------------
# Command: /ta <TICKER> — Technical analysis
# ---------------------------------------------------------------------------
def cmd_ta(ticker: str):
    ticker = ticker.upper().strip()
    if not ticker:
        ticker = "BTC"
    symbol = f"{ticker}USDT"
    tg_send(f"Analyzing `{ticker}`...")

    # Fetch 1h candles
    df = bybit_kline(symbol, interval="60", limit=200)
    if df.empty:
        tg_send(f"No data for `{ticker}`. Check ticker symbol.")
        return

    ind = calc_indicators(df)
    if not ind:
        tg_send(f"Not enough data for `{ticker}`.")
        return

    p = ind["price"]
    pf = f"${p:,.2f}" if p >= 1 else f"${p:.6f}"

    ema200_line = ""
    if ind["ema200"] is not None:
        e200 = ind["ema200"]
        ema200_line = f"  EMA 200: `{e200:.4g}` {'(above)' if p > e200 else '(below)'}\n"

    msg = (
        f"*Technical Analysis: {ticker}*\n"
        f"*Price:* `{pf}`\n\n"
        f"*Trend:* `{ind['trend']}`\n"
        f"*EMAs:*\n"
        f"  EMA 9: `{ind['ema9']:.4g}`\n"
        f"  EMA 21: `{ind['ema21']:.4g}`\n"
        f"  EMA 50: `{ind['ema50']:.4g}`\n"
        f"{ema200_line}\n"
        f"*RSI 14:* `{ind['rsi']:.1f}` — {ind['rsi_signal']}\n"
        f"*MACD:* `{ind['macd']:.4g}` — {ind['macd_signal_text']}\n"
        f"  Histogram: `{ind['macd_hist']:.4g}`\n\n"
        f"*Bollinger Bands:*\n"
        f"  Upper: `{ind['bb_upper']:.4g}`\n"
        f"  Mid: `{ind['bb_mid']:.4g}`\n"
        f"  Lower: `{ind['bb_lower']:.4g}`\n"
        f"  Position: `{ind['bb_position']}`\n\n"
        f"*Key Levels:*\n"
        f"  Support: `{ind['support']:.4g}`\n"
        f"  Resistance: `{ind['resistance']:.4g}`\n\n"
        f"*Volume:* `{ind['volume']:,.0f}` (avg `{ind['vol_avg']:,.0f}`)\n"
        f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')} · 1h candles_"
    )
    tg_send(msg)


# ---------------------------------------------------------------------------
# Command: /research <TICKER> — Full AI research
# ---------------------------------------------------------------------------
def cmd_research(ticker: str):
    ticker = ticker.upper().strip() or "BTC"
    symbol = f"{ticker}USDT"
    tg_send(f"Researching `{ticker}`... (TA + News + AI analysis)")

    # 1. Fetch market data
    df = bybit_kline(symbol, interval="60", limit=200)
    ind = calc_indicators(df) if not df.empty else {}

    # 2. Fetch news
    headlines = fetch_news(ticker)
    news_text = "\n".join(f"- {h}" for h in headlines[:5]) if headlines else "No recent news."

    # 3. Price from tickers
    tickers = bybit_tickers()
    pair_data = next((t for t in tickers if t.get("symbol") == symbol), {})
    price = float(pair_data.get("lastPrice", 0))
    change_24h = float(pair_data.get("price24hPcnt", 0)) * 100
    vol_24h = float(pair_data.get("turnover24h", 0))

    # 4. Build prompt for Claude
    ta_summary = "No TA data available."
    if ind:
        ta_summary = (
            f"Price: ${ind['price']:.4g}, Trend: {ind['trend']}, "
            f"RSI: {ind['rsi']:.1f} ({ind['rsi_signal']}), "
            f"MACD: {ind['macd_signal_text']} (hist: {ind['macd_hist']:.4g}), "
            f"BB position: {ind['bb_position']}, "
            f"Support: {ind['support']:.4g}, Resistance: {ind['resistance']:.4g}, "
            f"EMA9: {ind['ema9']:.4g}, EMA50: {ind['ema50']:.4g}, "
            f"Volume: {ind['volume']:,.0f} vs avg {ind['vol_avg']:,.0f}"
        )

    prompt = f"""You are a crypto research analyst. Provide a concise research report for {ticker}.

CURRENT DATA:
- Price: ${price:.4g} (24h: {change_24h:+.1f}%)
- 24h Volume: ${vol_24h/1e6:.1f}M
- Technical Analysis: {ta_summary}

RECENT NEWS:
{news_text}

Write a concise research report in Markdown with these sections:
1. **Market Status** (2 sentences: price action + trend)
2. **Technical Outlook** (3-4 bullet points: key indicator signals)
3. **News & Sentiment** (2-3 bullet points: what headlines suggest)
4. **Verdict** (1 paragraph: bullish/bearish/neutral with reasoning and key levels to watch)

Keep it under 300 words. Be specific with numbers. No disclaimers."""

    analysis = claude_analyze(prompt)

    msg = (
        f"*Research Report: {ticker}*\n"
        f"*Price:* `${price:.4g}` (`{change_24h:+.1f}%` 24h)\n\n"
        f"{analysis}\n\n"
        f"_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
    )
    tg_send(msg)


# ---------------------------------------------------------------------------
# Command: /plays — AI investment opportunities
# ---------------------------------------------------------------------------
def cmd_plays():
    tg_send("Scanning for opportunities...")

    # Gather market context
    tickers = bybit_tickers()
    pairs = []
    exclude = {"USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD", "USDP", "USDS", "USDE"}
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = sym.replace("USDT", "")
        if base in exclude or any(x in base for x in ("2L", "3L", "5L", "2S", "3S", "5S")):
            continue
        try:
            change = float(t.get("price24hPcnt", 0)) * 100
            turnover = float(t.get("turnover24h", 0))
            price = float(t.get("lastPrice", 0))
        except (ValueError, TypeError):
            continue
        if turnover < 1_000_000:
            continue
        pairs.append({"sym": base, "price": price, "change": change, "vol": turnover})

    top_by_vol = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:10]
    top_gainers = sorted(pairs, key=lambda x: x["change"], reverse=True)[:5]
    top_losers = sorted(pairs, key=lambda x: x["change"])[:5]

    market_ctx = "Top by volume:\n"
    for p in top_by_vol:
        market_ctx += f"  {p['sym']}: ${p['price']:.4g} ({p['change']:+.1f}%) vol ${p['vol']/1e6:.0f}M\n"
    market_ctx += "\nTop gainers:\n"
    for p in top_gainers:
        market_ctx += f"  {p['sym']}: +{p['change']:.1f}%\n"
    market_ctx += "\nTop losers:\n"
    for p in top_losers:
        market_ctx += f"  {p['sym']}: {p['change']:.1f}%\n"

    # Fetch BTC TA for macro context
    btc_df = bybit_kline("BTCUSDT", "60", 200)
    btc_ind = calc_indicators(btc_df) if not btc_df.empty else {}
    btc_ctx = ""
    if btc_ind:
        btc_ctx = (
            f"\nBTC Context: ${btc_ind['price']:,.0f}, {btc_ind['trend']}, "
            f"RSI {btc_ind['rsi']:.0f}, MACD {btc_ind['macd_signal_text']}"
        )

    prompt = f"""You are a crypto investment strategist. Based on current market data, provide exactly 3 actionable investment plays.

MARKET DATA:
{market_ctx}
{btc_ctx}

For each play, use this format:
**Play #N: [Name]**
Type: Spot Buy | Mean Reversion | Momentum
Risk: Low/Medium/High
- *Thesis:* Why this opportunity exists (1-2 sentences)
- *Entry:* Specific price or condition
- *TP:* Target price and expected %
- *SL:* Stop loss price and %
- *Timeframe:* Hours/Days/Weeks

Keep each play to 4-5 lines. Be specific with prices. No disclaimers. Total under 400 words."""

    analysis = claude_analyze(prompt, max_tokens=2000)
    msg = f"*Investment Plays*\n\n{analysis}\n\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
    tg_send(msg)

    # Save plays for trade overseer
    try:
        requests.post(
            "http://127.0.0.1:8090/plays",
            json={"raw_text": analysis, "market_context": market_ctx},
            timeout=3,
        )
    except Exception:
        pass  # Overseer may not be running


# ---------------------------------------------------------------------------
# Command: /news — Latest headlines
# ---------------------------------------------------------------------------
def cmd_news(ticker: str = ""):
    headlines = fetch_news(ticker.upper().strip() if ticker else "")
    if not headlines:
        tg_send("Could not fetch news.")
        return
    title = f"*Crypto News*" + (f" ({ticker.upper()})" if ticker else "")
    lines = [title, ""]
    for i, h in enumerate(headlines, 1):
        lines.append(f"{i}. {h}")
    lines.append(f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_")
    tg_send("\n".join(lines))


# ---------------------------------------------------------------------------
# Command: /fa_help
# ---------------------------------------------------------------------------
def cmd_help():
    tg_send(
        "*Sygnif Finance Agent*\n\n"
        "`/market` — Top 15 crypto overview\n"
        "`/movers` — Gainers & losers (24h)\n"
        "`/ta BTC` — Technical analysis\n"
        "`/research ETH` — Full AI research report\n"
        "`/plays` — AI investment opportunities\n"
        "`/news` — Latest crypto headlines\n"
        "`/news SOL` — News for specific coin\n"
        "`/overseer` — Trade overseer status\n"
        "`/evaluate` — Force trade evaluation (Plutus-3B)\n"
        "`/fa_help` — This message"
    )


# ---------------------------------------------------------------------------
# Command: /overseer — Trade overseer overview
# ---------------------------------------------------------------------------
def cmd_overseer():
    try:
        resp = requests.get("http://127.0.0.1:8090/overview", timeout=5)
        data = resp.json()
        commentary = data.get("last_commentary", "")
        if commentary:
            tg_send(commentary)
        else:
            tg_send(f"*Overseer* | {data.get('open_trades', 0)} trades tracked, no recent alerts.")
    except Exception as e:
        tg_send(f"Overseer unavailable: {e}")


# ---------------------------------------------------------------------------
# Command: /evaluate — Force trade evaluation
# ---------------------------------------------------------------------------
def cmd_evaluate():
    tg_send("Evaluating positions...")
    try:
        resp = requests.post("http://127.0.0.1:8090/evaluate", timeout=120)
        data = resp.json()
        commentary = data.get("commentary", "No output.")
        tg_send(commentary)
    except Exception as e:
        tg_send(f"Evaluation failed: {e}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
COMMANDS = {
    "/market": lambda args: cmd_market(),
    "/movers": lambda args: cmd_movers(),
    "/ta": lambda args: cmd_ta(args),
    "/research": lambda args: cmd_research(args),
    "/plays": lambda args: cmd_plays(),
    "/news": lambda args: cmd_news(args),
    "/overseer": lambda args: cmd_overseer(),
    "/evaluate": lambda args: cmd_evaluate(),
    "/fa_help": lambda args: cmd_help(),
}


def handle_message(text: str, chat_id: str):
    """Route incoming message to command handler."""
    if str(chat_id) != str(TG_CHAT):
        return

    text = text.strip()
    if not text.startswith("/"):
        return

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]  # strip @botname suffix
    args = parts[1] if len(parts) > 1 else ""

    if cmd in COMMANDS:
        try:
            COMMANDS[cmd](args)
        except Exception as e:
            logger.error(f"Command {cmd} error: {traceback.format_exc()}")
            tg_send(f"Error: {e}")


def main():
    if not TG_TOKEN:
        print("Set FINANCE_BOT_TOKEN env var")
        sys.exit(1)
    if not TG_CHAT:
        print("Set TELEGRAM_CHAT_ID env var")
        sys.exit(1)

    logger.info("Finance Agent started. Polling for commands...")
    tg_send("Finance Agent online. Send `/fa_help` for commands.")

    offset = 0
    while True:
        try:
            updates, offset = tg_poll(offset)
            for update in updates:
                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if text:
                    handle_message(text, chat_id)
        except KeyboardInterrupt:
            logger.info("Shutting down.")
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
