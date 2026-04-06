"""
SygnifStrategy - NFI-Enhanced Trading Bot with AI Sentiment Layer

Architecture (inspired by NostalgiaForInfinityX7):
- Multi-timeframe analysis: 5m base + 5m/15m/1h/4h/1d informative
- BTC correlation: BTC/USDT indicators merged into all pairs
- NFI-style indicators: RSI_3/14, Aroon, StochRSI, CMF, CCI, ROC, BB, EMA, Williams %R
- Global protections: Multi-TF cascade prevents buying during crashes
- Claude sentiment layer: When signals are ambiguous, Claude Haiku analyzes news
- NFI-style exit logic: Profit-tiered RSI exits + overbought signals + doom stoploss

Cost: ~$0.50-1.00/month with Haiku at ~20 calls/day
"""

import logging
import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import feedparser
import requests
import numpy as np
import pandas as pd
import pandas_ta as pta
import talib.abstract as ta
from freqtrade.strategy import IStrategy, merge_informative_pair
from freqtrade.persistence import Trade
from pandas import DataFrame

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sygnif Sentiment Layer
# ---------------------------------------------------------------------------

class SygnifSentiment:
    """Lightweight Claude API wrapper for crypto sentiment analysis."""

    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = "claude-haiku-4-5-20251001"
        self.base_url = "https://api.anthropic.com/v1/messages"
        self._cache: dict[str, tuple[float, float]] = {}
        self._news_cache: dict[str, tuple[float, list[str]]] = {}
        self.cache_ttl = 900  # 15 min cache (shorter for 5m TF)
        self.news_cache_ttl = 600  # 10 min news cache
        self.daily_calls = 0
        self.daily_limit = 50  # Higher limit for 5m TF
        self._last_reset = datetime.now().date()

    def _reset_daily_counter(self):
        today = datetime.now().date()
        if today > self._last_reset:
            self.daily_calls = 0
            self._last_reset = today

    def _get_cached(self, token: str) -> Optional[float]:
        if token in self._cache:
            ts, score = self._cache[token]
            if time.time() - ts < self.cache_ttl:
                return score
        return None

    def _fetch_rss(self, feed_url: str, token: str) -> list[str]:
        """Fetch headlines from a single RSS feed."""
        try:
            feed = feedparser.parse(feed_url)
            titles = []
            for entry in feed.entries[:3]:
                title = entry.get("title", "")
                if token.upper() in title.upper() or len(titles) < 2:
                    titles.append(title)
            return titles
        except Exception as e:
            logger.warning(f"Feed error {feed_url}: {e}")
            return []

    def _fetch_gdelt(self, token: str) -> list[str]:
        """Fetch headlines from GDELT API."""
        try:
            gdelt_url = (
                f"https://api.gdeltproject.org/api/v2/doc/doc"
                f"?query={token}%20crypto&mode=artlist&maxrecords=5&format=json"
            )
            resp = requests.get(gdelt_url, timeout=5)
            if resp.ok:
                return [art.get("title", "") for art in resp.json().get("articles", [])[:3]]
        except Exception:
            pass
        return []

    def fetch_news(self, token: str, max_items: int = 5) -> list[str]:
        """Fetch recent crypto news from free RSS feeds (parallel, cached)."""
        if token in self._news_cache:
            ts, cached_headlines = self._news_cache[token]
            if time.time() - ts < self.news_cache_ttl:
                return cached_headlines

        feeds = [
            f"https://cryptopanic.com/news/{token.lower()}/rss/",
            "https://cointelegraph.com/rss",
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
        ]
        headlines = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            rss_futures = {pool.submit(self._fetch_rss, url, token): url for url in feeds}
            gdelt_future = pool.submit(self._fetch_gdelt, token)
            for future in as_completed(rss_futures):
                headlines.extend(future.result())
            headlines.extend(gdelt_future.result())

        result = headlines[:max_items]
        self._news_cache[token] = (time.time(), result)
        return result

    def analyze_sentiment(
        self,
        token: str,
        current_price: float,
        ta_score: float,
        headlines: list[str],
    ) -> float:
        """
        Ask Claude for a sentiment score.
        Returns: adjustment between -20 and +20
        """
        self._reset_daily_counter()

        cached = self._get_cached(token)
        if cached is not None:
            logger.info(f"Claude sentiment (cached) for {token}: {cached}")
            return cached

        if self.daily_calls >= self.daily_limit:
            logger.warning("Claude daily limit reached, returning neutral")
            return 0.0

        if not self.api_key:
            logger.warning("No ANTHROPIC_API_KEY set, skipping sentiment")
            return 0.0

        news_text = "\n".join(f"- {h}" for h in headlines) if headlines else "No recent news available."

        prompt = f"""Analyze the current sentiment for {token} cryptocurrency.

Current price: ${current_price:.4f}
Technical analysis score: {ta_score:.0f}/100 (50 = neutral, >60 = bullish, <40 = bearish)

Recent headlines:
{news_text}

Based on the news sentiment and market context, provide a sentiment adjustment score.
Rules:
- Score between -20 (very bearish news) and +20 (very bullish news)
- 0 = neutral / no significant news impact
- Consider: regulatory news, partnerships, exchange listings, whale movements, macro events
- Be conservative — only give extreme scores for genuinely significant events

Respond with ONLY a JSON object: {{"score": <number>, "reason": "<one sentence>"}}"""

        try:
            resp = requests.post(
                self.base_url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=10,
            )

            self.daily_calls += 1

            if resp.ok:
                data = resp.json()
                text = data["content"][0]["text"]
                # Extract JSON object from text (Claude sometimes wraps in markdown/extra text)
                import re
                match = re.search(r'\{[^{}]*"score"[^{}]*\}', text, re.DOTALL)
                if not match:
                    logger.error(f"Claude sentiment: no JSON object found in response: {text[:200]}")
                    return None  # API error → caller decides fallback
                result = json.loads(match.group(0))
                score = max(-20, min(20, float(result["score"])))
                reason = result.get("reason", "")
                logger.info(f"Claude sentiment for {token}: {score} — {reason}")

                self._cache[token] = (time.time(), score)
                return score
            else:
                logger.error(f"Claude API error: {resp.status_code} {resp.text}")
                return None  # API error → caller decides fallback

        except Exception as e:
            logger.error(f"Claude sentiment error: {e}")
            return None  # API error → caller decides fallback


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class SygnifStrategy(IStrategy):
    """
    Sygnif — NFI-Enhanced Freqtrade strategy with AI sentiment analysis.

    Based on NostalgiaForInfinityX7 patterns:
    - Multi-timeframe indicators (5m + 15m/1h/4h/1d)
    - BTC correlation
    - Global protections cascade
    - Profit-tiered RSI exits
    - Claude sentiment for ambiguous signals
    """

    INTERFACE_VERSION = 3
    can_short = False  # Overridden to True in __init__ when futures mode

    # --- Core settings (NFI-style) ---
    stoploss = -0.20  # Base SL (overridden per-trade by custom_stoploss)
    trailing_stop = False
    use_custom_stoploss = True

    timeframe = "5m"
    info_timeframes = ["15m", "1h", "4h", "1d"]

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True

    startup_candle_count: int = 400

    # Minimal ROI — let custom_exit handle exits
    minimal_roi = {"0": 100}

    # Protections — lock pair after repeated stoploss hits
    protections = [
        {"method": "StoplossGuard", "lookback_period_candles": 12,
         "trade_limit": 2, "stop_duration_candles": 48, "only_per_pair": True},
        {"method": "CooldownPeriod", "stop_duration_candles": 5},
    ]

    # --- Thresholds ---
    stop_threshold_doom_spot = 0.20     # -20% doom stoploss (spot)
    stop_threshold_doom_futures = 0.20  # -20% doom stoploss (futures, divided by leverage)
    soft_sl_ratio_spot = 0.60           # soft SL at 60% of doom = -12% P&L (spot)
    soft_sl_ratio_futures = 0.60        # soft SL at 60% of doom = -12% P&L (futures, tighter due to margin/funding cost)

    # --- Leverage ---
    futures_mode_leverage = 3.0
    futures_mode_leverage_majors = 5.0  # BTC, ETH, SOL, XRP — slow movers, higher leverage ok

    # Major pairs eligible for 5x
    major_pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]

    # Sentiment thresholds
    sentiment_threshold_buy = 55.0
    sentiment_threshold_sell = 40.0

    # Movers tracking — top gainers/losers refreshed every 4h
    _movers_pairs: list[str] = []
    _movers_last_update: float = 0.0
    _movers_refresh_secs: int = 14400  # 4h

    # Doom cooldown — per-pair lockout after stoploss hit
    _doom_cooldown: dict[str, float] = {}
    doom_cooldown_secs = 14400  # 4h
    _doom_loss_count: dict[str, list[float]] = {}  # consecutive loss tracking

    # Slot caps per entry type (prevent one type hogging all slots)
    max_slots_strong = 6      # strong_ta entries (TA >= 65)
    max_slots_swing = 4       # swing_failure, claude_swing, etc.
    _swing_tags = {"swing_failure", "claude_swing", "swing_failure_short", "claude_swing_short"}

    # Claude layer
    claude = SygnifSentiment()

    # -------------------------------------------------------------------------
    # Enable shorts dynamically for futures mode
    # -------------------------------------------------------------------------
    def bot_start(self, **kwargs) -> None:
        if self.config.get("trading_mode", "") == "futures":
            self.can_short = True
        self._load_doom_cooldown()
        self._refresh_movers()

    def bot_loop_start(self, current_time=None, **kwargs) -> None:
        """Inject movers into active whitelist each loop iteration."""
        self._refresh_movers()
        if self._movers_pairs and self.dp:
            current_wl = self.dp.current_whitelist()
            for pair in self._movers_pairs:
                if pair not in current_wl:
                    current_wl.append(pair)
                    logger.info(f"Mover {pair} added to whitelist")

    # -------------------------------------------------------------------------
    # Doom cooldown persistence — survive restarts
    # -------------------------------------------------------------------------
    _doom_cooldown_path = "user_data/doom_cooldown.json"

    def _load_doom_cooldown(self):
        try:
            with open(self._doom_cooldown_path) as f:
                data = json.load(f)
            now = time.time()
            self._doom_cooldown = {
                k: v for k, v in data.get("cooldowns", data).items()
                if now - v < self.doom_cooldown_secs
            }
            # Load consecutive loss tracking
            loss_counts = data.get("loss_counts", {})
            self._doom_loss_count = {
                k: [t for t in v if now - t < 86400]
                for k, v in loss_counts.items()
            }
        except (FileNotFoundError, json.JSONDecodeError):
            self._doom_cooldown = {}
            self._doom_loss_count = {}

    def _save_doom_cooldown(self):
        try:
            with open(self._doom_cooldown_path, "w") as f:
                json.dump({
                    "cooldowns": self._doom_cooldown,
                    "loss_counts": self._doom_loss_count,
                }, f)
        except OSError as e:
            logger.warning(f"Failed to save doom cooldown: {e}")

    # -------------------------------------------------------------------------
    # Fetch top gainers/losers from movers file (refreshed every 4h)
    # -------------------------------------------------------------------------
    def _refresh_movers(self):
        now = time.time()
        if now - self._movers_last_update < self._movers_refresh_secs and self._movers_pairs:
            return
        try:
            # Walk up from this file until we find user_data/
            base = Path(__file__).resolve().parent
            while base != base.parent:
                candidate = base / "user_data" / "movers_pairlist.json"
                if candidate.exists():
                    break
                base = base.parent
            movers_file = base / "user_data" / "movers_pairlist.json"
            if not movers_file.exists():
                logger.warning(f"Movers file not found: {movers_file}")
                return
            data = json.loads(movers_file.read_text())
            pairs = data.get("exchange", {}).get("pair_whitelist", [])
            if pairs:
                self._movers_pairs = pairs
                self._movers_last_update = now
                meta = data.get("_meta", {})
                logger.info(
                    f"Movers loaded: gainers={meta.get('gainers', [])}, "
                    f"losers={meta.get('losers', [])}"
                )
        except Exception as e:
            logger.warning(f"Movers refresh failed: {e}")

    # -------------------------------------------------------------------------
    # Informative pairs — BTC data + movers
    # -------------------------------------------------------------------------
    def informative_pairs(self):
        self._refresh_movers()
        is_futures = self.config.get("trading_mode", "") == "futures"
        btc_pair = "BTC/USDT:USDT" if is_futures else "BTC/USDT"

        pairs = []
        # BTC correlation data
        for tf in self.info_timeframes:
            pairs.append((btc_pair, tf))

        # All whitelist pairs need higher TF data for exits + crash protection
        whitelist = self.dp.current_whitelist() if self.dp else []
        for pair in whitelist:
            for tf in self.info_timeframes:
                pairs.append((pair, tf))

        # Movers (may not be in whitelist yet)
        for mover in self._movers_pairs:
            for tf in [self.timeframe] + self.info_timeframes:
                pairs.append((mover, tf))

        return pairs

    # -------------------------------------------------------------------------
    # BTC informative indicators
    # -------------------------------------------------------------------------
    def btc_informative_indicators(self, btc_df: DataFrame, timeframe: str) -> DataFrame:
        btc_df["btc_RSI_3"] = pta.rsi(btc_df["close"], length=3)
        btc_df["btc_RSI_14"] = pta.rsi(btc_df["close"], length=14)
        _r = pta.ema(btc_df["close"], length=200)
        btc_df["btc_EMA_200"] = _r if _r is not None else np.nan
        btc_df["btc_change_pct"] = (btc_df["close"] - btc_df["open"]) / btc_df["open"] * 100.0
        # Rename to avoid collision
        ignore_columns = ["date", "btc_RSI_3", "btc_RSI_14", "btc_EMA_200", "btc_change_pct"]
        btc_df.drop(columns=[c for c in btc_df.columns if c not in ignore_columns], inplace=True)
        return btc_df

    # -------------------------------------------------------------------------
    # Informative timeframe indicators
    # -------------------------------------------------------------------------
    def informative_indicators(self, df: DataFrame, timeframe: str) -> DataFrame:
        if len(df) < 2 or "close" not in df.columns:
            return df
        # RSI
        df["RSI_3"] = pta.rsi(df["close"], length=3)
        df["RSI_14"] = pta.rsi(df["close"], length=14)
        df["RSI_3_change_pct"] = df["RSI_3"].pct_change() * 100.0
        # EMA
        df["EMA_12"] = pta.ema(df["close"], length=12)
        df["EMA_200"] = pta.ema(df["close"], length=200, )
        # BB
        if len(df) >= 20:
            bbands = pta.bbands(df["close"], length=20)
            if isinstance(bbands, pd.DataFrame) and "BBL_20_2.0" in bbands.columns:
                df["BBL_20_2.0"] = bbands["BBL_20_2.0"]
                df["BBM_20_2.0"] = bbands["BBM_20_2.0"]
                df["BBU_20_2.0"] = bbands["BBU_20_2.0"]
        for col in ["BBL_20_2.0", "BBM_20_2.0", "BBU_20_2.0"]:
            if col not in df.columns:
                df[col] = np.nan
        # CMF
        df["CMF_20"] = pta.cmf(df["high"], df["low"], df["close"], df["volume"], length=20)
        # Aroon
        aroon = pta.aroon(df["high"], df["low"], length=14)
        if isinstance(aroon, pd.DataFrame) and "AROONU_14" in aroon.columns:
            df["AROONU_14"] = aroon["AROONU_14"]
            df["AROOND_14"] = aroon["AROOND_14"]
        for col in ["AROONU_14", "AROOND_14"]:
            if col not in df.columns:
                df[col] = np.nan
        # StochRSI
        stochrsi = pta.stochrsi(df["close"])
        if isinstance(stochrsi, pd.DataFrame) and "STOCHRSIk_14_14_3_3" in stochrsi.columns:
            df["STOCHRSIk_14_14_3_3"] = stochrsi["STOCHRSIk_14_14_3_3"]
        if "STOCHRSIk_14_14_3_3" not in df.columns:
            df["STOCHRSIk_14_14_3_3"] = np.nan
        # CCI
        df["CCI_20"] = pta.cci(df["high"], df["low"], df["close"], length=20)
        # ROC
        df["ROC_9"] = pta.roc(df["close"], length=9)
        # Williams %R
        df["WILLR_14"] = pta.willr(df["high"], df["low"], df["close"], length=14)
        return df

    # -------------------------------------------------------------------------
    # Populate indicators
    # -------------------------------------------------------------------------
    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        if len(df) < self.startup_candle_count:
            return df
        try:
            return self._populate_indicators_inner(df, metadata)
        except (ValueError, KeyError) as e:
            logger.warning(f"[{metadata.get('pair')}] Skipping indicators: {e}")
            return df

    def _populate_indicators_inner(self, df: DataFrame, metadata: dict) -> DataFrame:
        tik = time.perf_counter()

        # --- BTC informative (all timeframes) ---
        is_futures = self.config.get("trading_mode", "") == "futures"
        btc_pair = "BTC/USDT:USDT" if is_futures else "BTC/USDT"
        if metadata["pair"] != btc_pair:
            for tf in self.info_timeframes:
                btc_df = self.dp.get_pair_dataframe(btc_pair, tf)
                if len(btc_df) < 2:
                    continue
                btc_df = self.btc_informative_indicators(btc_df, tf)
                df = merge_informative_pair(df, btc_df, self.timeframe, tf, ffill=True)
                drop_cols = [f"date_{tf}"]
                df.drop(columns=df.columns.intersection(drop_cols), inplace=True)

        # --- Informative timeframes ---
        for tf in self.info_timeframes:
            info_df = self.dp.get_pair_dataframe(metadata["pair"], tf)
            if len(info_df) < 2:
                continue
            info_df = self.informative_indicators(info_df, tf)
            df = merge_informative_pair(df, info_df, self.timeframe, tf, ffill=True)
            drop_cols = [f"date_{tf}", f"open_{tf}", f"high_{tf}", f"low_{tf}", f"close_{tf}", f"volume_{tf}"]
            df.drop(columns=df.columns.intersection(drop_cols), inplace=True)

        # --- Base 5m indicators (full NFI set) ---
        # RSI
        df["RSI_3"] = pta.rsi(df["close"], length=3)
        df["RSI_4"] = pta.rsi(df["close"], length=4)
        df["RSI_14"] = pta.rsi(df["close"], length=14)
        df["RSI_20"] = pta.rsi(df["close"], length=20)
        df["RSI_3_change_pct"] = df["RSI_3"].pct_change() * 100.0
        df["RSI_14_change_pct"] = df["RSI_14"].pct_change() * 100.0
        df["RSI_14_shift3"] = df["RSI_14"].shift(3)
        # EMA (full spectrum)
        df["EMA_3"] = pta.ema(df["close"], length=3)
        df["EMA_9"] = pta.ema(df["close"], length=9)
        df["EMA_12"] = pta.ema(df["close"], length=12)
        df["EMA_16"] = pta.ema(df["close"], length=16)
        df["EMA_20"] = pta.ema(df["close"], length=20)
        df["EMA_26"] = pta.ema(df["close"], length=26)
        df["EMA_50"] = pta.ema(df["close"], length=50)
        _r = pta.ema(df["close"], length=100)
        df["EMA_100"] = _r if _r is not None else np.nan
        _r = pta.ema(df["close"], length=200)
        df["EMA_200"] = _r if _r is not None else np.nan
        # SMA
        df["SMA_9"] = pta.sma(df["close"], length=9)
        df["SMA_16"] = pta.sma(df["close"], length=16)
        df["SMA_21"] = pta.sma(df["close"], length=21)
        df["SMA_30"] = pta.sma(df["close"], length=30)
        df["SMA_200"] = pta.sma(df["close"], length=200)
        # BB 20 - STD2
        bbands_20 = pta.bbands(df["close"], length=20)
        if isinstance(bbands_20, pd.DataFrame) and "BBL_20_2.0" in bbands_20.columns:
            df["BBL_20_2.0"] = bbands_20["BBL_20_2.0"]
            df["BBM_20_2.0"] = bbands_20["BBM_20_2.0"]
            df["BBU_20_2.0"] = bbands_20["BBU_20_2.0"]
            df["BBB_20_2.0"] = bbands_20["BBB_20_2.0"]
            df["BBP_20_2.0"] = bbands_20["BBP_20_2.0"]
        for col in ["BBL_20_2.0", "BBM_20_2.0", "BBU_20_2.0", "BBB_20_2.0", "BBP_20_2.0"]:
            if col not in df.columns:
                df[col] = np.nan
        # BB 40 - STD2
        try:
            bb40_upper, bb40_middle, bb40_lower = ta.BBANDS(df["close"], timeperiod=40, nbdevup=2.0, nbdevdn=2.0, matype=0)
            df["BBL_40_2.0"] = bb40_lower
            df["BBM_40_2.0"] = bb40_middle
            df["BBU_40_2.0"] = bb40_upper
            df["BBB_40_2.0"] = (bb40_upper - bb40_lower) / bb40_middle * 100.0
            df["BBP_40_2.0"] = (df["close"] - bb40_lower) / (bb40_upper - bb40_lower)
        except Exception:
            for col in ["BBL_40_2.0", "BBM_40_2.0", "BBU_40_2.0", "BBB_40_2.0", "BBP_40_2.0"]:
                df[col] = np.nan
        # MFI
        df["MFI_14"] = pta.mfi(df["high"], df["low"], df["close"], df["volume"], length=14)
        # CMF
        df["CMF_20"] = pta.cmf(df["high"], df["low"], df["close"], df["volume"], length=20)
        # Williams %R
        df["WILLR_14"] = pta.willr(df["high"], df["low"], df["close"], length=14)
        df["WILLR_480"] = pta.willr(df["high"], df["low"], df["close"], length=480)
        # Aroon
        aroon = pta.aroon(df["high"], df["low"], length=14)
        if isinstance(aroon, pd.DataFrame):
            df["AROONU_14"] = aroon["AROONU_14"]
            df["AROOND_14"] = aroon["AROOND_14"]
        # StochRSI (k + d)
        stochrsi = pta.stochrsi(df["close"])
        if isinstance(stochrsi, pd.DataFrame):
            df["STOCHRSIk_14_14_3_3"] = stochrsi["STOCHRSIk_14_14_3_3"]
            df["STOCHRSId_14_14_3_3"] = stochrsi["STOCHRSId_14_14_3_3"]
        # KST
        kst = pta.kst(df["close"])
        if isinstance(kst, pd.DataFrame):
            df["KST_10_15_20_30_10_10_10_15"] = kst["KST_10_15_20_30_10_10_10_15"]
            df["KSTs_9"] = kst["KSTs_9"]
        # CCI
        df["CCI_20"] = pta.cci(df["high"], df["low"], df["close"], length=20)
        # ROC
        df["ROC_2"] = pta.roc(df["close"], length=2)
        df["ROC_9"] = pta.roc(df["close"], length=9)
        # OBV
        df["OBV"] = pta.obv(df["close"], df["volume"])
        df["OBV_change_pct"] = df["OBV"].pct_change() * 100.0
        # Candle stats
        df["change_pct"] = (df["close"] - df["open"]) / df["open"] * 100.0
        df["close_delta"] = (df["close"] - df["close"].shift()).abs()
        df["close_max_6"] = df["close"].rolling(6).max()
        df["close_max_12"] = df["close"].rolling(12).max()
        df["close_max_48"] = df["close"].rolling(48).max()
        df["close_min_6"] = df["close"].rolling(6).min()
        df["close_min_12"] = df["close"].rolling(12).min()
        df["close_min_48"] = df["close"].rolling(48).min()
        df["volume_sma_25"] = pta.sma(df["volume"], length=25)
        df["ATR_14"] = pta.atr(df["high"], df["low"], df["close"], length=14)
        df["num_empty_288"] = (df["volume"] <= 0).rolling(window=288, min_periods=288).sum()

        # --- Handle NaN for merged columns ---
        for col in ["RSI_14_1h", "RSI_14_4h", "RSI_14_1d"]:
            if col in df.columns:
                df[col] = df[col].astype(np.float64).replace(to_replace=[np.nan, None], value=50.0)

        # --- Failure Swing (Stop Hunt) indicators ---
        # 48-bar S/R levels (4h on 5m TF, shifted: use closed bars only)
        df["sf_resistance"] = df["high"].shift(1).rolling(48).max()
        df["sf_support"] = df["low"].shift(1).rolling(48).min()
        # Stable level check: S/R unchanged for 2 bars (level is established)
        df["sf_resistance_stable"] = df["sf_resistance"] == df["sf_resistance"].shift(1)
        df["sf_support_stable"] = df["sf_support"] == df["sf_support"].shift(1)
        # EMA 120 for TP target
        df["EMA_120"] = pta.ema(df["close"], length=120)
        # Volatility filter: distance from EMA as % (must exceed 5%)
        df["sf_volatility"] = ((df["close"] - df["EMA_120"]).abs() / df["EMA_120"])
        df["sf_vol_filter"] = df["sf_volatility"] > 0.03
        # Long signal: wick below support but close back above + stable level
        df["sf_long"] = (
            (df["low"] <= df["sf_support"])
            & (df["close"] > df["sf_support"])
            & df["sf_support_stable"]
            & df["sf_vol_filter"]
        )
        # Short signal: wick above resistance but close back below + stable level
        df["sf_short"] = (
            (df["high"] >= df["sf_resistance"])
            & (df["close"] < df["sf_resistance"])
            & df["sf_resistance_stable"]
            & df["sf_vol_filter"]
        )
        # Dynamic SL/TP coefficients (Heavy91)
        df["sf_sl_pct"] = 0.02 + df["sf_volatility"] * 0.02  # base 2% + vol adjustment
        df["sf_tp_ema"] = df["EMA_120"] * (1 + df["sf_volatility"] * 0.05 * np.sign(df["close"] - df["EMA_120"]))

        # --- Global protections (NFI-style) ---
        df["protections_long_global"] = self._calc_global_protections(df)
        df["protections_short_global"] = self._calc_global_protections_short(df)

        # --- Exchange downtime protection ---
        if self.dp.runmode.value in ("live", "dry_run"):
            df["live_data_ok"] = df["volume"].rolling(window=72, min_periods=72).min() > 0

        tok = time.perf_counter()
        logger.debug(f"[{metadata['pair']}] populate_indicators took: {tok - tik:0.4f}s")
        return df

    # -------------------------------------------------------------------------
    # Global protections — NFI-style multi-TF cascade
    # -------------------------------------------------------------------------
    def _calc_global_protections(self, df: DataFrame) -> pd.Series:
        """
        Prevent buying when multiple timeframes confirm a crash.
        Each clause is an OR triplet — entry only blocked when ALL conditions in a clause fail.
        """
        prot = pd.Series(True, index=df.index)

        # 5m & 15m & 1h down move, higher TFs still not low enough
        if "RSI_3_15m" in df.columns and "RSI_3_1h" in df.columns:
            prot &= (
                (df["RSI_3"] > 2.0)
                | (df["RSI_3_15m"] > 15.0)
                | (df["RSI_3_1h"] > 20.0)
            )
            # 5m & 15m down, 1h & 4h still high
            prot &= (
                (df["RSI_3"] > 3.0)
                | (df["RSI_3_15m"] > 10.0)
                | (df.get("RSI_14_1h", 50.0) < 40.0)
            )

        # 5m & 1h down move, 4h still high
        if "RSI_3_1h" in df.columns and "RSI_14_4h" in df.columns:
            prot &= (
                (df["RSI_3"] > 3.0)
                | (df["RSI_3_1h"] > 25.0)
                | (df["RSI_14_4h"] < 50.0)
            )

        # 15m & 1h & 4h down move
        if "RSI_3_15m" in df.columns and "RSI_3_1h" in df.columns and "RSI_3_4h" in df.columns:
            prot &= (
                (df["RSI_3_15m"] > 5.0)
                | (df["RSI_3_1h"] > 10.0)
                | (df["RSI_3_4h"] > 15.0)
            )

        # 1h & 4h down move, 4h downtrend
        if "RSI_3_1h" in df.columns and "RSI_3_4h" in df.columns and "ROC_9_4h" in df.columns:
            prot &= (
                (df["RSI_3_1h"] > 5.0)
                | (df["RSI_3_4h"] > 10.0)
                | (df["ROC_9_4h"] > -20.0)
            )

        # 15m down, 15m & 4h still high (Aroon)
        if "RSI_3_15m" in df.columns and "AROONU_14_15m" in df.columns and "AROONU_14_4h" in df.columns:
            prot &= (
                (df["RSI_3_15m"] > 5.0)
                | (df["AROONU_14_15m"] < 50.0)
                | (df["AROONU_14_4h"] < 60.0)
            )

        # 5m down, 1h downtrend (CMF), 4h high
        if "CMF_20_1h" in df.columns and "AROONU_14_4h" in df.columns:
            prot &= (
                (df["RSI_3"] > 5.0)
                | (df["CMF_20_1h"] > -0.25)
                | (df["AROONU_14_4h"] < 70.0)
            )

        # 15m & 4h down, 1d downtrend
        if "RSI_3_15m" in df.columns and "RSI_3_4h" in df.columns and "ROC_9_1d" in df.columns:
            prot &= (
                (df["RSI_3_15m"] > 5.0)
                | (df["RSI_3_4h"] > 10.0)
                | (df["ROC_9_1d"] > -40.0)
            )

        # BTC crash protection
        if "btc_RSI_3_1h" in df.columns:
            prot &= (
                (df["btc_RSI_3_1h"] > 10.0)
                | (df.get("btc_RSI_14_4h", 50.0) < 30.0)
            )

        return prot

    # -------------------------------------------------------------------------
    # Global protections for SHORTS — NFI-style (inverse of long protections)
    # Prevent shorting when multiple timeframes confirm a pump / strong uptrend
    # -------------------------------------------------------------------------
    def _calc_global_protections_short(self, df: DataFrame) -> pd.Series:
        prot = pd.Series(True, index=df.index)

        # 5m & 15m & 1h up move — don't short into a rally
        if "RSI_3_15m" in df.columns and "RSI_3_1h" in df.columns:
            prot &= (
                (df["RSI_3"] < 90.0)
                | (df["RSI_3_15m"] < 75.0)
                | (df["RSI_3_1h"] < 75.0)
            )
            prot &= (
                (df["RSI_3"] < 95.0)
                | (df["RSI_3_15m"] < 85.0)
                | (df.get("RSI_14_1h", 50.0) > 60.0)
            )

        # 5m & 1h up move, 4h still low — don't short early rally
        if "RSI_3_1h" in df.columns and "RSI_14_4h" in df.columns:
            prot &= (
                (df["RSI_3"] < 95.0)
                | (df["RSI_3_1h"] < 75.0)
                | (df["RSI_14_4h"] > 50.0)
            )

        # 15m & 1h & 4h up move
        if "RSI_3_15m" in df.columns and "RSI_3_1h" in df.columns and "RSI_3_4h" in df.columns:
            prot &= (
                (df["RSI_3_15m"] < 90.0)
                | (df["RSI_3_1h"] < 85.0)
                | (df["RSI_3_4h"] < 80.0)
            )

        # 1h & 4h up move, 4h uptrend
        if "RSI_3_1h" in df.columns and "RSI_3_4h" in df.columns and "ROC_9_4h" in df.columns:
            prot &= (
                (df["RSI_3_1h"] < 90.0)
                | (df["RSI_3_4h"] < 85.0)
                | (df["ROC_9_4h"] < 20.0)
            )

        # Aroon uptrend — don't short strong trend
        if "AROONU_14_15m" in df.columns and "AROONU_14_4h" in df.columns:
            prot &= (
                (df["RSI_3_15m"] < 90.0)
                | (df["AROONU_14_15m"] < 75.0)
                | (df["AROONU_14_4h"] < 75.0)
            )

        # BTC pump — don't short alts during BTC rally
        if "btc_RSI_3_1h" in df.columns:
            prot &= (
                (df["btc_RSI_3_1h"] < 85.0)
                | (df.get("btc_RSI_14_4h", 50.0) > 70.0)
            )

        return prot

    # -------------------------------------------------------------------------
    # Leverage callback — NFI-style dynamic leverage
    # -------------------------------------------------------------------------
    def leverage(
        self, pair: str, current_time: datetime, current_rate: float,
        proposed_leverage: float, max_leverage: float,
        entry_tag: Optional[str], side: str, **kwargs
    ) -> float:
        # 5x for majors (BTC, ETH, SOL, XRP)
        if (pair.split(":")[0] if ":" in pair else pair) in self.major_pairs:
            tier_lev = self.futures_mode_leverage_majors
        else:
            # 3x default
            tier_lev = self.futures_mode_leverage

        # Shorts capped at 2x — unlimited upside risk in crypto
        if side == "short":
            tier_lev = min(tier_lev, 2.0)

        # Volatility cap: high ATR% → lower leverage
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if len(df) >= 14 and "ATR_14" in df.columns:
                atr_pct = (df["ATR_14"].iloc[-1] / df["close"].iloc[-1]) * 100
                if atr_pct > 3.0:
                    tier_lev = min(tier_lev, 2.0)
                elif atr_pct > 2.0:
                    tier_lev = min(tier_lev, 3.0)
        except Exception:
            pass

        return min(tier_lev, max_leverage)

    # -------------------------------------------------------------------------
    # Custom stoploss — leverage-aware, placed on exchange
    # -------------------------------------------------------------------------
    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float,
                        after_fill: bool, **kwargs) -> float:
        is_futures = self.config.get("trading_mode", "") == "futures"
        leverage = trade.leverage or 1.0
        enter_tag = trade.enter_tag or ""

        # --- Swing failure trades: use their own dynamic SL (on-exchange) ---
        if enter_tag in ("swing_failure", "claude_swing", "swing_failure_short", "claude_swing_short"):
            try:
                df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                if len(df) > 0 and "sf_sl_pct" in df.columns:
                    sf_sl = df.iloc[-1].get("sf_sl_pct", 0.02)
                    # Still apply ratcheting if trade is well in profit
                    if current_profit >= 0.05:
                        return -0.02
                    elif current_profit >= 0.02:
                        return -0.03
                    # Otherwise use the sf-specific dynamic SL (placed on exchange)
                    return -sf_sl
            except Exception:
                pass
            # Fallback: 3% if dataframe unavailable
            return -0.03

        # --- Ratcheting trail: tighten SL as profit grows ---
        if current_profit >= 0.10:
            return -0.015  # -1.5% price trail at 10%+ P&L
        elif current_profit >= 0.05:
            return -0.02   # -2% price trail at 5%+ P&L
        elif current_profit >= 0.02:
            return -0.03   # -3% price trail at 2%+ P&L
        elif current_profit >= 0.01:
            # Breakeven guard: trade was profitable, don't let it become a doom loss.
            # -1% price trail → at 5x worst case ~-4% P&L, at 3x ~-2% P&L, at 1x ~0% P&L
            return -0.01

        # --- Base SL: fixed doom stoploss ---
        sl = self.stop_threshold_doom_futures if is_futures else self.stop_threshold_doom_spot  # 0.20

        # For futures: SL is price-based, divide by leverage
        if is_futures:
            return -(sl / leverage)
        return -sl

    # -------------------------------------------------------------------------
    # Calculate vectorized TA score (enhanced with NFI indicators)
    # -------------------------------------------------------------------------
    def _calculate_ta_score_vectorized(self, df: DataFrame) -> pd.Series:
        score = pd.Series(50.0, index=df.index)

        # RSI_14 component (-15 to +15)
        rsi = df["RSI_14"].fillna(50.0)
        score += np.where(rsi < 30, 15, np.where(rsi < 40, 8, np.where(rsi > 70, -15, np.where(rsi > 60, -8, 0))))

        # RSI_3 momentum (-10 to +10)
        rsi3 = df["RSI_3"].fillna(50.0)
        score += np.where(rsi3 < 10, 10, np.where(rsi3 < 20, 5, np.where(rsi3 > 90, -10, np.where(rsi3 > 80, -5, 0))))

        # EMA crossover (-10 to +10)
        ema_bull = df["EMA_9"] > df["EMA_26"]
        ema_cross = ema_bull & (df["EMA_9"].shift(1) <= df["EMA_26"].shift(1))
        score += np.where(ema_cross, 10, np.where(ema_bull, 7, -7))

        # Bollinger (-8 to +8)
        if "BBL_20_2.0" in df.columns and "BBU_20_2.0" in df.columns:
            score += np.where(df["close"] <= df["BBL_20_2.0"], 8, np.where(df["close"] >= df["BBU_20_2.0"], -8, 0))

        # Aroon (-8 to +8)
        if "AROONU_14" in df.columns and "AROOND_14" in df.columns:
            aroonu = df["AROONU_14"].fillna(50)
            aroond = df["AROOND_14"].fillna(50)
            score += np.where((aroonu > 80) & (aroond < 30), 8, np.where((aroond > 80) & (aroonu < 30), -8, 0))

        # StochRSI (-5 to +5)
        if "STOCHRSIk_14_14_3_3" in df.columns:
            stoch = df["STOCHRSIk_14_14_3_3"].fillna(50)
            score += np.where(stoch < 20, 5, np.where(stoch > 80, -5, 0))

        # CMF (-5 to +5)
        cmf = df["CMF_20"].fillna(0)
        score += np.where(cmf > 0.15, 5, np.where(cmf < -0.15, -5, 0))

        # Multi-TF RSI (-5 to +5)
        if "RSI_14_1h" in df.columns and "RSI_14_4h" in df.columns:
            r1h = df["RSI_14_1h"].fillna(50)
            r4h = df["RSI_14_4h"].fillna(50)
            score += np.where((r1h < 35) & (r4h < 40), 5, np.where((r1h > 70) & (r4h > 65), -5, 0))

        # BTC correlation (-5 to +3)
        if "btc_RSI_14_1h" in df.columns:
            btc_rsi = df["btc_RSI_14_1h"].fillna(50)
            score += np.where(btc_rsi < 30, -5, np.where(btc_rsi > 60, 3, 0))

        # Volume confirmation (-3 to +3)
        vol_ratio = np.where(df["volume_sma_25"] > 0, df["volume"] / df["volume_sma_25"], 1.0)
        score += np.where((vol_ratio > 1.5) & (score > 50), 3, np.where((vol_ratio > 1.5) & (score < 50), -3, 0))

        return score.clip(0, 100)

    # -------------------------------------------------------------------------
    # Populate entry trend (vectorized — fast)
    # -------------------------------------------------------------------------
    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[:, "enter_long"] = 0
        df.loc[:, "enter_tag"] = ""
        if "RSI_14" not in df.columns:
            df.loc[:, "enter_short"] = 0
            return df

        # Global protections
        prot = df.get("protections_long_global", pd.Series(True, index=df.index))
        empty_ok = df.get("num_empty_288", pd.Series(0, index=df.index)).fillna(0) <= 60

        # TA score for all rows
        ta_score = self._calculate_ta_score_vectorized(df)

        # Strong TA signal — requires TA >= 65 + volume confirmation
        vol_ok = df["volume"] > (df["volume_sma_25"] * 1.2)
        strong = prot & empty_ok & (ta_score >= 65) & vol_ok
        df.loc[strong, "enter_long"] = 1
        df.loc[strong, "enter_tag"] = "strong_ta"

        # --- Claude sentiment long — ambiguous zone (TA 40-64), LAST candle only ---
        if len(df) > 0 and not df.iloc[-1].get("enter_long", 0):
            last_score = ta_score.iloc[-1]
            last_prot = prot.iloc[-1] if hasattr(prot, 'iloc') else True
            last_empty = empty_ok.iloc[-1] if hasattr(empty_ok, 'iloc') else True

            if last_prot and last_empty and 40 <= last_score <= 64:
                pair = metadata.get("pair", "XRP/USDT")
                token = pair.split("/")[0]
                price = df.iloc[-1]["close"]

                headlines = self.claude.fetch_news(token)
                sentiment = self.claude.analyze_sentiment(token, price, last_score, headlines)

                # sentiment=None → API broken, skip claude entry (don't enter blind)
                # sentiment=0 → Claude said neutral, also skip (no edge)
                # sentiment != 0 → real signal, combine with TA
                if sentiment is not None and sentiment != 0:
                    final_score = last_score + sentiment
                    if final_score >= self.sentiment_threshold_buy:
                        df.iloc[-1, df.columns.get_loc("enter_long")] = 1
                        df.iloc[-1, df.columns.get_loc("enter_tag")] = f"claude_s{sentiment:.0f}"

        # --- Failure Swing entries (last candle only) ---
        if len(df) > 0 and not df.iloc[-1].get("enter_long", 0):
            last_prot = prot.iloc[-1] if hasattr(prot, 'iloc') else True
            last_empty = empty_ok.iloc[-1] if hasattr(empty_ok, 'iloc') else True
            sf_long = df.iloc[-1].get("sf_long", False)

            if last_prot and last_empty and sf_long:
                last_score = ta_score.iloc[-1]
                if last_score >= 50:
                    # claude_swing: failure swing + TA confluence
                    df.iloc[-1, df.columns.get_loc("enter_long")] = 1
                    df.iloc[-1, df.columns.get_loc("enter_tag")] = "claude_swing"
                else:
                    # swing_failure: standalone, TA not confirming but pattern is clear
                    df.iloc[-1, df.columns.get_loc("enter_long")] = 1
                    df.iloc[-1, df.columns.get_loc("enter_tag")] = "swing_failure"


        # =====================================================================
        # SHORT ENTRIES (futures only — guarded by can_short in config)
        # =====================================================================
        df.loc[:, "enter_short"] = 0

        prot_short = df.get("protections_short_global", pd.Series(True, index=df.index))

        # Strong TA short signal — entry without Claude
        strong_short = prot_short & empty_ok & (ta_score <= 25)
        df.loc[strong_short, "enter_short"] = 1
        df.loc[strong_short, "enter_tag"] = "strong_ta_short"

        # Ambiguous zone — Claude sentiment short on LAST candle only
        if len(df) > 0 and not df.iloc[-1].get("enter_short", 0):
            last_score = ta_score.iloc[-1]
            last_prot_s = prot_short.iloc[-1] if hasattr(prot_short, 'iloc') else True
            last_empty = empty_ok.iloc[-1] if hasattr(empty_ok, 'iloc') else True

            if last_prot_s and last_empty and 30 <= last_score <= 60:
                pair = metadata.get("pair", "XRP/USDT")
                token = pair.split("/")[0]
                price = df.iloc[-1]["close"]

                headlines = self.claude.fetch_news(token)
                sentiment = self.claude.analyze_sentiment(token, price, last_score, headlines)

                # sentiment=None → API broken, skip; sentiment=0 → neutral, skip
                if sentiment is not None and sentiment != 0:
                    final_score = last_score + sentiment
                    if final_score <= self.sentiment_threshold_sell:
                        df.iloc[-1, df.columns.get_loc("enter_short")] = 1
                        df.iloc[-1, df.columns.get_loc("enter_tag")] = f"claude_short_s{sentiment:.0f}"

        # --- Failure Swing short entries (last candle only) ---
        if len(df) > 0 and not df.iloc[-1].get("enter_short", 0):
            last_prot_s = prot_short.iloc[-1] if hasattr(prot_short, 'iloc') else True
            last_empty = empty_ok.iloc[-1] if hasattr(empty_ok, 'iloc') else True
            sf_short = df.iloc[-1].get("sf_short", False)

            if last_prot_s and last_empty and sf_short:
                last_score = ta_score.iloc[-1]
                if last_score <= 50:
                    # claude_swing short: failure swing + bearish TA confluence
                    df.iloc[-1, df.columns.get_loc("enter_short")] = 1
                    df.iloc[-1, df.columns.get_loc("enter_tag")] = "claude_swing_short"
                else:
                    # swing_failure_short: standalone pattern
                    df.iloc[-1, df.columns.get_loc("enter_short")] = 1
                    df.iloc[-1, df.columns.get_loc("enter_tag")] = "swing_failure_short"

        return df

    # -------------------------------------------------------------------------
    # Populate exit trend (basic — main exits via custom_exit)
    # -------------------------------------------------------------------------
    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[:, "exit_long"] = 0
        df.loc[:, "exit_short"] = 0
        return df

    # -------------------------------------------------------------------------
    # Doom cooldown — block re-entry after stoploss hit
    # -------------------------------------------------------------------------
    def confirm_trade_entry(self, pair, order_type, amount, rate,
                            time_in_force, current_time, entry_tag, side, **kwargs):
        now = time.time()
        # Standard 4h cooldown after any stoploss
        cooldown_since = self._doom_cooldown.get(pair, 0)
        if now - cooldown_since < self.doom_cooldown_secs:
            logger.info(f"Doom cooldown active for {pair}, skipping entry")
            return False
        # Escalated 24h lockout after 2+ losses within 24h
        losses = self._doom_loss_count.get(pair, [])
        recent = [t for t in losses if now - t < 86400]
        if len(recent) >= 2:
            logger.info(f"Consecutive loss lockout for {pair} ({len(recent)} losses in 24h)")
            return False

        # Slot caps per entry type
        tag = entry_tag or ""
        open_trades = Trade.get_trades_proxy(is_open=True)

        if tag == "strong_ta":
            count = sum(1 for t in open_trades if (t.enter_tag or "") == "strong_ta")
            if count >= self.max_slots_strong:
                logger.info(f"Strong TA slot cap: {count}/{self.max_slots_strong}, skipping {pair}")
                return False

        if tag in self._swing_tags:
            count = sum(1 for t in open_trades if (t.enter_tag or "") in self._swing_tags)
            if count >= self.max_slots_swing:
                logger.info(f"Swing slot cap: {count}/{self.max_slots_swing}, skipping {pair} ({tag})")
                return False

        # Futures: minimum average volume gate (filter micro caps, swing bypasses)
        if self.config.get("trading_mode", "") == "futures" and tag not in self._swing_tags and self.dp:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if len(df) > 0 and "volume_sma_25" in df.columns:
                vol_avg = df.iloc[-1].get("volume_sma_25", 0)
                if vol_avg < 5000:
                    logger.info(f"Futures volume gate: {pair} vol_sma_25={vol_avg:.0f} < 5k, skipping")
                    return False

        return True

    def confirm_trade_exit(self, pair, trade, order_type, amount, rate,
                           time_in_force, exit_reason, current_time, **kwargs):
        reason = exit_reason.lower()
        is_loss_exit = (
            "stoploss" in reason       # stoploss_on_exchange, exit_stoploss_conditional
            or "stop_loss" in reason   # trailing_stop_loss
            or "vol_sl" in reason      # exit_sf_vol_sl, exit_sf_short_vol_sl
        )
        if is_loss_exit:
            now = time.time()
            self._doom_cooldown[pair] = now
            self._save_doom_cooldown()
            # Track consecutive losses for escalated lockout
            losses = self._doom_loss_count.get(pair, [])
            losses = [t for t in losses if now - t < 86400] + [now]  # keep last 24h
            self._doom_loss_count[pair] = losses
            logger.info(f"Doom cooldown set for {pair} after {exit_reason} ({len(losses)} losses in 24h)")
        return True

    # -------------------------------------------------------------------------
    # Custom exit — NFI-style profit-tiered exits
    # -------------------------------------------------------------------------
    def custom_exit(
        self, pair: str, trade: Trade, current_time: datetime,
        current_rate: float, current_profit: float, **kwargs
    ):
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(df) < 2:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]

        filled_entries = trade.select_filled_orders(trade.entry_side)
        if not filled_entries:
            return None

        is_short = trade.is_short
        leverage = trade.leverage or 1.0

        # ==================================================================
        # SHORT EXITS
        # ==================================================================
        if is_short:
            return self._custom_exit_short(
                last, prev, current_profit, current_rate, trade,
                filled_entries, leverage,
            )

        # ==================================================================
        # FAILURE SWING EXITS (tag-based routing)
        # ==================================================================
        enter_tag = trade.enter_tag or ""

        if enter_tag == "swing_failure":
            return self._exit_swing_failure(last, current_rate, trade, current_profit)

        if enter_tag == "claude_swing":
            # Hybrid: check both EMA-TP and Williams %R, first one wins
            sf_exit = self._exit_swing_failure(last, current_rate, trade, current_profit)
            if sf_exit:
                return sf_exit
            # Fall through to Williams %R below

        # ==================================================================
        # LONG EXITS
        # ==================================================================
        rsi14 = last.get("RSI_14", 50)
        rsi14_1h = last.get("RSI_14_1h", 50)

        # Minimum profit before RSI exits activate (don't exit tiny gains)
        min_profit_for_rsi = 0.02 if leverage <= 1.0 else 0.02 * leverage
        if current_profit >= min_profit_for_rsi:
            above_ema200 = last["close"] > last.get("EMA_200", 0)
            rsi_threshold = self._get_exit_rsi_threshold(current_profit, above_ema200, leverage)
            if rsi14 < rsi_threshold:
                return f"exit_profit_rsi_{current_profit:.1%}"

        # --- Williams %R reversal exit: was at peak, now falling ---
        # Leverage-normalized profit gate matches RSI exit pattern
        min_profit_for_willr = 0.02 if leverage <= 1.0 else 0.02 * leverage
        willr = last.get("WILLR_14", -50)
        willr_prev = prev.get("WILLR_14", -50)
        if willr is not None and willr_prev is not None:
            willr_topped = willr_prev > -10 and willr < willr_prev
            if willr_topped and current_profit > min_profit_for_willr:
                return "exit_willr_reversal"

        # --- Doom stoploss now handled by custom_stoploss + stoploss_on_exchange ---

        # --- Soft stoploss — fires before exchange SL, needs fewer conditions ---
        is_futures = self.config.get("trading_mode", "") == "futures"
        soft_sl = -(self.stop_threshold_doom_futures * self.soft_sl_ratio_futures) if is_futures else -(self.stop_threshold_doom_spot * self.soft_sl_ratio_spot)
        if current_profit < soft_sl:
            rsi_falling = rsi14 < last.get("RSI_14_shift3", rsi14)
            if rsi_falling and (last["close"] < last.get("EMA_200", float("inf")) or rsi14 > prev.get("RSI_14", 50)):
                return "exit_stoploss_conditional"

        return None

    # -------------------------------------------------------------------------
    # Failure Swing exit — Heavy91 EMA-TP + volatility-adjusted SL
    # -------------------------------------------------------------------------
    def _exit_swing_failure(self, last, current_rate, trade, current_profit):
        ema_tp = last.get("sf_tp_ema", 0)
        sl_pct = last.get("sf_sl_pct", 0.02)
        is_short = trade.is_short

        if not is_short:
            # Long TP: price reaches volatility-adjusted EMA
            if ema_tp and current_rate >= ema_tp and current_profit > 0.005:
                return "exit_sf_ema_tp"
            # Long SL: dynamic volatility-adjusted stop
            entry = trade.open_rate
            sl_price = entry * (1 - sl_pct)
            if current_rate <= sl_price:
                return "exit_sf_vol_sl"
        else:
            # Short TP: price falls to volatility-adjusted EMA
            if ema_tp and current_rate <= ema_tp and current_profit > 0.005:
                return "exit_sf_short_ema_tp"
            # Short SL: dynamic volatility-adjusted stop
            entry = trade.open_rate
            sl_price = entry * (1 + sl_pct)
            if current_rate >= sl_price:
                return "exit_sf_short_vol_sl"

        return None

    # -------------------------------------------------------------------------
    # Short exit logic — NFI-style inverted signals
    # -------------------------------------------------------------------------
    def _custom_exit_short(self, last, prev, current_profit, current_rate,
                           trade, filled_entries, leverage):
        enter_tag = trade.enter_tag or ""

        # --- Failure Swing short exits ---
        if enter_tag == "swing_failure_short":
            return self._exit_swing_failure(last, current_rate, trade, current_profit)

        if enter_tag == "claude_swing_short":
            sf_exit = self._exit_swing_failure(last, current_rate, trade, current_profit)
            if sf_exit:
                return sf_exit
            # Fall through to Williams %R below

        rsi14 = last.get("RSI_14", 50)
        rsi14_1h = last.get("RSI_14_1h", 50)

        # --- Profit-tiered RSI exit for shorts (inverted) ---
        min_profit_for_rsi = 0.02 if leverage <= 1.0 else 0.02 * leverage
        if current_profit >= min_profit_for_rsi:
            below_ema200 = last["close"] < last.get("EMA_200", float("inf"))
            rsi_threshold = self._get_short_exit_rsi_threshold(current_profit, below_ema200, leverage)
            if rsi14 > rsi_threshold:
                return f"exit_short_profit_rsi_{current_profit:.1%}"

        # --- Williams %R reversal exit for shorts: was at bottom, now rising ---
        # Leverage-normalized profit gate matches RSI exit pattern
        min_profit_for_willr = 0.02 if leverage <= 1.0 else 0.02 * leverage
        willr = last.get("WILLR_14", -50)
        willr_prev = prev.get("WILLR_14", -50)
        if willr is not None and willr_prev is not None:
            willr_bottomed = willr_prev < -90 and willr > willr_prev
            if willr_bottomed and current_profit > min_profit_for_willr:
                return "exit_short_willr_reversal"

        # --- Doom stoploss now handled by custom_stoploss + stoploss_on_exchange ---

        # --- Soft stoploss for shorts — fires before exchange SL ---
        is_futures = self.config.get("trading_mode", "") == "futures"
        soft_sl = -(self.stop_threshold_doom_futures * self.soft_sl_ratio_futures) if is_futures else -(self.stop_threshold_doom_spot * self.soft_sl_ratio_spot)
        if current_profit < soft_sl:
            rsi_rising = rsi14 > last.get("RSI_14_shift3", rsi14)
            if rsi_rising and (last["close"] > last.get("EMA_200", 0) or rsi14 < prev.get("RSI_14", 50)):
                return "exit_short_stoploss_conditional"

        return None

    # -------------------------------------------------------------------------
    # Profit-tiered RSI threshold (from NFI long_exit_main)
    # -------------------------------------------------------------------------
    def _get_exit_rsi_threshold(self, profit: float, above_ema200: bool, leverage: float = 1.0) -> float:
        """
        Higher profit → more willing to exit on RSI dip.
        Below EMA200 → stricter (lower RSI required to exit).
        Leverage-aware: at 3x, 1% profit = 0.33% price move — too thin to exit on.
        """
        # Normalize profit to price-move equivalent for leveraged trades
        adj_profit = profit / leverage if leverage > 1.0 else profit
        offset = 0 if above_ema200 else 2
        if adj_profit < 0.01:
            return 10.0 + offset
        elif adj_profit < 0.02:
            return 28.0 + offset
        elif adj_profit < 0.03:
            return 30.0 + offset
        elif adj_profit < 0.04:
            return 32.0 + offset
        elif adj_profit < 0.05:
            return 34.0 + offset
        elif adj_profit < 0.06:
            return 36.0 + offset
        elif adj_profit < 0.08:
            return 38.0 + offset
        elif adj_profit < 0.10:
            return 42.0 + offset
        elif adj_profit < 0.12:
            return 46.0 + offset
        elif adj_profit < 0.20:
            return 44.0 + offset
        else:
            return 42.0 + offset

    # -------------------------------------------------------------------------
    # Short exit RSI threshold — inverted (high RSI = cover short)
    # NFI short_exit_main pattern
    # -------------------------------------------------------------------------
    def _get_short_exit_rsi_threshold(self, profit: float, below_ema200: bool, leverage: float = 1.0) -> float:
        adj_profit = profit / leverage if leverage > 1.0 else profit
        offset = 0 if below_ema200 else -2
        if adj_profit < 0.01:
            return 90.0 + offset
        elif adj_profit < 0.02:
            return 72.0 + offset
        elif adj_profit < 0.03:
            return 70.0 + offset
        elif adj_profit < 0.04:
            return 68.0 + offset
        elif adj_profit < 0.05:
            return 66.0 + offset
        elif adj_profit < 0.06:
            return 64.0 + offset
        elif adj_profit < 0.08:
            return 62.0 + offset
        elif adj_profit < 0.10:
            return 58.0 + offset
        elif adj_profit < 0.12:
            return 54.0 + offset
        elif adj_profit < 0.20:
            return 56.0 + offset
        else:
            return 58.0 + offset

