#!/usr/bin/env python3
"""
Sygnif Finance Agent — Telegram bot for crypto research & analysis.
Combines market scanning, technical analysis, and AI-powered insights.
Strategy-aware: computes the same TA score and detects entry/exit signals
as SygnifStrategy.py so research aligns with live bot behavior.

Commands:
  /market          — Top 10 crypto overview
  /movers [1h|24h] — Top gainers & losers
  /ta <TICKER>     — Technical analysis with strategy signals
  /research <TICK> — Full research (market + TA + news + AI)
  /plays           — AI investment opportunity scan
  /signals         — Quick scan: active entry signals across top pairs
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
from pathlib import Path

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
# Trade overseer HTTP (host:8090 when overseer publishes to localhost)
_SYGNIF_OVERSEER_HTTP = os.environ.get("SYGNIF_OVERSEER_HTTP", "http://127.0.0.1:8090").rstrip("/")

# Optional: FinancialData.net (query param key=) — see https://financialdata.net/documentation
_FINANCIALDATA_API_KEY = (
    os.environ.get("FINANCIALDATA_API_KEY", "").strip()
    or os.environ.get("FINANCIAL_DATA_API_KEY", "").strip()
)
_FINANCIALDATA_API_BASE = os.environ.get(
    "FINANCIALDATA_API_BASE", "https://financialdata.net/api/v1"
).rstrip("/")

# Repo layout on servers: finance_agent lives next to Freqtrade `user_data/strategies/`.
_repo_root = Path(__file__).resolve().parents[1]
_strategies_dir = _repo_root / "user_data" / "strategies"
if not _strategies_dir.is_dir():
    alt = Path("/home/ubuntu/xrp_claude_bot/user_data/strategies")
    if alt.is_dir():
        _strategies_dir = alt
if _strategies_dir.is_dir() and str(_strategies_dir) not in sys.path:
    sys.path.insert(0, str(_strategies_dir))

from sygnif_ta_score import TA_SPEC_VERSION, ta_score_from_indicator_dict, ta_spec_fingerprint

# Strategy constants (mirrors SygnifStrategy.py)
MAJOR_PAIRS = {"BTC", "ETH", "SOL", "XRP"}
LEVERAGE_MAJORS = 5.0
LEVERAGE_DEFAULT = 3.0


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
def _tg_send_chunk(
    chunk: str,
    *,
    parse_mode: str | None,
    reply_markup: dict | None,
) -> bool:
    """Return True if Telegram accepted the message."""
    payload: dict = {
        "chat_id": TG_CHAT,
        "text": chunk,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json=payload,
            timeout=15,
        )
        data = r.json() if r.content else {}
        if r.ok and data.get("ok"):
            return True
        logger.warning(
            "telegram_send fail status=%s ok=%s desc=%r",
            r.status_code,
            data.get("ok"),
            (data.get("description") or "")[:220],
        )
        return False
    except Exception as e:
        logger.error(f"tg_send error: {e}")
        return False


def tg_send(text: str, parse_mode: str = "Markdown", reply_markup: dict | None = None):
    """Send a Telegram message, auto-split if too long.

    Legacy Markdown breaks on many real-world strings (news, LLM output). If parsing fails,
    retry the same chunk as plain text so users always get a reply.
    """
    MAX = 4000
    chunks = [text[i : i + MAX] for i in range(0, len(text), MAX)]
    for i, chunk in enumerate(chunks):
        last_kb = reply_markup if reply_markup and i == len(chunks) - 1 else None
        if parse_mode:
            if not _tg_send_chunk(chunk, parse_mode=parse_mode, reply_markup=last_kb):
                _tg_send_chunk(chunk, parse_mode=None, reply_markup=last_kb)
        else:
            _tg_send_chunk(chunk, parse_mode=None, reply_markup=last_kb)


# Persistent reply keyboard — shown at bottom of chat
KEYBOARD = {
    "keyboard": [
        ["/sygnif", "/overview", "/tendency"],
        ["/signals", "/scan", "/ta BTC"],
        ["/plays", "/market", "/movers"],
        ["/deduce", "/ask", "/fa_help"],
        ["/news", "/evaluate", "/finance-agent"],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
}


# Slash aliases (same as Cursor / older keyboards) → canonical /command
_COMMAND_ALIASES: dict[str, str] = {
    "/finance": "/finance-agent",
    "/sygnif": "/overview",
    "/deduce": "/scan",
}


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
# Pure-pandas indicator helpers
# ---------------------------------------------------------------------------
def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _aroon(high: pd.Series, low: pd.Series, period: int = 14) -> tuple[pd.Series, pd.Series]:
    aroonu = high.rolling(period + 1).apply(lambda x: x.argmax(), raw=True) / period * 100
    aroond = low.rolling(period + 1).apply(lambda x: x.argmin(), raw=True) / period * 100
    return aroonu, aroond


def _stochrsi(close: pd.Series, period: int = 14) -> pd.Series:
    rsi = _rsi(close, period)
    rsi_min = rsi.rolling(period).min()
    rsi_max = rsi.rolling(period).max()
    rng = rsi_max - rsi_min
    return ((rsi - rsi_min) / rng.replace(0, np.nan) * 100).rolling(3).mean()


def _cmf(high: pd.Series, low: pd.Series, close: pd.Series,
         volume: pd.Series, period: int = 20) -> pd.Series:
    rng = high - low
    mfv = ((close - low) - (high - close)) / rng.replace(0, np.nan)
    return (mfv * volume).rolling(period).sum() / volume.rolling(period).sum()


def _willr(high: pd.Series, low: pd.Series, close: pd.Series,
           period: int = 14) -> pd.Series:
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    rng = hh - ll
    return ((hh - close) / rng.replace(0, np.nan)) * -100


def _cci(high: pd.Series, low: pd.Series, close: pd.Series,
         period: int = 20) -> pd.Series:
    tp = (high + low + close) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - sma) / (0.015 * mad.replace(0, np.nan))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series,
         period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _rsi14_last_on_resampled(df: pd.DataFrame, rule: str) -> float | None:
    """RSI(14) on resampled OHLCV (Bybit rows with millisecond `ts`)."""
    if df.empty or len(df) < 20 or "ts" not in df.columns:
        return None
    try:
        work = pd.DataFrame({
            "open": pd.to_numeric(df["open"], errors="coerce"),
            "high": pd.to_numeric(df["high"], errors="coerce"),
            "low": pd.to_numeric(df["low"], errors="coerce"),
            "close": pd.to_numeric(df["close"], errors="coerce"),
            "volume": pd.to_numeric(df["volume"], errors="coerce"),
        })
        work.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
        work = work.sort_index()
        agg = work.resample(rule).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna(subset=["close"])
        if len(agg) < 15:
            return None
        rsi_s = _rsi(agg["close"], 14)
        v = rsi_s.iloc[-1]
        return float(v) if pd.notna(v) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# TA: Calculate indicators from OHLCV DataFrame
# ---------------------------------------------------------------------------
def calc_indicators(df: pd.DataFrame, btc_df: pd.DataFrame | None = None) -> dict:
    """Calculate technical indicators matching SygnifStrategy. Returns dict."""
    if len(df) < 50:
        return {}
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # EMAs (strategy set)
    ema9 = close.ewm(span=9).mean()
    ema12 = close.ewm(span=12).mean()
    ema21 = close.ewm(span=21).mean()
    ema26 = close.ewm(span=26).mean()
    ema50 = close.ewm(span=50).mean()
    ema120 = close.ewm(span=120).mean()
    ema200 = close.ewm(span=200).mean() if len(df) >= 200 else pd.Series(dtype=float)

    # RSI (strategy uses 3 + 14)
    rsi14 = _rsi(close, 14)
    rsi3 = _rsi(close, 3)

    # Bollinger Bands 20
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20

    # MACD
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9).mean()
    macd_hist = macd_line - macd_signal

    # Volume SMA (strategy uses 25)
    vol_sma25 = volume.rolling(25).mean()

    # --- Strategy indicators ---
    aroonu, aroond = _aroon(high, low, 14)
    stochrsi_k = _stochrsi(close, 14)
    cmf20 = _cmf(high, low, close, volume, 20)
    willr14 = _willr(high, low, close, 14)
    cci20 = _cci(high, low, close, 20)
    roc9 = close.pct_change(9) * 100
    atr14 = _atr(high, low, close, 14)

    # Swing Failure (SF) levels — 48-bar S/R
    sf_resistance = high.shift(1).rolling(48).max()
    sf_support = low.shift(1).rolling(48).min()
    sf_resistance_stable = sf_resistance == sf_resistance.shift(1)
    sf_support_stable = sf_support == sf_support.shift(1)
    sf_volatility = ((close - ema120).abs() / ema120)

    last = close.iloc[-1]
    prev = close.iloc[-2]

    result = {
        "price": last,
        "prev_close": prev,
        "change_pct": (last - prev) / prev * 100 if prev else 0,
        # EMAs
        "ema9": ema9.iloc[-1],
        "ema12": ema12.iloc[-1],
        "ema21": ema21.iloc[-1],
        "ema26": ema26.iloc[-1],
        "ema50": ema50.iloc[-1],
        "ema120": ema120.iloc[-1],
        "ema200": ema200.iloc[-1] if len(ema200) > 0 else None,
        # RSI
        "rsi": rsi14.iloc[-1],
        "rsi3": rsi3.iloc[-1],
        # Bollinger
        "bb_upper": bb_upper.iloc[-1],
        "bb_lower": bb_lower.iloc[-1],
        "bb_mid": sma20.iloc[-1],
        # MACD
        "macd": macd_line.iloc[-1],
        "macd_signal": macd_signal.iloc[-1],
        "macd_hist": macd_hist.iloc[-1],
        # Volume
        "volume": volume.iloc[-1],
        "vol_avg": vol_sma25.iloc[-1],
        "vol_ratio": volume.iloc[-1] / vol_sma25.iloc[-1] if vol_sma25.iloc[-1] > 0 else 1.0,
        # Strategy indicators
        "aroonu": aroonu.iloc[-1],
        "aroond": aroond.iloc[-1],
        "stochrsi_k": stochrsi_k.iloc[-1],
        "cmf": cmf20.iloc[-1],
        "willr": willr14.iloc[-1],
        "cci": cci20.iloc[-1],
        "roc9": roc9.iloc[-1],
        "atr": atr14.iloc[-1],
        "atr_pct": (atr14.iloc[-1] / last * 100) if last > 0 else 0,
        # Swing failure
        "sf_support": sf_support.iloc[-1],
        "sf_resistance": sf_resistance.iloc[-1],
        "sf_support_stable": bool(sf_support_stable.iloc[-1]),
        "sf_resistance_stable": bool(sf_resistance_stable.iloc[-1]),
        "sf_volatility": sf_volatility.iloc[-1],
        "sf_long": bool(
            low.iloc[-1] <= sf_support.iloc[-1]
            and close.iloc[-1] > sf_support.iloc[-1]
            and sf_support_stable.iloc[-1]
            and sf_volatility.iloc[-1] > 0.03
        ),
        "sf_short": bool(
            high.iloc[-1] >= sf_resistance.iloc[-1]
            and close.iloc[-1] < sf_resistance.iloc[-1]
            and sf_resistance_stable.iloc[-1]
            and sf_volatility.iloc[-1] > 0.03
        ),
        # Legacy keys
        "support": sf_support.iloc[-1],
        "resistance": sf_resistance.iloc[-1],
        "high_24": df.tail(48)["high"].max(),
        "low_24": df.tail(48)["low"].min(),
    }

    # EMA crossover state (9 vs 26 — matches strategy scoring)
    result["ema_bull"] = result["ema9"] > result["ema26"]
    prev_ema9 = ema9.iloc[-2] if len(ema9) >= 2 else result["ema9"]
    prev_ema26 = ema26.iloc[-2] if len(ema26) >= 2 else result["ema26"]
    result["ema_cross"] = result["ema_bull"] and prev_ema9 <= prev_ema26

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

    # Multi-TF + BTC overlays (parity with SygnifStrategy informative merge)
    result["rsi_14_1h"] = float(rsi14.iloc[-1])
    r4 = _rsi14_last_on_resampled(df, "4h")
    result["rsi_14_4h"] = r4 if r4 is not None else None

    if btc_df is not None and not btc_df.empty and len(btc_df) >= 15:
        bclose = pd.to_numeric(btc_df["close"], errors="coerce")
        br = _rsi(bclose, 14)
        bx = br.iloc[-1]
        result["btc_rsi_14_1h"] = float(bx) if pd.notna(bx) else None
    else:
        result["btc_rsi_14_1h"] = None

    return result


# ---------------------------------------------------------------------------
# Strategy TA score — sygnif_ta_score.single source
# ---------------------------------------------------------------------------
def calc_ta_score(ind: dict) -> dict:
    """Compute strategy TA score (0-100) from indicator dict (spec-tagged)."""
    return ta_score_from_indicator_dict(ind)


# ---------------------------------------------------------------------------
# Signal detection — mirrors SygnifStrategy entry/exit conditions
# ---------------------------------------------------------------------------
def detect_signals(ind: dict, ticker: str = "") -> dict:
    """Detect active strategy entry/exit signals from indicators.
    Returns {"entries": [...], "exits": [...], "leverage": float, "atr_pct": float}."""
    if not ind:
        return {
            "entries": [],
            "exits": [],
            "leverage": LEVERAGE_DEFAULT,
            "atr_pct": 0,
            "ta_score": 50,
            "ta_components": {},
            "ta_spec_version": TA_SPEC_VERSION,
            "ta_spec_fingerprint": ta_spec_fingerprint(),
        }

    ta = calc_ta_score(ind)
    score = ta["score"]
    entries = []
    exits = []

    # --- Leverage tier ---
    atr_pct = ind.get("atr_pct", 0)
    if ticker.upper() in MAJOR_PAIRS:
        lev = LEVERAGE_MAJORS
    else:
        lev = LEVERAGE_DEFAULT
    if atr_pct > 3.0:
        lev = min(lev, 2.0)
    elif atr_pct > 2.0:
        lev = min(lev, 3.0)

    vol_ratio = ind.get("vol_ratio", 1.0)

    # --- Entry signals ---
    if score >= 65 and vol_ratio > 1.2:
        entries.append("strong_ta_long")
    if score <= 25:
        entries.append("strong_ta_short")
    if 40 <= score <= 70 and not any("strong" in e for e in entries):
        entries.append("ambiguous_long")
    if 30 <= score <= 60 and not any("strong" in e for e in entries):
        entries.append("ambiguous_short")
    if ind.get("sf_long"):
        entries.append("sf_long")
    if ind.get("sf_short"):
        entries.append("sf_short")

    # --- Exit signals ---
    willr = ind.get("willr", -50)
    if not np.isnan(willr):
        if willr > -5:
            exits.append("willr_overbought")
        if willr < -95:
            exits.append("willr_oversold")

    return {
        "entries": entries,
        "exits": exits,
        "leverage": lev,
        "atr_pct": atr_pct,
        "ta_score": score,
        "ta_components": ta["components"],
        "ta_spec_version": ta["spec_version"],
        "ta_spec_fingerprint": ta["spec_fingerprint"],
    }


def _format_score_label(score: int) -> str:
    if score >= 65:
        return "Bullish"
    elif score <= 35:
        return "Bearish"
    elif score >= 55:
        return "Lean Bullish"
    elif score <= 45:
        return "Lean Bearish"
    return "Neutral"


# ---------------------------------------------------------------------------
# Claude Haiku — AI analysis
# ---------------------------------------------------------------------------
def claude_analyze(prompt: str, max_tokens: int = 1500) -> str:
    """Call Claude Haiku for analysis. Returns empty string if disabled or on failure (no Telegram markup)."""
    if not ANTHROPIC_KEY:
        return ""
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
        return ""
    except Exception as e:
        logger.error(f"Claude error: {e}")
        return ""


# ---------------------------------------------------------------------------
# Pair filtering helper
# ---------------------------------------------------------------------------
_STABLECOIN_EXCLUDE = {"USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD", "USDP", "USDS", "USDE"}


def _filter_pairs(tickers: list[dict], min_turnover: float = 1_000_000) -> list[dict]:
    """Filter USDT pairs, exclude stablecoins and leveraged tokens."""
    pairs = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = sym.replace("USDT", "")
        if base in _STABLECOIN_EXCLUDE or any(x in base for x in ("2L", "3L", "5L", "2S", "3S", "5S")):
            continue
        try:
            price = float(t.get("lastPrice", 0))
            change = float(t.get("price24hPcnt", 0)) * 100
            turnover = float(t.get("turnover24h", 0))
        except (ValueError, TypeError):
            continue
        if turnover < min_turnover:
            continue
        pairs.append({"sym": base, "price": price, "change": change, "vol": turnover})
    return pairs


def _fmt_price(price: float) -> str:
    if price >= 100:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:.2f}"
    else:
        return f"${price:.5f}"


def _fdn_daily_close_and_dod(rows: list | None) -> tuple[float | None, float | None, str | None]:
    """From index-prices / crypto-prices JSON (newest bar first): close, prior-day % change, date."""
    if not rows or not isinstance(rows, list):
        return None, None, None
    cur = rows[0]
    if not isinstance(cur, dict):
        return None, None, None
    close = cur.get("close")
    d = cur.get("date")
    if close is None:
        return None, None, str(d) if d else None
    c0 = float(close)
    pct = None
    if len(rows) > 1 and isinstance(rows[1], dict):
        prev = rows[1].get("close")
        if prev is not None:
            p = float(prev)
            if p != 0:
                pct = (c0 - p) / p * 100.0
    return c0, pct, str(d) if d else None


def fdn_tradfi_snapshot() -> str:
    """TradFi + FDN crypto via Standard-tier daily endpoints only (index-prices, crypto-prices)."""
    if not _FINANCIALDATA_API_KEY:
        return ""
    key = _FINANCIALDATA_API_KEY
    base = _FINANCIALDATA_API_BASE
    lines: list[str] = []

    index_ids = [
        ("^GSPC", "S&P 500"),
        ("^DJI", "Dow"),
        ("DX-Y.NYB", "USD Index"),
    ]
    idx_ok = False
    for ident, label in index_ids:
        try:
            r = requests.get(
                f"{base}/index-prices",
                params={"identifier": ident, "offset": 0, "key": key},
                timeout=15,
            )
            if not r.ok:
                logger.warning("financialdata index-prices %s http=%s", ident, r.status_code)
                continue
            data = r.json()
            if not isinstance(data, list) or not data:
                continue
            close, dod, bar_date = _fdn_daily_close_and_dod(data)
            if close is None:
                continue
            if not idx_ok:
                lines.append("*FDN indexes (daily close):*")
                idx_ok = True
            ch = f" ({dod:+.2f}% vs prior day)" if dod is not None else ""
            lines.append(f"  `{ident}` {label}: `{close:,.2f}` · {bar_date}{ch}")
        except Exception as e:
            logger.warning("financialdata index-prices %s err=%s", ident, e)

    crypto_ids = ["BTCUSD", "ETHUSD"]
    cry_ok = False
    for ident in crypto_ids:
        try:
            r = requests.get(
                f"{base}/crypto-prices",
                params={"identifier": ident, "offset": 0, "key": key},
                timeout=15,
            )
            if not r.ok:
                logger.warning("financialdata crypto-prices %s http=%s", ident, r.status_code)
                continue
            data = r.json()
            if not isinstance(data, list) or not data:
                continue
            close, dod, bar_date = _fdn_daily_close_and_dod(data)
            if close is None:
                continue
            if not cry_ok:
                lines.append("*FDN crypto USD (daily close):*")
                cry_ok = True
            ch = f" ({dod:+.2f}% vs prior day)" if dod is not None else ""
            lines.append(f"  `{ident}` `{close:,.2f}` · {bar_date}{ch}")
        except Exception as e:
            logger.warning("financialdata crypto-prices %s err=%s", ident, e)

    if not lines:
        return ""
    lines.append(
        f"_{datetime.now(timezone.utc).strftime('%H:%M UTC')} · financialdata.net (daily / Standard)_"
    )
    return "\n".join(lines)


def cmd_tradfi() -> str:
    """Explicit FinancialData.net macro snapshot (Standard daily prices only)."""
    s = fdn_tradfi_snapshot()
    if s:
        return s
    if not _FINANCIALDATA_API_KEY:
        return (
            "*FinancialData.net*\n"
            "Set `FINANCIALDATA_API_KEY` in the bot environment. "
            "Docs: https://financialdata.net/documentation"
        )
    return (
        "*FinancialData.net*\n"
        "Key is set but no daily price rows returned (check API status or symbol availability)."
    )


# ---------------------------------------------------------------------------
# Command: /tendency — Market tendency (bull/bear)
# ---------------------------------------------------------------------------
def cmd_tendency() -> str:
    """Market tendency: TA scan + Claude AI insight."""
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch data."

    # BTC + ETH + top 3 alts by volume
    core_syms = ["BTCUSDT", "ETHUSDT"]
    pairs = _filter_pairs(tickers, min_turnover=5_000_000)
    top_alts = [p for p in sorted(pairs, key=lambda x: x["vol"], reverse=True)
                if p["sym"] not in ("BTC", "ETH")][:3]
    scan_syms = core_syms + [f"{p['sym']}USDT" for p in top_alts]

    bull_count = 0
    bear_count = 0
    total = 0
    lines = ["*Market Tendency*\n"]
    coin_data = []  # collect for Claude prompt
    score_rows = []

    btc_ref_df = bybit_kline("BTCUSDT", "60", 200)
    btc_ref = btc_ref_df if not btc_ref_df.empty else None

    for sym in scan_syms:
        df = bybit_kline(sym, interval="60", limit=200)
        if df.empty:
            continue
        ind = calc_indicators(df, btc_df=btc_ref)
        if not ind:
            continue
        sig = detect_signals(ind, sym.replace("USDT", ""))
        score = sig["ta_score"]
        total += 1

        name = sym.replace("USDT", "")
        trend = ind.get("trend", "?")
        rsi = ind.get("rsi", 50)
        willr = ind.get("willr", -50)
        macd = ind.get("macd_signal_text", "?")
        pf = _fmt_price(ind["price"])
        entry = sig["entries"][0] if sig["entries"] else "none"

        if score >= 55:
            bull_count += 1
            icon = "\U0001f7e2"
        elif score <= 45:
            bear_count += 1
            icon = "\U0001f534"
        else:
            icon = "\u26aa"

        lines.append(f"{icon} `{name:>5}` {pf} TA:`{score}` {trend} RSI:`{rsi:.0f}`")
        score_rows.append(
            {
                "name": name,
                "score": score,
                "trend": trend,
                "rsi": rsi,
                "entry": entry,
                "macd": macd,
                "willr": willr,
            }
        )
        coin_data.append(
            f"{name}: ${ind['price']:.4g} {trend} TA:{score} RSI:{rsi:.0f} "
            f"WR:{willr:.0f} MACD:{macd} signal:{entry}"
        )

    lines.append("")

    # Overall verdict
    if total == 0:
        verdict = "\u2753 No data"
    elif bull_count > bear_count and bull_count >= total * 0.6:
        verdict = "\U0001f7e2 *BULLISH* — majority leaning up"
    elif bear_count > bull_count and bear_count >= total * 0.6:
        verdict = "\U0001f534 *BEARISH* — majority leaning down"
    elif bull_count > bear_count:
        verdict = "\U0001f7e1 *LEAN BULLISH* — mixed, tilting up"
    elif bear_count > bull_count:
        verdict = "\U0001f7e1 *LEAN BEARISH* — mixed, tilting down"
    else:
        verdict = "\u26aa *NEUTRAL* — no clear direction"
    lines.append(verdict)

    if score_rows:
        avg_score = sum(r["score"] for r in score_rows) / len(score_rows)
        strong_bull = [r for r in score_rows if r["score"] >= 65]
        strong_bear = [r for r in score_rows if r["score"] <= 35]
        leader = max(score_rows, key=lambda r: r["score"])
        laggard = min(score_rows, key=lambda r: r["score"])
        lines.append(
            f"\n*Tendency Detail:* avg TA `{avg_score:.1f}` | strong bull `{len(strong_bull)}` | strong bear `{len(strong_bear)}`"
        )
        lines.append(
            f"*Leader/Laggard:* `{leader['name']} ({leader['score']})` / `{laggard['name']} ({laggard['score']})`"
        )

        if avg_score >= 60 and bull_count >= max(1, bear_count + 1):
            bias = "Risk-on tilt"
            plan = "Prefer long setups with TA>=65; use pullbacks and keep risk tight."
        elif avg_score <= 40 and bear_count >= max(1, bull_count + 1):
            bias = "Risk-off tilt"
            plan = "Stay defensive; favor short breakdowns and avoid weak long attempts."
        else:
            bias = "Mixed regime"
            plan = "Trade selectively around strongest names and reduce size on ambiguous signals."
        lines.append(f"\n*Finance-Agent Opinion:* {bias}. {plan}")

    # --- Claude AI insight ---
    headlines = fetch_news("", max_items=5)
    news_text = "\n".join(f"- {h}" for h in headlines) if headlines else "No recent news."
    data_block = "\n".join(coin_data)

    prompt = f"""You are Sygnif's market analyst. Give a 3-4 sentence market tendency reading.

MARKET DATA:
{data_block}

Bull/Bear count: {bull_count} bullish, {bear_count} bearish, {total - bull_count - bear_count} neutral

RECENT NEWS:
{news_text}

Rules:
- State the overall tendency clearly (bullish/bearish/neutral)
- Mention the key driver (BTC leading? alts diverging? news catalyst?)
- Flag any risks or watch-outs (overbought RSI, divergence, etc.)
- 3-4 sentences max, no disclaimers, be direct"""

    insight = claude_analyze(prompt, max_tokens=200)
    if (insight or "").strip():
        lines.append(f"\n\U0001f9e0 *Agent Insight:*\n{insight}")

    lines.append(f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /market — Top crypto overview
# ---------------------------------------------------------------------------
def cmd_market() -> str:
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch market data."

    pairs = _filter_pairs(tickers)
    top = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:15]

    lines = ["*Crypto Market Overview*\n"]
    for p in top:
        arrow = "+" if p["change"] >= 0 else ""
        vol_m = p["vol"] / 1e6
        pf = _fmt_price(p["price"])
        lines.append(f"`{p['sym']:>6}` {pf:>12} `{arrow}{p['change']:.1f}%` Vol `${vol_m:.0f}M`")

    lines.append(f"\n_{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /movers — Top gainers & losers
# ---------------------------------------------------------------------------
def cmd_movers() -> str:
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch data."

    pairs = _filter_pairs(tickers, min_turnover=500_000)
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
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /ta <TICKER> — Technical analysis + strategy signals
# ---------------------------------------------------------------------------
def cmd_ta(ticker: str) -> str:
    ticker = ticker.upper().strip() or "BTC"
    symbol = f"{ticker}USDT"

    df = bybit_kline(symbol, interval="60", limit=200)
    if df.empty:
        return f"No data for `{ticker}`. Check ticker symbol."

    btc_df = bybit_kline("BTCUSDT", "60", 200)
    ind = calc_indicators(df, btc_df=btc_df if not btc_df.empty else None)
    if not ind:
        return f"Not enough data for `{ticker}`."

    sig = detect_signals(ind, ticker)
    p = ind["price"]
    pf = f"${p:,.2f}" if p >= 1 else f"${p:.6f}"

    ema200_line = ""
    if ind["ema200"] is not None:
        e200 = ind["ema200"]
        ema200_line = f"  EMA 200: `{e200:.4g}` {'(above)' if p > e200 else '(below)'}\n"

    # Strategy signals section
    score = sig["ta_score"]
    label = _format_score_label(score)
    entry_str = ", ".join(sig["entries"]) if sig["entries"] else "None"
    exit_str = ", ".join(sig["exits"]) if sig["exits"] else "None"

    # Score breakdown
    comps = sig["ta_components"]
    comp_parts = [f"{k}({v:+d})" for k, v in comps.items() if v != 0]
    comp_str = " ".join(comp_parts) if comp_parts else "all neutral"

    sf_status = ""
    if ind.get("sf_long"):
        sf_status = "SF Long active"
    elif ind.get("sf_short"):
        sf_status = "SF Short active"
    else:
        sf_status = "No active pattern"

    msg = (
        f"*Technical Analysis: {ticker}*\n"
        f"*Price:* `{pf}`\n\n"
        f"*Strategy Signals:*\n"
        f"  TA Score: `{score}/100` ({label})\n"
        f"  Entry: `{entry_str}`\n"
        f"  Exit: `{exit_str}`\n"
        f"  Leverage: `{sig['leverage']:.0f}x` (ATR {sig['atr_pct']:.1f}%)\n"
        f"  Swing Failure: {sf_status}\n"
        f"  Score: `{comp_str}`\n\n"
        f"*Trend:* `{ind['trend']}`\n"
        f"*EMAs:*\n"
        f"  EMA 9: `{ind['ema9']:.4g}`\n"
        f"  EMA 21: `{ind['ema21']:.4g}`\n"
        f"  EMA 50: `{ind['ema50']:.4g}`\n"
        f"{ema200_line}\n"
        f"*RSI:* `{ind['rsi']:.1f}` — {ind['rsi_signal']} | RSI3: `{ind['rsi3']:.0f}`\n"
        f"*MACD:* `{ind['macd']:.4g}` — {ind['macd_signal_text']}\n\n"
        f"*Oscillators:*\n"
        f"  Williams %R: `{ind['willr']:.0f}`\n"
        f"  StochRSI: `{ind['stochrsi_k']:.0f}`\n"
        f"  CCI: `{ind['cci']:.0f}` | CMF: `{ind['cmf']:.3f}`\n"
        f"  Aroon U/D: `{ind['aroonu']:.0f}/{ind['aroond']:.0f}`\n\n"
        f"*Bollinger:* `{ind['bb_position']}` "
        f"(`{ind['bb_lower']:.4g}` — `{ind['bb_upper']:.4g}`)\n\n"
        f"*Levels:*\n"
        f"  Support: `{ind['support']:.4g}` | Resistance: `{ind['resistance']:.4g}`\n\n"
        f"*Volume:* `{ind['volume']:,.0f}` ({ind['vol_ratio']:.1f}x avg)\n"
        f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')} · 1h candles_"
    )
    return msg


# ---------------------------------------------------------------------------
# Command: /research <TICKER> — Full AI research
# ---------------------------------------------------------------------------
def cmd_research(ticker: str) -> str:
    ticker = ticker.upper().strip() or "BTC"
    symbol = f"{ticker}USDT"

    # 1. Fetch market data
    df = bybit_kline(symbol, interval="60", limit=200)
    btc_df = bybit_kline("BTCUSDT", "60", 200)
    ind = (
        calc_indicators(df, btc_df=btc_df if not btc_df.empty else None)
        if not df.empty
        else {}
    )
    sig = detect_signals(ind, ticker)

    # 2. Fetch news
    headlines = fetch_news(ticker)
    news_text = "\n".join(f"- {h}" for h in headlines[:5]) if headlines else "No recent news."

    # 3. Price from tickers
    tickers = bybit_tickers()
    pair_data = next((t for t in tickers if t.get("symbol") == symbol), {})
    price = float(pair_data.get("lastPrice", 0))
    change_24h = float(pair_data.get("price24hPcnt", 0)) * 100
    vol_24h = float(pair_data.get("turnover24h", 0))

    # 4. Build prompt for Claude with strategy context
    ta_summary = "No TA data available."
    strat_summary = ""
    if ind:
        ta_summary = (
            f"Price: ${ind['price']:.4g}, Trend: {ind['trend']}, "
            f"RSI14: {ind['rsi']:.1f} ({ind['rsi_signal']}), RSI3: {ind['rsi3']:.0f}, "
            f"MACD: {ind['macd_signal_text']} (hist: {ind['macd_hist']:.4g}), "
            f"BB position: {ind['bb_position']}, "
            f"Williams%%R: {ind['willr']:.0f}, StochRSI: {ind['stochrsi_k']:.0f}, "
            f"Aroon U/D: {ind['aroonu']:.0f}/{ind['aroond']:.0f}, CMF: {ind['cmf']:.3f}, "
            f"Support: {ind['support']:.4g}, Resistance: {ind['resistance']:.4g}, "
            f"Volume: {ind['vol_ratio']:.1f}x average"
        )
        entry_str = ", ".join(sig["entries"]) if sig["entries"] else "None"
        strat_summary = (
            f"\nSTRATEGY CONTEXT:\n"
            f"- TA Score: {sig['ta_score']}/100 ({_format_score_label(sig['ta_score'])})\n"
            f"- Active Signals: {entry_str}\n"
            f"- Leverage Tier: {sig['leverage']:.0f}x (ATR {sig['atr_pct']:.1f}%)\n"
            f"- Swing Failure: {'SF Long' if ind.get('sf_long') else 'SF Short' if ind.get('sf_short') else 'None'}"
        )

    prompt = f"""You are a crypto research analyst for the Sygnif trading bot. Provide a concise research report for {ticker}.

CURRENT DATA:
- Price: ${price:.4g} (24h: {change_24h:+.1f}%)
- 24h Volume: ${vol_24h/1e6:.1f}M
- Technical Analysis: {ta_summary}
{strat_summary}

RECENT NEWS:
{news_text}

Write a concise research report in Markdown with these sections:
1. **Market Status** (2 sentences: price action + trend)
2. **Technical Outlook** (3-4 bullet points: key indicator signals including strategy TA score)
3. **Strategy View** (2 bullet points: what signals are active, would the bot enter/exit?)
4. **News & Sentiment** (2-3 bullet points: what headlines suggest)
5. **Verdict** (1 paragraph: bullish/bearish/neutral with reasoning and key levels)

Keep it under 350 words. Be specific with numbers. No disclaimers."""

    analysis = claude_analyze(prompt)

    msg = (
        f"*Research Report: {ticker}*\n"
        f"*Price:* `${price:.4g}` (`{change_24h:+.1f}%` 24h)\n"
        f"*TA Score:* `{sig['ta_score']}/100` | Signals: `{', '.join(sig['entries']) or 'None'}`\n\n"
        f"{analysis}\n\n"
        f"_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
    )
    return msg


# ---------------------------------------------------------------------------
# Command: /plays — AI investment opportunities (strategy-aware)
# ---------------------------------------------------------------------------
def cmd_plays() -> str:

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

    # Enrich top pairs with TA scores
    btc_df = bybit_kline("BTCUSDT", "60", 200)
    btc_ref = btc_df if not btc_df.empty else None
    market_ctx = "Top by volume (with strategy TA score):\n"
    for p in top_by_vol:
        df = bybit_kline(f"{p['sym']}USDT", "60", 200)
        ind = calc_indicators(df, btc_df=btc_ref) if not df.empty else {}
        sig = detect_signals(ind, p["sym"])
        signal_str = sig["entries"][0] if sig["entries"] else "no_signal"
        market_ctx += (
            f"  {p['sym']}: ${p['price']:.4g} ({p['change']:+.1f}%) "
            f"vol ${p['vol']/1e6:.0f}M | TA:{sig['ta_score']} {signal_str} "
            f"| Lev:{sig['leverage']:.0f}x\n"
        )
    market_ctx += "\nTop gainers:\n"
    for p in top_gainers:
        market_ctx += f"  {p['sym']}: +{p['change']:.1f}%\n"
    market_ctx += "\nTop losers:\n"
    for p in top_losers:
        market_ctx += f"  {p['sym']}: {p['change']:.1f}%\n"

    # Fetch BTC TA for macro context (reuse klines fetched above when available)
    btc_ind = calc_indicators(btc_df, btc_df=btc_df) if not btc_df.empty else {}
    btc_sig = detect_signals(btc_ind, "BTC")
    btc_ctx = ""
    if btc_ind:
        btc_ctx = (
            f"\nBTC Context: ${btc_ind['price']:,.0f}, {btc_ind['trend']}, "
            f"RSI {btc_ind['rsi']:.0f}, MACD {btc_ind['macd_signal_text']}, "
            f"TA Score: {btc_sig['ta_score']}/100"
        )

    prompt = f"""You are a crypto strategist for the Sygnif trading bot. Based on market data AND strategy signals, provide exactly 3 actionable plays.

IMPORTANT: The bot uses these entry types:
- strong_ta (long): TA score >= 65 + volume > 1.2x avg
- strong_ta_short: TA score <= 25
- claude_sentiment (long): TA 40-70, news sentiment pushes combined >= 55
- claude_sentiment_short: TA 30-60, news sentiment pushes combined <= 40
- swing_failure: price wicks past 48-bar S/R then closes back

Plays should align with what the bot would actually trade. Prioritize coins with active signals.

MARKET DATA:
{market_ctx}
{btc_ctx}

For each play, use this format:
**Play #N: TICKER — [Name]**
Type: strong_ta | claude_sentiment | swing_failure | mean_reversion
Side: Long | Short
Risk: Low/Medium/High
- *Thesis:* Why this opportunity exists (1-2 sentences)
- *Entry:* Specific price or condition
- *TP:* Target price and expected %
- *SL:* Stop loss price and %
- *Timeframe:* Hours/Days

Keep each play to 4-5 lines. Be specific with prices. No disclaimers. Total under 400 words."""

    analysis = claude_analyze(prompt, max_tokens=2000)

    # Save plays for trade overseer
    try:
        requests.post(
            f"{_SYGNIF_OVERSEER_HTTP}/plays",
            json={"raw_text": analysis, "market_context": market_ctx},
            timeout=3,
        )
    except Exception:
        pass  # Overseer may not be running

    return f"*Investment Plays*\n\n{analysis}\n\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"


# ---------------------------------------------------------------------------
# Command: /signals — Quick scan: active entry signals across top pairs
# ---------------------------------------------------------------------------
def cmd_signals() -> str:
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch data."

    pairs = _filter_pairs(tickers, min_turnover=2_000_000)

    top = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:12]

    longs = []
    shorts = []
    ambiguous = []

    btc_ref_df = bybit_kline("BTCUSDT", "60", 200)
    btc_ref = btc_ref_df if not btc_ref_df.empty else None

    for p in top:
        df = bybit_kline(f"{p['sym']}USDT", "60", 200)
        ind = calc_indicators(df, btc_df=btc_ref) if not df.empty else {}
        if not ind:
            continue
        sig = detect_signals(ind, p["sym"])
        score = sig["ta_score"]
        entries = sig["entries"]

        row = f"  `{p['sym']:>5}` TA:`{score}` "

        if "strong_ta_long" in entries or "sf_long" in entries:
            detail = f"RSI:{ind['rsi']:.0f} vol:{ind['vol_ratio']:.1f}x lev:{sig['leverage']:.0f}x"
            sig_name = "strong_ta" if "strong_ta_long" in entries else "sf_long"
            longs.append(row + f"`{sig_name}` ({detail})")
        elif "strong_ta_short" in entries or "sf_short" in entries:
            detail = f"RSI:{ind['rsi']:.0f} lev:{sig['leverage']:.0f}x"
            sig_name = "strong_ta_short" if "strong_ta_short" in entries else "sf_short"
            shorts.append(row + f"`{sig_name}` ({detail})")
        elif "ambiguous_long" in entries or "ambiguous_short" in entries:
            zone = "40-70" if "ambiguous_long" in entries else "30-60"
            ambiguous.append(row + f"claude zone ({zone})")

    lines = ["*Active Strategy Signals*\n"]
    if longs:
        lines.append("LONG:")
        lines.extend(longs)
    if shorts:
        lines.append("\nSHORT:")
        lines.extend(shorts)
    if ambiguous:
        lines.append("\nAMBIGUOUS (Claude sentiment zone):")
        lines.extend(ambiguous)
    if not longs and not shorts and not ambiguous:
        lines.append("_No active signals across top pairs._")

    lines.append(f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')} · 1h candles_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /scan — Deep opportunity scanner (TA + news + Claude ranking)
# ---------------------------------------------------------------------------
def cmd_scan() -> str:
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch data."

    pairs = _filter_pairs(tickers, min_turnover=2_000_000)
    top = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:15]

    btc_ref_df = bybit_kline("BTCUSDT", "60", 200)
    btc_ref = btc_ref_df if not btc_ref_df.empty else None

    # 1. Compute TA + signals for all pairs
    signal_pairs = []
    for p in top:
        df = bybit_kline(f"{p['sym']}USDT", "60", 200)
        if df.empty:
            continue
        ind = calc_indicators(df, btc_df=btc_ref)
        if not ind:
            continue
        sig = detect_signals(ind, p["sym"])
        entries = sig["entries"]
        # Skip pairs with no actionable signal
        if not entries or entries == ["ambiguous_short"]:
            continue
        signal_pairs.append({
            "sym": p["sym"], "ind": ind, "sig": sig,
            "price": ind["price"], "vol": p["vol"],
        })

    if not signal_pairs:
        return "*Scan* | No active signals across top 15 pairs."

    # 2. Fetch news for top signal pairs (max 6)
    scan_pairs = signal_pairs[:6]
    data_lines = []
    for sp in scan_pairs:
        sym = sp["sym"]
        ind = sp["ind"]
        sig = sp["sig"]
        entry = sig["entries"][0]
        side = "Short" if "short" in entry else "Long"

        headlines = fetch_news(sym, max_items=2)
        news_str = headlines[0].split(" — ")[0] if headlines else "No recent news"

        data_lines.append(
            f"{sym}: ${ind['price']:.4g} {ind['trend']} "
            f"TA:{sig['ta_score']} {entry} RSI:{ind['rsi']:.0f} "
            f"WR:{ind['willr']:.0f} Lev:{sig['leverage']:.0f}x "
            f"| News: \"{news_str}\""
        )

    data_block = "\n".join(data_lines)

    # 3. Claude ranking
    prompt = f"""Rank these crypto opportunities by conviction (best first).
Each has strategy TA signals + recent news.

{data_block}

For each, output one line:
#N PAIR Side — key reason (max 10 words)

Side is Long or Short. Skip weak opportunities. Max 6 lines. Be specific with numbers."""

    ranking = claude_analyze(prompt, max_tokens=400)

    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = [f"*Scan* | {now_str}\n"]
    lines.append(ranking)
    lines.append(f"\n_Scanned {len(top)} pairs, {len(signal_pairs)} with signals_")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /overview — Full trade + market overview (consults overseer)
# ---------------------------------------------------------------------------
OVERSEER_TRADES = f"{_SYGNIF_OVERSEER_HTTP}/trades"


def _duration_str(seconds: float) -> str:
    if not seconds or seconds < 0:
        return "--"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


def cmd_overview() -> str:
    # 1. Fetch trades + profits from overseer
    try:
        resp = requests.get(OVERSEER_TRADES, timeout=10)
        data = resp.json()
        trades = data.get("trades", [])
        profits = data.get("profits", [])
    except Exception as e:
        return f"Overseer unavailable: {e}"

    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = [f"*SYGNIF OVERVIEW*", f"_{now_str}_\n"]

    # 2. Profit summary per instance
    for p in profits:
        inst = p.get("instance", "?")
        total = p.get("profit_all", 0)
        wins = p.get("winning_trades", 0)
        losses = p.get("losing_trades", 0)
        total_closed = wins + losses
        wr = f"{wins / total_closed * 100:.0f}%" if total_closed else "--"
        lines.append(f"*{inst.upper()}:* P/L `{total:+.4f}` | W/L {wins}/{losses} ({wr})")

    # 3. Open trades with TA context
    if trades:
        # Get TA for traded symbols
        trade_syms = list({
            t["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
            for t in trades
        })
        btc_ref_df = bybit_kline("BTCUSDT", "60", 200)
        btc_ref = btc_ref_df if not btc_ref_df.empty else None
        ta_map = {}
        for sym in trade_syms:
            df = bybit_kline(f"{sym}USDT", "60", 200)
            if not df.empty:
                ind = calc_indicators(df, btc_df=btc_ref)
                if ind:
                    sig = detect_signals(ind, sym)
                    ta_map[sym] = {"ind": ind, "sig": sig}

        # Group by instance
        spot = [t for t in trades if t["instance"] == "spot"]
        futures = [t for t in trades if t["instance"] == "futures"]

        for label, group in [("Spot", spot), ("Futures", futures)]:
            if not group:
                continue
            lines.append(f"\n*{label} ({len(group)}):*")
            for t in sorted(group, key=lambda x: x["profit_pct"], reverse=True):
                pair = t["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
                pct = t["profit_pct"]
                pnl = t["profit_abs"]
                dur = _duration_str(t["trade_duration"])
                tag = (t.get("enter_tag") or "")[:14]
                emoji = "\U0001f7e2" if pct >= 0 else "\U0001f534"

                line = f"{emoji} *{pair}* `{pct:+.1f}%` ({pnl:+.4f}) {dur}"
                if tag:
                    line += f" _{tag}_"
                lines.append(line)

                # TA context
                ctx = ta_map.get(pair)
                if ctx:
                    s = ctx["sig"]
                    i = ctx["ind"]
                    entry = s["entries"][0] if s["entries"] else ""
                    exit_s = s["exits"][0] if s["exits"] else ""
                    parts = [f"TA:{s['ta_score']}"]
                    if entry:
                        parts.append(entry)
                    if exit_s:
                        parts.append(f"EXIT:{exit_s}")
                    parts.append(f"RSI:{i['rsi']:.0f}")
                    parts.append(f"WR:{i['willr']:.0f}")
                    lines.append(f"    `{' '.join(parts)}`")

        total_unreal = sum(t["profit_abs"] for t in trades)
        lines.append(f"\n*Unrealized:* `{total_unreal:+.4f}` USDT")
    else:
        lines.append("\n_No open trades_")

    # 4. Market tendency (BTC + ETH)
    lines.append("\n*Market:*")
    btc_eth_ref = bybit_kline("BTCUSDT", "60", 200)
    btc_eth = btc_eth_ref if not btc_eth_ref.empty else None
    for sym_name in ["BTC", "ETH"]:
        df = bybit_kline(f"{sym_name}USDT", "60", 200)
        if df.empty:
            continue
        ind = calc_indicators(df, btc_df=btc_eth)
        if not ind:
            continue
        sig_m = detect_signals(ind, sym_name)
        ta_sc = sig_m["ta_score"]
        pf = _fmt_price(ind["price"])
        trend = ind["trend"]
        if ta_sc >= 55:
            icon = "\U0001f7e2"
        elif ta_sc <= 45:
            icon = "\U0001f534"
        else:
            icon = "\u26aa"
        lines.append(f"  {icon} {sym_name} {pf} TA:`{ta_sc}` {trend}")

    # 5. Bybit API health — check rate limits
    try:
        resp = requests.get(
            f"{BYBIT}/market/tickers",
            params={"category": "spot", "symbol": "BTCUSDT"},
            timeout=5,
        )
        rl_remaining = resp.headers.get("X-Bapi-Limit-Status", "?")
        rl_limit = resp.headers.get("X-Bapi-Limit", "?")
        rl_reset = resp.headers.get("X-Bapi-Limit-Reset-Timestamp", "")
        status_code = resp.status_code

        if status_code == 200:
            lines.append(f"\n*API:* `{rl_remaining}/{rl_limit}` calls left")
            # Warn if low
            try:
                remaining = int(rl_remaining)
                limit = int(rl_limit)
                if remaining < limit * 0.2:
                    lines.append(f"  \u26a0\ufe0f Rate limit low!")
            except (ValueError, TypeError):
                pass
        else:
            lines.append(f"\n*API:* \u26a0\ufe0f Status {status_code}")
    except Exception:
        lines.append(f"\n*API:* \u26a0\ufe0f Bybit unreachable")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /news — Latest headlines
# ---------------------------------------------------------------------------
def cmd_news(ticker: str = "") -> str:
    headlines = fetch_news(ticker.upper().strip() if ticker else "")
    if not headlines:
        return "Could not fetch news."
    title = f"*Crypto News*" + (f" ({ticker.upper()})" if ticker else "")
    lines = [title, ""]
    for i, h in enumerate(headlines, 1):
        lines.append(f"{i}. {h}")
    lines.append(f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /fa_help
# ---------------------------------------------------------------------------
def cmd_start() -> str:
    return (
        "*Sygnif Finance Agent*\n\n"
        "Use the keyboard below or `/fa_help` for all commands.\n\n"
        "_Shortcuts:_ `/finance` → `/finance-agent`, `/sygnif` → `/overview`, `/deduce` → `/scan`"
    )


def cmd_help() -> str:
    return (
        "*Sygnif Finance Agent*\n\n"
        "`/finance-agent` — Comprehensive research\n"
        "`/finance-agent <cmd>` — Run specific module\n"
        "`/finance-agent <TICKER>` — Research for ticker\n"
        "`/overview` — Trades + TA + market (full dashboard)\n"
        "`/tendency` — Market tendency (bull/bear)\n"
        "`/signals` — Active entry signals (top pairs)\n"
        "`/scan` — Deep scan: signals + news + AI ranking\n"
        "`/market` — Top 15 crypto overview\n"
        "`/movers` — Gainers & losers (24h)\n"
        "`/ta BTC` — TA + strategy signals\n"
        "`/research ETH` — Full AI research report\n"
        "`/plays` — AI investment plays\n"
        "`/news` — Latest crypto headlines\n"
        "`/evaluate` — Force trade evaluation\n"
        "`/finance-agent tradfi` — FDN daily index + BTC/ETH closes (Standard API)\n"
        "`/fa_help` — This message\n\n"
        "*Shortcuts:* `/finance` → `/finance-agent`, `/sygnif` → `/overview`, "
        "`/deduce` → `/scan`"
    )


# ---------------------------------------------------------------------------
# Command: /ask — quick research for a ticker (alias-friendly)
# ---------------------------------------------------------------------------
def cmd_ask(args: str) -> str:
    tick = (args or "").strip()
    if tick:
        return cmd_research(tick)
    return (
        "*Ask*\n"
        "Send `/ask BTC` (or any ticker) for a research snapshot.\n"
        "Full report: `/research TICKER`"
    )


# ---------------------------------------------------------------------------
# Command: /macro — BTC-led macro overlay
# ---------------------------------------------------------------------------
def cmd_macro() -> str:
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch market data."

    btc_df = bybit_kline("BTCUSDT", "240", 200)
    btc_ind = calc_indicators(btc_df) if not btc_df.empty else {}
    btc_sig = detect_signals(btc_ind, "BTC") if btc_ind else {"ta_score": 50}

    pairs = _filter_pairs(tickers, min_turnover=5_000_000)
    top = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:20]
    up = sum(1 for p in top if p["change"] > 0)
    breadth = f"{up}/{len(top)} up" if top else "n/a"

    if not btc_ind:
        return f"*Macro*\nBreadth: `{breadth}`\n_BTC data unavailable_"

    ta = btc_sig.get("ta_score", 50)
    regime = "Mixed / transition"
    if ta >= 65:
        regime = "Risk-on bias"
    elif ta <= 35:
        regime = "Risk-off bias"

    core = (
        "*Macro-Crypto Context*\n"
        f"BTC: `{_fmt_price(btc_ind['price'])}` | `{btc_ind['trend']}` | TA `{ta}`\n"
        f"RSI `{btc_ind['rsi']:.0f}` | WR `{btc_ind['willr']:.0f}` | MACD `{btc_ind['macd_signal_text']}`\n"
        f"Breadth (top vol alts): `{breadth}`\n"
        f"Regime: *{regime}*\n"
        f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
    )
    fdn = fdn_tradfi_snapshot()
    if fdn:
        return core + "\n\n" + fdn
    return core


# ---------------------------------------------------------------------------
# Command: /finance-agent — umbrella router
# ---------------------------------------------------------------------------
def cmd_finance_agent(args: str) -> str:
    raw = (args or "").strip()
    if not raw:
        sections = [
            ("Tendency", cmd_tendency()),
            ("Signals", cmd_signals()),
            ("Macro", cmd_macro()),
            ("Top Plays", cmd_plays()),
        ]
        lines = [f"*Finance Agent Comprehensive* | {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n"]
        for title, content in sections:
            lines.append(f"*{title}*")
            snippet = content.strip()
            if len(snippet) > 1200:
                snippet = snippet[:1200].rstrip() + "\n... (truncated)"
            lines.append(snippet)
            lines.append("")
        lines.append("*Active Ops Modules:*")
        lines.append("- BTC dependency gating for alts")
        lines.append("- Strategy comparison baseline: `claude_s0`")
        lines.append("- Futures shorts module with squeeze-risk filter")
        if _FINANCIALDATA_API_KEY:
            lines.append("- TradFi overlay: FDN daily index/crypto closes (Standard tier, in `/macro`)")
        return "\n".join(lines).strip()

    parts = raw.split(maxsplit=1)
    sub = parts[0].lower().strip()
    tail = parts[1].strip() if len(parts) > 1 else ""
    subcommands = {
        "market": lambda: cmd_market(),
        "movers": lambda: cmd_movers(),
        "signals": lambda: cmd_signals(),
        "scan": lambda: cmd_scan(),
        "plays": lambda: cmd_plays(),
        "tendency": lambda: cmd_tendency(),
        "macro": lambda: cmd_macro(),
        "overview": lambda: cmd_overview(),
        "evaluate": lambda: cmd_evaluate(),
        "help": lambda: cmd_help(),
        "ta": lambda: cmd_ta(tail or "BTC"),
        "research": lambda: cmd_research(tail or "BTC"),
        "tradfi": lambda: cmd_tradfi(),
        "fdn": lambda: cmd_tradfi(),
    }
    if sub in subcommands:
        return subcommands[sub]()
    if sub.isalpha() and 2 <= len(sub) <= 10:
        return cmd_research(sub.upper())
    return "Unknown /finance-agent command. Use `market|movers|ta <TICK>|signals|scan|research <TICK>|plays|tendency|macro|tradfi`"


# ---------------------------------------------------------------------------
# Command: /overseer — Trade overseer overview
# ---------------------------------------------------------------------------
def cmd_overseer() -> str:
    try:
        resp = requests.get(f"{_SYGNIF_OVERSEER_HTTP}/overview", timeout=5)
        data = resp.json()
        commentary = data.get("last_commentary", "")
        if commentary:
            return commentary
        return f"*Overseer* | {data.get('open_trades', 0)} trades tracked, no recent alerts."
    except Exception as e:
        return f"Overseer unavailable: {e}"


# ---------------------------------------------------------------------------
# Command: /evaluate — Force trade evaluation
# ---------------------------------------------------------------------------
def cmd_evaluate() -> str:
    # 1. Fetch trades from overseer
    try:
        resp = requests.get(OVERSEER_TRADES, timeout=10)
        data = resp.json()
        trades = data.get("trades", [])
        profits = data.get("profits", [])
    except Exception as e:
        return f"Overseer unavailable: {e}"

    if not trades:
        return "*Evaluate* | No open trades."

    # 2. Get TA for each traded symbol
    trade_syms = list({
        t["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
        for t in trades
    })
    btc_ref_df = bybit_kline("BTCUSDT", "60", 200)
    btc_ref = btc_ref_df if not btc_ref_df.empty else None
    ta_map = {}
    ta_context = []
    for sym in trade_syms:
        df = bybit_kline(f"{sym}USDT", "60", 200)
        if df.empty:
            continue
        ind = calc_indicators(df, btc_df=btc_ref)
        if not ind:
            continue
        sig = detect_signals(ind, sym)
        ta_map[sym] = {"ind": ind, "sig": sig}
        entry = sig["entries"][0] if sig["entries"] else "none"
        exit_s = sig["exits"][0] if sig["exits"] else "none"
        ta_context.append(
            f"{sym}: ${ind['price']:.4g} {ind['trend']} TA:{sig['ta_score']} "
            f"RSI:{ind['rsi']:.0f} WR:{ind['willr']:.0f} "
            f"MACD:{ind['macd_signal_text']} CMF:{ind['cmf']:.3f} "
            f"S:{ind['support']:.4g} R:{ind['resistance']:.4g} "
            f"signal:{entry} exit:{exit_s}"
        )

    # 3. Build trade lines for Claude
    trade_lines = []
    for t in sorted(trades, key=lambda x: x["profit_pct"]):
        pair = t["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
        inst = t["instance"][0]
        tag = t.get("enter_tag", "") or "?"
        trade_lines.append(
            f"{pair}[{inst}] {t['profit_pct']:+.2f}% ${t['current_rate']:.4g} {tag}"
        )

    # 4. Claude: get action per trade (compact JSON)
    ta_block = "\n".join(ta_context) if ta_context else "No TA data"
    trades_block = "\n".join(trade_lines)

    prompt = f"""Classify each trade. TA data then trades.

{ta_block}

{trades_block}

Reply ONLY with one line per trade, format: PAIR ACTION reason
ACTION is HOLD, TRAIL, or CUT. Reason max 6 words. Example:
ETH HOLD RSI:50 uptrend intact
FHE CUT RSI:26 broke support"""

    raw = claude_analyze(prompt, max_tokens=400)

    # 5. Parse actions into lookup
    actions = {}
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) >= 2:
            sym = parts[0].replace("[s]", "").replace("[f]", "")
            act = parts[1].upper()
            reason = parts[2] if len(parts) > 2 else ""
            if act in ("HOLD", "TRAIL", "CUT"):
                actions[sym] = {"action": act, "reason": reason}

    # 6. Build Freqtrade-style table
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    sorted_trades = sorted(trades, key=lambda x: x["profit_pct"], reverse=True)

    # P/L totals
    total_pnl = sum(t["profit_abs"] for t in trades)
    spot_trades = [t for t in trades if t["instance"] == "spot"]
    fut_trades = [t for t in trades if t["instance"] == "futures"]

    lines = [f"*Evaluate* | {now_str}\n"]

    # Header
    lines.append("`  # Pair         P/L%   Action  Reason`")
    lines.append("`" + "-" * 50 + "`")

    for t in sorted_trades:
        pair = t["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
        inst = t["instance"][0]
        tid = t.get("trade_id", "?")
        pct = t["profit_pct"]

        display = f"{pair}" if inst == "s" else f"{pair}(f)"
        act_info = actions.get(pair, {"action": "HOLD", "reason": ""})
        act = act_info["action"]
        reason = act_info["reason"][:20]

        # Action icon
        if act == "CUT":
            icon = "\u2716"
        elif act == "TRAIL":
            icon = "\u2795"
        else:
            icon = "\u2022"

        lines.append(
            f"`{tid:>3} {display:<12} {pct:>+6.1f}%` {icon}`{act:<5}` _{reason}_"
        )

    lines.append("`" + "-" * 50 + "`")
    lines.append(f"`    TOTAL      {total_pnl:>+8.4f} USDT  ({len(trades)} trades)`")

    # Summary counts
    cuts = sum(1 for a in actions.values() if a["action"] == "CUT")
    trails = sum(1 for a in actions.values() if a["action"] == "TRAIL")
    holds = len(trades) - cuts - trails
    if cuts:
        lines.append(f"\n\u2716 *{cuts} CUT* | \u2795 {trails} TRAIL | \u2022 {holds} HOLD")
    else:
        lines.append(f"\n\u2795 {trails} TRAIL | \u2022 {holds} HOLD")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command dispatch — returns response string (matches sygnif_bot.py pattern)
# ---------------------------------------------------------------------------
COMMANDS = {
    "/start": lambda args: cmd_start(),
    "/finance-agent": lambda args: cmd_finance_agent(args),
    "/overview": lambda args: cmd_overview(),
    "/tendency": lambda args: cmd_tendency(),
    "/market":   lambda args: cmd_market(),
    "/movers":   lambda args: cmd_movers(),
    "/ta":       lambda args: cmd_ta(args),
    "/signals":  lambda args: cmd_signals(),
    "/scan":     lambda args: cmd_scan(),
    "/research": lambda args: cmd_research(args),
    "/plays":    lambda args: cmd_plays(),
    "/news":     lambda args: cmd_news(args),
    "/overseer": lambda args: cmd_overseer(),
    "/evaluate": lambda args: cmd_evaluate(),
    "/macro":    lambda args: cmd_macro(),
    "/tradfi":   lambda args: cmd_tradfi(),
    "/fdn":      lambda args: cmd_tradfi(),
    "/ask":      lambda args: cmd_ask(args),
    "/fa_help":  lambda args: cmd_help(),
}


# Commands that take a while — send a loading message first
_SLOW_COMMANDS = {
    "/finance-agent",
    "/overview",
    "/tendency",
    "/signals",
    "/scan",
    "/research",
    "/plays",
    "/evaluate",
    "/macro",
    "/ask",
}

# Loading messages per command
_LOADING_MSG = {
    "/finance-agent": "Running finance-agent workflow...",
    "/overview":  "\U0001f50d Contacting overseer + scanning TA...",
    "/tendency":  "\U0001f4ca Scanning market tendency...",
    "/signals":   "\U0001f4e1 Scanning signals across top pairs...",
    "/research":  "\U0001f9e0 Researching — TA + news + AI analysis...",
    "/plays":     "\U0001f3af Scanning opportunities — TA + AI...",
    "/scan":      "\U0001f50e Scanning opportunities — TA + news + AI ranking...",
    "/macro":     "\U0001f30d Building macro-crypto context...",
    "/evaluate":  "\U0001f916 Evaluating positions...",
    "/ask":       "\U0001f4ac Preparing answer...",
}


def handle_command(text: str) -> str | tuple | None:
    """Route command to handler.

    Returns:
        str — immediate response (fast commands)
        tuple(loading_msg, handler, args) — for slow commands (dispatcher sends loading first)
        None — unknown command
    """
    if not text.strip().startswith("/"):
        return None

    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]  # strip @botname suffix
    cmd = _COMMAND_ALIASES.get(cmd, cmd)
    args = parts[1] if len(parts) > 1 else ""

    handler = COMMANDS.get(cmd)
    if handler is None:
        return "Unknown command. Try /fa_help"

    try:
        if cmd in _SLOW_COMMANDS:
            loading = _LOADING_MSG.get(cmd, "\u23f3 Working...")
            # Return loading msg + handler callable — dispatcher sends loading first
            if cmd == "/ask" and not (args or "").strip():
                return handler(args)
            return (loading, handler, args)
        return handler(args)
    except Exception as e:
        logger.error(f"Command {cmd} error: {traceback.format_exc()}")
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# HTTP server for overseer integration (:8091)
# ---------------------------------------------------------------------------
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import json as _json


def _briefing(symbols: list[str] | None = None) -> str:
    """Return compact market briefing optimized for Plutus-3B consumption.

    Format per line (pipe-delimited for easy 3B parsing):
      BTC $67,200 uptrend|RSI:65 WR:-32 StRSI:45|MACD:bull CMF:+0.12|S:65800 R:68400|TA:72 strong_ta_long 5x
    """
    lines = []
    # Always include BTC + ETH
    core = ["BTCUSDT", "ETHUSDT"]
    extra = [f"{s}USDT" for s in (symbols or []) if f"{s}USDT" not in core]
    btc_ref_df = bybit_kline("BTCUSDT", "60", 200)
    btc_ref = btc_ref_df if not btc_ref_df.empty else None
    for sym in core + extra[:4]:  # max 6 total
        df = bybit_kline(sym, interval="60", limit=200)
        if df.empty:
            continue
        ta = calc_indicators(df, btc_df=btc_ref)
        if not ta:
            continue
        name = sym.replace("USDT", "")
        sig = detect_signals(ta, name)
        price = ta.get("price", 0)
        if price >= 100:
            pf = f"${price:,.0f}"
        elif price >= 1:
            pf = f"${price:.2f}"
        else:
            pf = f"${price:.5f}"
        trend = ta.get("trend", "?").replace("Strong ", "s-")
        rsi = ta.get("rsi", 0)
        willr = ta.get("willr", -50)
        stochrsi = ta.get("stochrsi_k", 50)
        macd_sig = ta.get("macd_signal_text", "?").lower().replace(" ", "_")
        cmf = ta.get("cmf", 0)
        sup = ta.get("support", 0)
        res = ta.get("resistance", 0)
        entry = sig["entries"][0] if sig["entries"] else "none"
        exit_sig = sig["exits"][0] if sig["exits"] else ""
        exit_part = f" EXIT:{exit_sig}" if exit_sig else ""
        lev = sig["leverage"]

        lines.append(
            f"{name} {pf} {trend}"
            f"|RSI:{rsi:.0f} WR:{willr:.0f} StRSI:{stochrsi:.0f}"
            f"|MACD:{macd_sig} CMF:{cmf:+.2f}"
            f"|S:{sup:.4g} R:{res:.4g}"
            f"|TA:{sig['ta_score']} {entry} {lev:.0f}x{exit_part}"
        )
    return "\n".join(lines) if lines else "No data"


def _build_local_overseer_commentary(prompt: str) -> str:
    """Deterministic local commentary for trade_overseer.

    Produces one line per trade in parser-friendly format:
      COINs +1.23%: HOLD — reason
    """
    import re

    lines = []
    seen = set()
    for raw in (prompt or "").splitlines():
        m = re.search(r"\b([A-Z0-9]+)\[([sf])\]\s+([+-]?\d+(?:\.\d+)?)%", raw)
        if not m:
            continue
        sym, inst, pct_s = m.group(1), m.group(2), m.group(3)
        key = (sym, inst)
        if key in seen:
            continue
        seen.add(key)
        pct = float(pct_s)
        if pct <= -2.0:
            action = "CUT"
            reason = "loss beyond tolerance"
        elif pct >= 3.0:
            action = "TRAIL"
            reason = "lock gains, trend extension"
        else:
            action = "HOLD"
            reason = "no decisive trigger"
        lines.append(f"{sym}{inst} {pct:+.2f}%: {action} — {reason}")

    if not lines:
        return "No actionable trades."
    return "\n".join(lines[:12])


class _BriefingHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/briefing"):
            # Parse ?symbols=BTC,ETH,SOL from query
            syms = None
            if "?" in self.path:
                qs = self.path.split("?", 1)[1]
                for part in qs.split("&"):
                    if part.startswith("symbols="):
                        syms = part.split("=", 1)[1].split(",")
            body = _briefing(syms)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
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
        if self.path == "/overseer/commentary":
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw.decode("utf-8", errors="ignore"))
                prompt = str(payload.get("prompt", "") or "")
                commentary = _build_local_overseer_commentary(prompt)
                body = json.dumps({"commentary": commentary})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())
            except Exception as e:
                body = json.dumps({"commentary": "", "error": str(e)})
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())
            return
        self.send_response(404)
        self.end_headers()


def _start_http():
    http_host = os.environ.get("FINANCE_AGENT_HTTP_HOST", "127.0.0.1")
    http_port = int(os.environ.get("FINANCE_AGENT_HTTP_PORT", "8091"))
    server = HTTPServer((http_host, http_port), _BriefingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Briefing HTTP server on %s:%s", http_host, http_port)


def main():
    if not TG_TOKEN:
        print("Set FINANCE_BOT_TOKEN env var")
        sys.exit(1)
    if not TG_CHAT:
        print("Set TELEGRAM_CHAT_ID env var")
        sys.exit(1)

    _start_http()

    logger.info("Finance Agent started. Polling for commands...")
    tg_send("Finance Agent online.", reply_markup=KEYBOARD)

    offset = 0
    while True:
        try:
            updates, offset = tg_poll(offset)
            for update in updates:
                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))
                user = msg.get("from", {}) or {}
                username = user.get("username") or "-"
                user_id = user.get("id") or "-"
                stripped = (text or "").strip()
                if stripped:
                    cmd_name = stripped.split(maxsplit=1)[0] if stripped.startswith("/") else "<text>"
                    logger.info(
                        "telegram_in chat=%s user=%s(%s) cmd=%s text=%r",
                        chat_id,
                        username,
                        user_id,
                        cmd_name,
                        stripped[:120],
                    )
                if text and str(chat_id) == str(TG_CHAT):
                    reply = handle_command(text)
                    if reply is None:
                        logger.info("telegram_dispatch cmd=unknown chat=%s", chat_id)
                        continue
                    if isinstance(reply, tuple):
                        # Slow command: (loading_msg, handler, args)
                        loading, handler_fn, handler_args = reply
                        logger.info("telegram_dispatch cmd=slow loading=%r chat=%s", loading, chat_id)
                        tg_send(loading)
                        try:
                            result = handler_fn(handler_args)
                            logger.info("telegram_result kind=slow chars=%s chat=%s", len(result or ""), chat_id)
                            tg_send(result, reply_markup=KEYBOARD)
                        except Exception as e:
                            logger.error(f"Slow command error: {traceback.format_exc()}")
                            tg_send(f"Error: {e}", reply_markup=KEYBOARD)
                    else:
                        logger.info("telegram_result kind=fast chars=%s chat=%s", len(reply or ""), chat_id)
                        tg_send(reply, reply_markup=KEYBOARD)
                elif stripped:
                    logger.warning("telegram_reject unauthorized_chat=%s allowed_chat=%s", chat_id, TG_CHAT)
        except KeyboardInterrupt:
            logger.info("Shutting down.")
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
