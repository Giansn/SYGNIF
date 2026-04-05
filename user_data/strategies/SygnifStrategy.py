"""
SygnifStrategy - NFI-Enhanced Trading Bot with AI Sentiment Layer

Architecture (inspired by NostalgiaForInfinityX7):
- Multi-timeframe analysis: 5m base + 15m/1h/4h/1d informative
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
from datetime import datetime, timedelta
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
from functools import reduce

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
                result = json.loads(text)
                score = max(-20, min(20, float(result["score"])))
                reason = result.get("reason", "")
                logger.info(f"Claude sentiment for {token}: {score} — {reason}")

                self._cache[token] = (time.time(), score)
                return score
            else:
                logger.error(f"Claude API error: {resp.status_code} {resp.text}")
                return 0.0

        except Exception as e:
            logger.error(f"Claude sentiment error: {e}")
            return 0.0


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
    can_short = False  # Set to True via config: Freqtrade auto-enables in futures mode

    # --- Core settings (NFI-style) ---
    stoploss = -0.99  # Disabled — managed internally
    trailing_stop = False
    use_custom_stoploss = True

    timeframe = "5m"
    info_timeframes = ["15m", "1h", "4h", "1d"]

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True

    startup_candle_count: int = 200

    # Minimal ROI — let custom_exit handle exits
    minimal_roi = {"0": 100}

    # --- Thresholds ---
    stop_threshold_doom = 0.20  # -20% doom stoploss
    stop_threshold_normal = 0.10  # -10% normal stoploss

    # Sentiment thresholds
    sentiment_threshold_buy = 55.0
    sentiment_threshold_sell = 40.0

    # Movers refresh
    _movers_pairs: list[str] = []
    _movers_gainers: list[str] = []
    _movers_losers: list[str] = []
    _movers_last_update: float = 0.0
    _movers_refresh_secs: int = 14400  # 4h

    # Mover slot management (NFI-style tag-based routing)
    mover_max_slots_gainer = 1
    mover_max_slots_loser = 1
    mover_stake_multiplier = 0.5  # Half stake for higher-risk mover trades
    mover_tags_gainer = ["mover_gainer"]
    mover_tags_loser = ["mover_loser"]

    # Mover exit thresholds
    mover_gainer_tp_min = 0.015      # +1.5% minimum before considering exit
    mover_gainer_sl = 0.07           # -7% stoploss (tighter — momentum can reverse fast)
    mover_loser_tp_min = 0.01        # +1% quick scalp
    mover_loser_sl = 0.05            # -5% stoploss (tight — losers can keep losing)

    # --- Futures / NautilusTrader-inspired settings ---
    futures_leverage_default = 3        # Conservative default
    futures_leverage_low_vol = 5        # When ATR ratio < 0.015
    futures_leverage_high_vol = 2       # When ATR ratio > 0.03
    futures_leverage_mover_cap = 4      # Hard cap for mover trades
    atr_trailing_multiplier = 3.0      # ATR x3 for trailing stop distance
    atr_trailing_min_profit = 0.02     # Only trail after +2% profit
    ob_imbalance_block_ratio = 3.0     # Block entry if ask_vol > bid_vol * 3
    vhf_trending_threshold = 0.5       # VHF > 0.5 = trending market
    vhf_ranging_threshold = 0.3        # VHF < 0.3 = ranging market
    pressure_confirm_ratio = 1.2       # Buy pressure must exceed 1.2x sell

    # --- Adaptive slot allocation ---
    _market_regime: str = "neutral"     # "bullish", "bearish", "neutral"
    _max_long_slots: int = 5
    _max_short_slots: int = 5
    _regime_last_update: float = 0.0
    _regime_refresh_secs: int = 300     # Re-evaluate every 5 min

    # Short entry requires higher conviction than long (crypto long bias)
    short_entry_threshold = 80          # vs 75 for longs
    short_stake_multiplier = 0.75       # 75% of normal stake for shorts

    # Stale trade timeout (funding rate protection)
    stale_trade_hours = 24              # Exit losing trades older than this
    stale_trade_hours_leveraged = 12    # Tighter for high leverage (>3x)

    # Claude layer
    claude = SygnifSentiment()

    # -------------------------------------------------------------------------
    # Fetch top gainers/losers from Bybit API (refreshed every 4h)
    # -------------------------------------------------------------------------
    def _refresh_movers(self):
        now = time.time()
        if now - self._movers_last_update < self._movers_refresh_secs and self._movers_pairs:
            return
        try:
            movers_file = Path(__file__).resolve().parent.parent / "movers_pairlist.json"
            if not movers_file.exists():
                logger.warning(f"Movers file not found: {movers_file}")
                return
            data = json.loads(movers_file.read_text())
            pairs = data.get("exchange", {}).get("pair_whitelist", [])
            if pairs:
                self._movers_pairs = pairs
                meta = data.get("_meta", {})
                self._movers_gainers = meta.get("gainers", [])
                self._movers_losers = meta.get("losers", [])
                self._movers_last_update = now
                logger.info(f"Movers loaded from file: gainers={self._movers_gainers}, losers={self._movers_losers}")
        except Exception as e:
            logger.warning(f"Movers refresh failed: {e}")

    # -------------------------------------------------------------------------
    # Market regime detection — adaptive long/short slot allocation
    # -------------------------------------------------------------------------
    def _update_market_regime(self):
        """Evaluate BTC multi-TF to determine market regime and slot allocation."""
        now = time.time()
        if now - self._regime_last_update < self._regime_refresh_secs:
            return

        if not self.dp:
            return

        try:
            btc_1h = self.dp.get_pair_dataframe("BTC/USDT", "1h")
            btc_4h = self.dp.get_pair_dataframe("BTC/USDT", "4h")
            btc_1d = self.dp.get_pair_dataframe("BTC/USDT", "1d")

            if len(btc_1h) < 20 or len(btc_4h) < 20:
                return

            # Scoring: -100 (extreme bear) to +100 (extreme bull)
            regime_score = 0.0

            # 1h EMA trend (weight: 20)
            ema9_1h = pta.ema(btc_1h["close"], length=9)
            ema21_1h = pta.ema(btc_1h["close"], length=21)
            if len(ema9_1h) > 0 and len(ema21_1h) > 0:
                if ema9_1h.iloc[-1] > ema21_1h.iloc[-1]:
                    regime_score += 20
                else:
                    regime_score -= 20

            # 4h RSI (weight: 25)
            rsi_4h = pta.rsi(btc_4h["close"], length=14)
            if len(rsi_4h) > 0:
                r = rsi_4h.iloc[-1]
                if r > 60:
                    regime_score += 25
                elif r > 50:
                    regime_score += 10
                elif r < 40:
                    regime_score -= 25
                elif r < 50:
                    regime_score -= 10

            # 4h EMA 50/200 (weight: 25)
            ema50_4h = pta.ema(btc_4h["close"], length=50)
            ema200_4h = pta.ema(btc_4h["close"], length=200)
            if len(ema50_4h) > 0 and len(ema200_4h) > 0:
                e50 = ema50_4h.iloc[-1]
                e200 = ema200_4h.iloc[-1]
                if e50 and e200 and e200 > 0:
                    if e50 > e200:
                        regime_score += 25
                    else:
                        regime_score -= 25

            # 1d trend: close vs 20-day SMA (weight: 20)
            if len(btc_1d) >= 20:
                sma20_1d = btc_1d["close"].rolling(20).mean()
                if sma20_1d.iloc[-1] and btc_1d["close"].iloc[-1] > sma20_1d.iloc[-1]:
                    regime_score += 20
                else:
                    regime_score -= 20

            # 1h momentum: RSI_3 (weight: 10)
            rsi3_1h = pta.rsi(btc_1h["close"], length=3)
            if len(rsi3_1h) > 0:
                r3 = rsi3_1h.iloc[-1]
                if r3 > 70:
                    regime_score += 10
                elif r3 < 30:
                    regime_score -= 10

            # Determine regime and slot allocation
            # Score range: -100 to +100
            if regime_score >= 40:
                self._market_regime = "bullish"
                # Bull: mostly long, few shorts for hedging
                long_pct = min(0.8 + (regime_score - 40) / 300, 1.0)  # 80-100%
            elif regime_score <= -40:
                self._market_regime = "bearish"
                # Bear: mostly short, few longs for bounces
                long_pct = max(0.2 - (abs(regime_score) - 40) / 300, 0.0)  # 0-20%
            else:
                self._market_regime = "neutral"
                # Neutral: balanced, slight tilt based on score
                long_pct = 0.5 + regime_score / 200  # 30-70%

            self._max_long_slots = max(1, round(10 * long_pct))
            self._max_short_slots = max(1, 10 - self._max_long_slots)
            self._regime_last_update = now

            logger.info(
                f"Market regime: {self._market_regime} (score={regime_score:.0f}) "
                f"→ slots: {self._max_long_slots}L / {self._max_short_slots}S"
            )

        except Exception as e:
            logger.warning(f"Regime detection failed: {e}")

    def _count_open_trades_by_side(self) -> tuple[int, int]:
        """Count open long and short trades."""
        trades = Trade.get_trades_proxy(is_open=True)
        longs = sum(1 for t in trades if not t.is_short)
        shorts = sum(1 for t in trades if t.is_short)
        return longs, shorts

    # -------------------------------------------------------------------------
    # Informative pairs — BTC data + movers
    # -------------------------------------------------------------------------
    def informative_pairs(self):
        self._refresh_movers()
        pairs = []
        # Whitelist pairs need informative TF data for populate_indicators
        if self.dp:
            for pair in self.dp.current_whitelist():
                for tf in self.info_timeframes:
                    pairs.append((pair, tf))
        for tf in self.info_timeframes:
            pairs.append(("BTC/USDT", tf))
        # Add movers as informative so Freqtrade downloads their data
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
        btc_df["btc_EMA_200"] = pta.ema(btc_df["close"], length=200)
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
        df["EMA_200"] = pta.ema(df["close"], length=200)
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
        if len(df) < 20:
            return df
        tik = time.perf_counter()

        # --- BTC informative (all timeframes) ---
        btc_pair = "BTC/USDT"
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
        # EMA (full spectrum)
        df["EMA_3"] = pta.ema(df["close"], length=3)
        df["EMA_9"] = pta.ema(df["close"], length=9)
        df["EMA_12"] = pta.ema(df["close"], length=12)
        df["EMA_16"] = pta.ema(df["close"], length=16)
        df["EMA_20"] = pta.ema(df["close"], length=20)
        df["EMA_26"] = pta.ema(df["close"], length=26)
        df["EMA_50"] = pta.ema(df["close"], length=50)
        df["EMA_100"] = pta.ema(df["close"], length=100)
        df["EMA_200"] = pta.ema(df["close"], length=200)
        # SMA
        df["SMA_9"] = pta.sma(df["close"], length=9)
        df["SMA_16"] = pta.sma(df["close"], length=16)
        df["SMA_21"] = pta.sma(df["close"], length=21)
        df["SMA_30"] = pta.sma(df["close"], length=30)
        df["SMA_200"] = pta.sma(df["close"], length=200)
        # BB 20 - STD2 (handle pandas_ta naming: BBL_20_2.0 or BBL_20_2.0_2.0)
        bbands_20 = pta.bbands(df["close"], length=20)
        if isinstance(bbands_20, pd.DataFrame):
            for target, candidates in [
                ("BBL_20_2.0", ["BBL_20_2.0", "BBL_20_2.0_2.0"]),
                ("BBM_20_2.0", ["BBM_20_2.0", "BBM_20_2.0_2.0"]),
                ("BBU_20_2.0", ["BBU_20_2.0", "BBU_20_2.0_2.0"]),
                ("BBB_20_2.0", ["BBB_20_2.0", "BBB_20_2.0_2.0"]),
                ("BBP_20_2.0", ["BBP_20_2.0", "BBP_20_2.0_2.0"]),
            ]:
                for c in candidates:
                    if c in bbands_20.columns:
                        df[target] = bbands_20[c]
                        break
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
        df["volume_sma_20"] = pta.sma(df["volume"], length=20)
        df["num_empty_288"] = (df["volume"] <= 0).rolling(window=288, min_periods=288).sum()

        # --- NautilusTrader-inspired indicators ---
        # Volume Acceleration — slope of rolling volume (momentum lead indicator)
        vol_sma_fast = df["volume"].rolling(5).mean()
        vol_sma_slow = df["volume"].rolling(20).mean()
        df["vol_accel"] = np.where(vol_sma_slow > 0, (vol_sma_fast - vol_sma_slow) / vol_sma_slow, 0.0)

        # ATR (foundation for trailing stop, Keltner, vol sizing, vol-of-vol)
        df["ATR_14"] = pta.atr(df["high"], df["low"], df["close"], length=14)
        df["ATR_20"] = pta.atr(df["high"], df["low"], df["close"], length=20)

        # Vol-of-Vol — instability of volatility (breakout precursor, reuses ATR_14)
        atr_mean = df["ATR_14"].rolling(20).mean()
        atr_std = df["ATR_14"].rolling(20).std()
        df["vol_of_vol"] = np.where(atr_mean > 0, atr_std / atr_mean, 0.0)

        # Keltner Channel (ATR-based, period-matched to EMA_20)
        df["KC_upper"] = df["EMA_20"] + 2.0 * df["ATR_20"]
        df["KC_lower"] = df["EMA_20"] - 2.0 * df["ATR_20"]

        # BB/KC Squeeze — BB inside KC = low volatility, breakout imminent
        df["squeeze_on"] = (df["BBL_20_2.0"] > df["KC_lower"]) & (df["BBU_20_2.0"] < df["KC_upper"])

        # Fuzzy Candlesticks — candle pattern classification
        candle_length = df["high"] - df["low"]
        candle_body = (df["close"] - df["open"]).abs()
        candle_body_pct = candle_body / candle_length.replace(0, np.nan)
        mean_len = candle_length.rolling(10).mean()
        std_len = candle_length.rolling(10).std()
        df["candle_dir"] = np.where(df["close"] > df["open"], 1, -1)
        df["candle_size"] = np.where(candle_length > mean_len + std_len, 2,
                            np.where(candle_length > mean_len, 1, 0))
        df["candle_body_type"] = np.where(candle_body_pct > 0.7, 3,
                                 np.where(candle_body_pct > 0.3, 2,
                                 np.where(candle_body_pct > 0.1, 1, 0)))

        # Buy/Sell Pressure (14-period rolling)
        hl_range = (df["high"] - df["low"]).replace(0, np.nan)
        df["buy_pressure"] = (df["volume"] * (df["close"] - df["low"]) / hl_range).rolling(14).sum()
        df["sell_pressure"] = (df["volume"] * (df["high"] - df["close"]) / hl_range).rolling(14).sum()
        df["pressure_ratio"] = np.where(
            df["sell_pressure"] > 0, df["buy_pressure"] / df["sell_pressure"], 1.0
        )

        # VHF — Vertical Horizontal Filter (28-period)
        vhf_range = df["close"].rolling(28).max() - df["close"].rolling(28).min()
        vhf_denom = df["close"].diff().abs().rolling(28).sum()
        df["VHF_28"] = np.where(vhf_denom > 0, vhf_range / vhf_denom, 0.4)

        # Spread ratio — liquidity/slippage detection
        spread_raw = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
        spread_avg = spread_raw.rolling(20).mean()
        df["spread_ratio"] = np.where(spread_avg > 0, spread_raw / spread_avg, 1.0)

        # Donchian Channel — breakout detection
        df["DC_upper"] = df["high"].rolling(20).max()
        df["DC_lower"] = df["low"].rolling(20).min()
        df["DC_mid"] = (df["DC_upper"] + df["DC_lower"]) / 2
        df["DC_breakout_up"] = df["close"] > df["DC_upper"].shift(1)

        # --- Handle NaN for merged columns ---
        for col in ["RSI_14_1h", "RSI_14_4h", "RSI_14_1d"]:
            if col in df.columns:
                df[col] = df[col].astype(np.float64).replace(to_replace=[np.nan, None], value=50.0)

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
    # Global protections for SHORTS — block shorts during pumps
    # -------------------------------------------------------------------------
    def _calc_global_protections_short(self, df: DataFrame) -> pd.Series:
        """Inverted long protections: block shorts when market is pumping hard."""
        prot = pd.Series(True, index=df.index)

        # 5m & 15m & 1h pumping — don't short into a rally
        if "RSI_3_15m" in df.columns and "RSI_3_1h" in df.columns:
            prot &= (
                (df["RSI_3"] < 98.0)
                | (df["RSI_3_15m"] < 85.0)
                | (df["RSI_3_1h"] < 80.0)
            )
            prot &= (
                (df["RSI_3"] < 97.0)
                | (df["RSI_3_15m"] < 90.0)
                | (df.get("RSI_14_1h", 50.0) > 60.0)
            )

        # 5m & 1h pumping, 4h still low
        if "RSI_3_1h" in df.columns and "RSI_14_4h" in df.columns:
            prot &= (
                (df["RSI_3"] < 97.0)
                | (df["RSI_3_1h"] < 75.0)
                | (df["RSI_14_4h"] > 50.0)
            )

        # 15m & 1h & 4h pumping
        if "RSI_3_15m" in df.columns and "RSI_3_1h" in df.columns and "RSI_3_4h" in df.columns:
            prot &= (
                (df["RSI_3_15m"] < 95.0)
                | (df["RSI_3_1h"] < 90.0)
                | (df["RSI_3_4h"] < 85.0)
            )

        # 1h & 4h pumping, 4h strong uptrend
        if "RSI_3_1h" in df.columns and "RSI_3_4h" in df.columns and "ROC_9_4h" in df.columns:
            prot &= (
                (df["RSI_3_1h"] < 95.0)
                | (df["RSI_3_4h"] < 90.0)
                | (df["ROC_9_4h"] < 20.0)
            )

        # BTC pump protection — don't short during BTC rally
        if "btc_RSI_3_1h" in df.columns:
            prot &= (
                (df["btc_RSI_3_1h"] < 90.0)
                | (df.get("btc_RSI_14_4h", 50.0) > 70.0)
            )

        return prot

    # -------------------------------------------------------------------------
    # Short TA score — inverted logic (high score = good short opportunity)
    # -------------------------------------------------------------------------
    def _calculate_ta_score_short(self, df: DataFrame) -> pd.Series:
        score = pd.Series(50.0, index=df.index)

        # RSI_14: overbought = good short
        rsi = df["RSI_14"].fillna(50.0)
        score += np.where(rsi > 70, 15, np.where(rsi > 60, 8, np.where(rsi < 30, -15, np.where(rsi < 40, -8, 0))))

        # RSI_3: extreme high = short signal
        rsi3 = df["RSI_3"].fillna(50.0)
        score += np.where(rsi3 > 90, 10, np.where(rsi3 > 80, 5, np.where(rsi3 < 10, -10, np.where(rsi3 < 20, -5, 0))))

        # EMA crossover: bearish cross = short signal
        ema_bear = df["EMA_9"] < df["EMA_26"]
        ema_cross_down = ema_bear & (df["EMA_9"].shift(1) >= df["EMA_26"].shift(1))
        score += np.where(ema_cross_down, 10, np.where(ema_bear, 7, -7))

        # Bollinger: above upper = good short
        if "BBL_20_2.0" in df.columns and "BBU_20_2.0" in df.columns:
            score += np.where(df["close"] >= df["BBU_20_2.0"], 8, np.where(df["close"] <= df["BBL_20_2.0"], -8, 0))

        # Aroon: downtrend = short signal
        if "AROONU_14" in df.columns and "AROOND_14" in df.columns:
            aroonu = df["AROONU_14"].fillna(50)
            aroond = df["AROOND_14"].fillna(50)
            score += np.where((aroond > 80) & (aroonu < 30), 8, np.where((aroonu > 80) & (aroond < 30), -8, 0))

        # StochRSI: overbought = short signal
        if "STOCHRSIk_14_14_3_3" in df.columns:
            stoch = df["STOCHRSIk_14_14_3_3"].fillna(50)
            score += np.where(stoch > 80, 5, np.where(stoch < 20, -5, 0))

        # CMF: negative = bearish = short signal
        cmf = df["CMF_20"].fillna(0)
        score += np.where(cmf < -0.15, 5, np.where(cmf > 0.15, -5, 0))

        # Multi-TF RSI: overbought on higher TFs = good short
        if "RSI_14_1h" in df.columns and "RSI_14_4h" in df.columns:
            r1h = df["RSI_14_1h"].fillna(50)
            r4h = df["RSI_14_4h"].fillna(50)
            score += np.where((r1h > 70) & (r4h > 65), 5, np.where((r1h < 35) & (r4h < 40), -5, 0))

        # BTC correlation: BTC overbought = short opportunity
        if "btc_RSI_14_1h" in df.columns:
            btc_rsi = df["btc_RSI_14_1h"].fillna(50)
            score += np.where(btc_rsi > 70, 5, np.where(btc_rsi < 40, -3, 0))

        # Volume confirmation
        vol_ratio = np.where(df["volume_sma_20"] > 0, df["volume"] / df["volume_sma_20"], 1.0)
        score += np.where((vol_ratio > 1.5) & (score > 50), 3, np.where((vol_ratio > 1.5) & (score < 50), -3, 0))

        # Pressure ratio: sell pressure = short signal
        if "pressure_ratio" in df.columns:
            pr = df["pressure_ratio"].fillna(1.0)
            score += np.where(pr < 0.67, 4, np.where(pr < 0.83, 2,
                     np.where(pr > 1.5, -4, np.where(pr > self.pressure_confirm_ratio, -2, 0))))

        # VHF regime: trending down = good short
        if "VHF_28" in df.columns:
            vhf = df["VHF_28"].fillna(0.4)
            ema_bear_vhf = df["EMA_9"] < df["EMA_26"]
            score += np.where((vhf > self.vhf_trending_threshold) & ema_bear_vhf, 3,
                     np.where((vhf < self.vhf_ranging_threshold) & ~ema_bear_vhf, -3, 0))

        # Donchian breakdown
        if "DC_lower" in df.columns:
            dc_break_down = df["close"] < df["DC_lower"].shift(1)
            score += np.where(dc_break_down, 2, 0)

        return score.clip(0, 100)

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
        vol_ratio = np.where(df["volume_sma_20"] > 0, df["volume"] / df["volume_sma_20"], 1.0)
        score += np.where((vol_ratio > 1.5) & (score > 50), 3, np.where((vol_ratio > 1.5) & (score < 50), -3, 0))

        # --- NautilusTrader-inspired scoring components ---
        # Volume Acceleration (±3 — momentum lead indicator)
        if "vol_accel" in df.columns:
            va = df["vol_accel"].fillna(0.0)
            score += np.where(va > 0.5, 3, np.where(va < -0.3, -3, 0))

        # Squeeze boost (+5 base, +8 below midline, +10 triple-confirm with vol_of_vol)
        if "squeeze_on" in df.columns and "BBM_20_2.0" in df.columns:
            sq = df["squeeze_on"].fillna(False)
            below_mid = df["close"] < df["BBM_20_2.0"]
            vov_high = df["vol_of_vol"].fillna(0.0) > 0.5 if "vol_of_vol" in df.columns \
                       else pd.Series(False, index=df.index)
            score += np.where(sq & below_mid & vov_high, 10,
                     np.where(sq & below_mid, 8, np.where(sq, 5, 0)))

        # Pressure ratio (+4/+2/-2/-4)
        if "pressure_ratio" in df.columns:
            pr = df["pressure_ratio"].fillna(1.0)
            score += np.where(pr > 1.5, 4, np.where(pr > self.pressure_confirm_ratio, 2,
                     np.where(pr < 0.67, -4, np.where(pr < 0.83, -2, 0))))

        # VHF regime — modulate confidence (-3 to +3)
        if "VHF_28" in df.columns:
            vhf = df["VHF_28"].fillna(0.4)
            ema_bull_vhf = df["EMA_9"] > df["EMA_26"]
            score += np.where((vhf > self.vhf_trending_threshold) & ema_bull_vhf, 3,
                     np.where((vhf < self.vhf_ranging_threshold) & ~ema_bull_vhf, -3, 0))

        # Donchian breakout confirmation (+2 — trend-following signal)
        if "DC_breakout_up" in df.columns:
            score += np.where(df["DC_breakout_up"].fillna(False), 2, 0)

        return score.clip(0, 100)

    # -------------------------------------------------------------------------
    # Populate entry trend (vectorized — fast)
    # -------------------------------------------------------------------------
    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[:, "enter_long"] = 0
        df.loc[:, "enter_tag"] = ""

        # Update market regime (cached, every 5 min)
        self._update_market_regime()

        # Slot check: how many longs/shorts are open?
        open_longs, open_shorts = self._count_open_trades_by_side()
        long_slots_avail = self._max_long_slots - open_longs
        short_slots_avail = self._max_short_slots - open_shorts

        # Global protections
        prot = df.get("protections_long_global", pd.Series(True, index=df.index))
        empty_ok = df.get("num_empty_288", pd.Series(0, index=df.index)).fillna(0) <= 60

        # TA score for all rows — cached in df for reuse by adjust_trade_position
        ta_score = self._calculate_ta_score_vectorized(df)
        df["ta_score"] = ta_score

        # === LONG entries (only if long slots available) ===
        if long_slots_avail > 0:
            # Strong TA signal — entry without Claude
            strong = prot & empty_ok & (ta_score >= 75)
            df.loc[strong, "enter_long"] = 1
            df.loc[strong, "enter_tag"] = "strong_ta"

            # Ambiguous zone — Claude sentiment on LAST candle only (live efficiency)
            if len(df) > 0 and not df.iloc[-1].get("enter_long", 0):
                last_score = ta_score.iloc[-1]
                last_prot = prot.iloc[-1] if hasattr(prot, 'iloc') else True
                last_empty = empty_ok.iloc[-1] if hasattr(empty_ok, 'iloc') else True

                if last_prot and last_empty and 40 <= last_score <= 70:
                    pair = metadata.get("pair", "XRP/USDT")
                    token = pair.split("/")[0]
                    price = df.iloc[-1]["close"]

                    headlines = self.claude.fetch_news(token)
                    sentiment = self.claude.analyze_sentiment(token, price, last_score, headlines)
                    final_score = last_score + sentiment

                    if final_score >= self.sentiment_threshold_buy:
                        df.iloc[-1, df.columns.get_loc("enter_long")] = 1
                        df.iloc[-1, df.columns.get_loc("enter_tag")] = f"claude_s{sentiment:.0f}"

            # --- Mover long entries (last candle only) ---
            if len(df) > 0 and not df.iloc[-1].get("enter_long", 0):
                pair = metadata.get("pair", "")
                self._refresh_movers()
                last = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else last

                if pair in self._movers_gainers:
                    if self._count_open_mover_trades(self.mover_tags_gainer) < self.mover_max_slots_gainer:
                        if self._check_gainer_entry(last, prev):
                            df.iloc[-1, df.columns.get_loc("enter_long")] = 1
                            df.iloc[-1, df.columns.get_loc("enter_tag")] = "mover_gainer"
                            logger.info(f"Mover gainer entry signal: {pair}")

                if pair in self._movers_losers and not df.iloc[-1].get("enter_long", 0):
                    if self._count_open_mover_trades(self.mover_tags_loser) < self.mover_max_slots_loser:
                        if self._check_loser_entry(last, prev):
                            df.iloc[-1, df.columns.get_loc("enter_long")] = 1
                            df.iloc[-1, df.columns.get_loc("enter_tag")] = "mover_loser"
                            logger.info(f"Mover loser entry signal: {pair}")

        # =================================================================
        # SHORT entries (futures only — higher threshold, slot-limited)
        # =================================================================
        if self.can_short and short_slots_avail > 0:
            df.loc[:, "enter_short"] = 0

            prot_short = df.get("protections_short_global", pd.Series(True, index=df.index))
            ta_score_short = self._calculate_ta_score_short(df)
            df["ta_score_short"] = ta_score_short

            # Strong bearish TA signal (higher threshold than longs)
            strong_short = prot_short & empty_ok & (ta_score_short >= self.short_entry_threshold)
            df.loc[strong_short, "enter_short"] = 1
            df.loc[strong_short, "enter_tag"] = "strong_ta_short"

            # Ambiguous zone — Claude sentiment (last candle, short bias)
            if len(df) > 0 and not df.iloc[-1].get("enter_short", 0) and not df.iloc[-1].get("enter_long", 0):
                last_score_s = ta_score_short.iloc[-1]
                last_prot_s = prot_short.iloc[-1] if hasattr(prot_short, 'iloc') else True
                last_empty_s = empty_ok.iloc[-1] if hasattr(empty_ok, 'iloc') else True

                if last_prot_s and last_empty_s and 40 <= last_score_s <= 70:
                    pair = metadata.get("pair", "XRP/USDT")
                    token = pair.split("/")[0]
                    price = df.iloc[-1]["close"]

                    headlines = self.claude.fetch_news(token)
                    sentiment = self.claude.analyze_sentiment(token, price, last_score_s, headlines)
                    final_score_s = last_score_s - sentiment

                    if final_score_s >= self.sentiment_threshold_buy:
                        df.iloc[-1, df.columns.get_loc("enter_short")] = 1
                        df.iloc[-1, df.columns.get_loc("enter_tag")] = f"claude_short_s{sentiment:.0f}"

            # Mover short entries (last candle only)
            if len(df) > 0 and not df.iloc[-1].get("enter_short", 0) and not df.iloc[-1].get("enter_long", 0):
                pair = metadata.get("pair", "")
                self._refresh_movers()
                last = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else last

                # Gainer short: short the blow-off top
                if pair in self._movers_gainers:
                    if self._count_open_mover_trades(["mover_gainer_short"]) < self.mover_max_slots_gainer:
                        if self._check_gainer_short_entry(last, prev):
                            df.iloc[-1, df.columns.get_loc("enter_short")] = 1
                            df.iloc[-1, df.columns.get_loc("enter_tag")] = "mover_gainer_short"
                            logger.info(f"Mover gainer SHORT signal: {pair}")

                # Loser short: ride the loser down
                if pair in self._movers_losers and not df.iloc[-1].get("enter_short", 0):
                    if self._count_open_mover_trades(["mover_loser_short"]) < self.mover_max_slots_loser:
                        if self._check_loser_short_entry(last, prev):
                            df.iloc[-1, df.columns.get_loc("enter_short")] = 1
                            df.iloc[-1, df.columns.get_loc("enter_tag")] = "mover_loser_short"
                            logger.info(f"Mover loser SHORT signal: {pair}")

        return df

    # -------------------------------------------------------------------------
    # Gainer entry: buy the dip in a strong uptrend (NFI quick_mode pattern)
    # -------------------------------------------------------------------------
    def _check_gainer_entry(self, last, prev) -> bool:
        rsi3 = last.get("RSI_3", 50)
        rsi14 = last.get("RSI_14", 50)
        rsi14_1h = last.get("RSI_14_1h", 50)
        close = last.get("close", 0)
        ema20 = last.get("EMA_20", 0)
        bbl = last.get("BBL_20_2.0", 0)

        # Pullback on 5m (RSI_3 dip) while higher TFs still bullish
        pullback = rsi3 < 35
        # 1h trend still intact
        trend_ok = rsi14_1h > 50
        # Not already overbought on 5m
        not_ob = rsi14 < 65
        # Price above EMA20 (uptrend structure)
        above_ema = close > ema20 if ema20 else False
        # Volume confirmation
        vol_ok = last.get("volume", 0) > last.get("volume_sma_20", 0) * 0.8

        # NFI-style multi-condition AND
        return pullback and trend_ok and not_ob and above_ema and vol_ok

    # -------------------------------------------------------------------------
    # Loser entry: mean-reversion at oversold extremes (NFI grind_mode pattern)
    # -------------------------------------------------------------------------
    def _check_loser_entry(self, last, prev) -> bool:
        rsi3 = last.get("RSI_3", 50)
        rsi14 = last.get("RSI_14", 50)
        rsi3_prev = prev.get("RSI_3", 50)
        close = last.get("close", 0)
        bbl = last.get("BBL_20_2.0", 0)
        cmf = last.get("CMF_20", 0)
        stochrsi = last.get("STOCHRSIk_14_14_3_3", 50)
        willr = last.get("WILLR_14", -50)

        # Deep oversold on RSI_14
        oversold = rsi14 < 30
        # RSI_3 turning up (bounce starting)
        rsi3_turning = rsi3 > rsi3_prev and rsi3 < 40
        # Near or below Bollinger lower band
        near_bbl = close < bbl * 1.01 if bbl else False
        # StochRSI in oversold zone
        stoch_os = stochrsi < 20
        # Williams %R extreme
        willr_os = willr < -85
        # CMF not deeply negative (some buying pressure returning)
        cmf_ok = cmf > -0.25

        # Need at least: oversold + bounce signal + 2 confirmations
        confirmations = sum([near_bbl, stoch_os, willr_os, cmf_ok])
        return oversold and rsi3_turning and confirmations >= 2

    # -------------------------------------------------------------------------
    # Gainer SHORT entry: short the blow-off top (RSI extreme + rejection)
    # -------------------------------------------------------------------------
    def _check_gainer_short_entry(self, last, prev) -> bool:
        rsi3 = last.get("RSI_3", 50)
        rsi14 = last.get("RSI_14", 50)
        rsi14_1h = last.get("RSI_14_1h", 50)
        close = last.get("close", 0)
        bbu = last.get("BBU_20_2.0", 0)

        # Extreme overbought on 5m
        overbought = rsi3 > 90
        # RSI_14 also elevated
        rsi_high = rsi14 > 70
        # Price at or above upper BB
        at_bbu = close > bbu if bbu else False
        # 1h not deeply oversold (confirming uptrend exhaustion, not a trend)
        htf_ok = rsi14_1h > 60
        # Volume spike (blow-off)
        vol_ok = last.get("volume", 0) > last.get("volume_sma_20", 0) * 1.5

        return overbought and rsi_high and at_bbu and htf_ok and vol_ok

    # -------------------------------------------------------------------------
    # Loser SHORT entry: ride the breakdown (trend continuation short)
    # -------------------------------------------------------------------------
    def _check_loser_short_entry(self, last, prev) -> bool:
        rsi3 = last.get("RSI_3", 50)
        rsi14 = last.get("RSI_14", 50)
        rsi3_prev = prev.get("RSI_3", 50)
        close = last.get("close", 0)
        bbl = last.get("BBL_20_2.0", 0)
        cmf = last.get("CMF_20", 0)
        stochrsi = last.get("STOCHRSIk_14_14_3_3", 50)

        # Bearish momentum: RSI_14 mid-range heading down (not oversold yet)
        bearish = 30 < rsi14 < 55
        # RSI_3 bounced slightly then rolling over again
        rollover = rsi3 < rsi3_prev and rsi3 < 40
        # Price breaking below BB lower
        below_bbl = close < bbl if bbl else False
        # StochRSI confirming weakness
        stoch_weak = stochrsi < 30
        # CMF negative (selling pressure)
        cmf_neg = cmf < -0.10

        confirmations = sum([below_bbl, stoch_weak, cmf_neg])
        return bearish and rollover and confirmations >= 2

    # -------------------------------------------------------------------------
    # Populate exit trend (basic — main exits via custom_exit)
    # -------------------------------------------------------------------------
    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[:, "exit_long"] = 0
        df.loc[:, "exit_short"] = 0
        return df

    # -------------------------------------------------------------------------
    # Custom stake — reduced size for mover trades (NFI rebuy_mode pattern)
    # -------------------------------------------------------------------------
    def custom_stake_amount(
        self, pair: str, current_time: datetime, current_rate: float,
        proposed_stake: float, min_stake: float | None, max_stake: float,
        leverage: float, entry_tag: str | None, side: str, **kwargs
    ) -> float:
        # Volatility-adjusted sizing (NautilusTrader pattern)
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(df) > 0:
            atr = df.iloc[-1].get("ATR_14", 0)
            if atr and current_rate > 0:
                vol_ratio = atr / current_rate
                if vol_ratio > 0.03:
                    proposed_stake *= 0.5
                    logger.info(f"High vol stake reduction for {pair}: vol_ratio={vol_ratio:.4f}")
                elif vol_ratio > 0.02:
                    proposed_stake *= 0.75

        # Mover stake reduction (long + short movers)
        all_mover_tags = self.mover_tags_gainer + self.mover_tags_loser + [
            "mover_gainer_short", "mover_loser_short"
        ]
        if entry_tag and entry_tag in all_mover_tags:
            reduced = proposed_stake * self.mover_stake_multiplier
            if min_stake and reduced < min_stake:
                return min_stake
            logger.info(f"Mover stake for {pair}: {reduced:.2f} ({self.mover_stake_multiplier}x)")
            return reduced

        # Short trades get reduced stake (crypto long bias)
        if side == "short":
            proposed_stake *= self.short_stake_multiplier
            logger.info(f"Short stake for {pair}: {proposed_stake:.2f} ({self.short_stake_multiplier}x)")

        return proposed_stake

    # -------------------------------------------------------------------------
    # Confirm entry — orderbook imbalance + liquidity + candle filter
    # -------------------------------------------------------------------------
    def confirm_trade_entry(
        self, pair: str, order_type: str, amount: float, rate: float,
        time_in_force: str, current_time: datetime, entry_tag: str,
        side: str, **kwargs
    ) -> bool:
        # Orderbook imbalance check (only in live/dry_run)
        if self.dp.runmode.value in ("live", "dry_run"):
            try:
                ob = self.dp.orderbook(pair, 5)
                bid_vol = sum(b[1] for b in ob.get("bids", [])[:5])
                ask_vol = sum(a[1] for a in ob.get("asks", [])[:5])
                if side == "long" and ask_vol > bid_vol * self.ob_imbalance_block_ratio:
                    logger.info(f"Long blocked for {pair}: sell wall "
                                f"(bid={bid_vol:.1f}, ask={ask_vol:.1f})")
                    return False
                if side == "short" and bid_vol > ask_vol * self.ob_imbalance_block_ratio:
                    logger.info(f"Short blocked for {pair}: buy wall "
                                f"(bid={bid_vol:.1f}, ask={ask_vol:.1f})")
                    return False
            except Exception:
                pass  # Orderbook unavailable — allow entry

        # Indicator-based filters
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(df) < 1:
            return True
        last = df.iloc[-1]

        # Block entry on illiquid candles
        if last.get("spread_ratio", 1.0) > 2.0:
            logger.info(f"Entry blocked for {pair}: illiquid (spread_ratio={last['spread_ratio']:.2f})")
            return False

        # Block long on large bearish candles / block short on large bullish candles
        if side == "long" and last.get("candle_dir", 1) == -1 and last.get("candle_size", 0) == 2:
            logger.info(f"Long blocked for {pair}: large bearish candle")
            return False
        if side == "short" and last.get("candle_dir", 1) == 1 and last.get("candle_size", 0) == 2:
            logger.info(f"Short blocked for {pair}: large bullish candle")
            return False

        return True

    # -------------------------------------------------------------------------
    # Count open mover trades by tag
    # -------------------------------------------------------------------------
    def _count_open_mover_trades(self, tags: list[str]) -> int:
        trades = Trade.get_trades_proxy(is_open=True)
        return sum(1 for t in trades if t.enter_tag in tags)

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

        # --- Stale trade exit (funding rate protection) ---
        if current_profit < 0 and trade.open_date_utc:
            trade_age_hours = (current_time - trade.open_date_utc).total_seconds() / 3600
            lev = trade.leverage or 1.0
            timeout = self.stale_trade_hours_leveraged if lev > 3 else self.stale_trade_hours
            # Shorts get tighter timeout (funding bleed in bull markets)
            if trade.is_short:
                timeout *= 0.75
            if trade_age_hours > timeout:
                return f"exit_stale_{trade_age_hours:.0f}h"

        # --- Mover-specific exits (tag-based routing, NFI pattern) ---
        enter_tag = trade.enter_tag or ""

        if enter_tag == "mover_gainer":
            return self._exit_mover_gainer(last, prev, current_profit)

        if enter_tag == "mover_loser":
            return self._exit_mover_loser(last, prev, current_profit)

        # =================================================================
        # SHORT exits (inverted logic)
        # =================================================================
        if trade.is_short:
            return self._custom_exit_short(last, prev, trade, current_rate, current_profit, filled_entries)

        # =================================================================
        # LONG exits (original logic)
        # =================================================================

        # --- Exit Signal 1: Extreme overbought (NFI pattern) ---
        rsi14 = last.get("RSI_14", 50)
        bbu = last.get("BBU_20_2.0", 0)
        if rsi14 > 84 and last["close"] > bbu and prev["close"] > prev.get("BBU_20_2.0", 0):
            if current_profit > 0.01:
                return "exit_overbought_bb_rsi"

        # --- Exit Signal 2: RSI > 88 ---
        if rsi14 > 88 and current_profit > 0.01:
            return "exit_extreme_rsi"

        # --- Exit Signal 3: RSI_14 + RSI_14_1h both overbought ---
        rsi14_1h = last.get("RSI_14_1h", 50)
        if rsi14 > 80 and rsi14_1h > 75 and current_profit > 0.01:
            return "exit_multi_tf_overbought"

        # --- Exit Signal 4: Price above 1h BB upper * 1.10 ---
        bbu_1h = last.get("BBU_20_2.0_1h", 0)
        if bbu_1h and last["close"] > bbu_1h * 1.10 and current_profit > 0.01:
            return "exit_1h_bb_stretch"

        # --- Profit-tiered RSI exit (NFI long_exit_main pattern) ---
        if current_profit > 0.0:
            above_ema200 = last["close"] > last.get("EMA_200", 0)
            rsi_threshold = self._get_exit_rsi_threshold(current_profit, above_ema200)
            if rsi14 < rsi_threshold:
                return f"exit_profit_rsi_{current_profit:.1%}"

        # --- Williams %R exit ---
        willr = last.get("WILLR_14", -50)
        if willr is not None and willr > -5 and current_profit > 0.02:
            return "exit_willr_overbought"

        # --- Leverage-aware stoploss thresholds ---
        lev = trade.leverage or 1.0
        # Scale SL tighter with leverage: -20% at 1x → -10% at 2x → -4% at 5x
        doom_sl = self.stop_threshold_doom / lev
        normal_sl = self.stop_threshold_normal / lev

        # --- Doom stoploss ---
        if current_profit < -doom_sl:
            return "exit_stoploss_doom"

        # --- Normal stoploss with conditions (NFI stoploss_u_e pattern) ---
        if (
            current_profit < -normal_sl
            and last["close"] < last.get("EMA_200", float("inf"))
            and last.get("CMF_20", 0) < 0
            and rsi14 > prev.get("RSI_14", 50)
            and rsi14 > (rsi14_1h + 20)
        ):
            return "exit_stoploss_conditional"

        return None

    # -------------------------------------------------------------------------
    # Short exit logic — mirrored from long exits
    # -------------------------------------------------------------------------
    def _custom_exit_short(self, last, prev, trade, current_rate, current_profit, filled_entries):
        enter_tag = trade.enter_tag or ""

        # Mover short exits
        if enter_tag == "mover_gainer_short":
            return self._exit_mover_gainer_short(last, prev, current_profit)
        if enter_tag == "mover_loser_short":
            return self._exit_mover_loser_short(last, prev, current_profit)

        rsi14 = last.get("RSI_14", 50)
        rsi14_1h = last.get("RSI_14_1h", 50)

        # --- Short Exit 1: Extreme oversold (cover short) ---
        bbl = last.get("BBL_20_2.0", 0)
        if rsi14 < 16 and bbl and last["close"] < bbl and prev["close"] < prev.get("BBL_20_2.0", 0):
            if current_profit > 0.01:
                return "exit_short_oversold_bb_rsi"

        # --- Short Exit 2: RSI < 12 ---
        if rsi14 < 12 and current_profit > 0.01:
            return "exit_short_extreme_rsi"

        # --- Short Exit 3: RSI_14 + RSI_14_1h both oversold ---
        if rsi14 < 20 and rsi14_1h < 25 and current_profit > 0.01:
            return "exit_short_multi_tf_oversold"

        # --- Short Exit 4: Price below 1h BB lower * 0.90 ---
        bbl_1h = last.get("BBL_20_2.0_1h", 0)
        if bbl_1h and last["close"] < bbl_1h * 0.90 and current_profit > 0.01:
            return "exit_short_1h_bb_stretch"

        # --- Profit-tiered RSI exit for shorts (high RSI = cover) ---
        if current_profit > 0.0:
            below_ema200 = last["close"] < last.get("EMA_200", float("inf"))
            rsi_threshold = self._get_exit_rsi_threshold_short(current_profit, below_ema200)
            if rsi14 > rsi_threshold:
                return f"exit_short_profit_rsi_{current_profit:.1%}"

        # --- Williams %R exit for shorts ---
        willr = last.get("WILLR_14", -50)
        if willr is not None and willr < -95 and current_profit > 0.02:
            return "exit_short_willr_oversold"

        # --- Leverage-aware stoploss thresholds ---
        lev = trade.leverage or 1.0
        doom_sl = self.stop_threshold_doom / lev
        normal_sl = self.stop_threshold_normal / lev

        # --- Doom stoploss ---
        if current_profit < -doom_sl:
            return "exit_short_stoploss_doom"

        # --- Normal stoploss with conditions ---
        if (
            current_profit < -normal_sl
            and last["close"] > last.get("EMA_200", 0)
            and last.get("CMF_20", 0) > 0
            and rsi14 < prev.get("RSI_14", 50)
            and rsi14 < (rsi14_1h - 20)
        ):
            return "exit_short_stoploss_conditional"

        return None

    # -------------------------------------------------------------------------
    # Profit-tiered RSI threshold for SHORTS (inverted)
    # -------------------------------------------------------------------------
    def _get_exit_rsi_threshold_short(self, profit: float, below_ema200: bool) -> float:
        """Higher profit → more willing to cover on RSI bounce."""
        offset = 0 if below_ema200 else -2
        if profit < 0.01:
            return 90.0 + offset
        elif profit < 0.02:
            return 72.0 + offset
        elif profit < 0.03:
            return 70.0 + offset
        elif profit < 0.04:
            return 68.0 + offset
        elif profit < 0.05:
            return 66.0 + offset
        elif profit < 0.06:
            return 64.0 + offset
        elif profit < 0.08:
            return 62.0 + offset
        elif profit < 0.10:
            return 58.0 + offset
        elif profit < 0.12:
            return 54.0 + offset
        elif profit < 0.20:
            return 56.0 + offset
        else:
            return 58.0 + offset

    # -------------------------------------------------------------------------
    # Mover gainer SHORT exit — cover when selling exhaustion
    # -------------------------------------------------------------------------
    def _exit_mover_gainer_short(self, last, prev, current_profit: float):
        rsi14 = last.get("RSI_14", 50)
        rsi3 = last.get("RSI_3", 50)

        # Hard stoploss
        if current_profit < -self.mover_gainer_sl:
            return "exit_mover_gainer_short_sl"

        # Oversold bounce — cover
        if current_profit > self.mover_gainer_tp_min:
            if rsi14 > 40 and prev.get("RSI_14", 50) < 35:
                return "exit_mover_gainer_short_bounce"
            if rsi14 < 20:
                return "exit_mover_gainer_short_oversold"
            if rsi3 > 80:
                return "exit_mover_gainer_short_rsi3_spike"

        if current_profit > 0.05 and rsi3 > 80:
            return "exit_mover_gainer_short_trail_5pct"

        return None

    # -------------------------------------------------------------------------
    # Mover loser SHORT exit — cover when momentum stalls
    # -------------------------------------------------------------------------
    def _exit_mover_loser_short(self, last, prev, current_profit: float):
        rsi14 = last.get("RSI_14", 50)
        rsi3 = last.get("RSI_3", 50)

        # Hard stoploss
        if current_profit < -self.mover_loser_sl:
            return "exit_mover_loser_short_sl"

        # Quick TP on any bounce
        if current_profit > self.mover_loser_tp_min:
            if rsi3 > 70:
                return "exit_mover_loser_short_bounce"
            if rsi14 < 20:
                return "exit_mover_loser_short_extreme_os"

        if current_profit > 0.03 and rsi3 > 60:
            return "exit_mover_loser_short_trail"

        return None

    # -------------------------------------------------------------------------
    # Custom stoploss — ATR-based dynamic trailing (NautilusTrader pattern)
    # -------------------------------------------------------------------------
    def custom_stoploss(
        self, pair: str, trade: Trade, current_time: datetime,
        current_rate: float, current_profit: float, after_fill: bool,
        **kwargs
    ) -> float:
        # Below 1% profit — no trailing, let custom_exit handle doom/conditional SL
        if current_profit < 0.01:
            return -0.99

        # ATR trailing stop with smooth ramp
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(df) < 1:
            return -0.99

        atr = df.iloc[-1].get("ATR_14", 0)
        if not atr or atr <= 0 or current_rate <= 0:
            return -0.99

        # Smooth ramp: wide trail at low profit, tightens as profit grows
        #   1-2% profit → ATR × 5 (loose, give room to breathe)
        #   2-5% profit → ATR × 3 (standard)
        #   5%+ profit  → ATR × 2 (tight, protect gains)
        if current_profit < 0.02:
            mult = 5.0
        elif current_profit < 0.05:
            mult = self.atr_trailing_multiplier  # 3.0
        else:
            mult = 2.0

        trailing_dist = (atr * mult) / current_rate
        # Cap at -0.20 max width. No tight-side clamp — Freqtrade auto-ratchets
        # (only tightens), so a floor would permanently lock after one
        # low-ATR candle even if volatility expands later.
        return max(-trailing_dist, -0.20)

    # -------------------------------------------------------------------------
    # Dynamic leverage — volatility-adjusted (NautilusTrader pattern)
    # -------------------------------------------------------------------------
    def leverage(
        self, pair: str, current_time: datetime, current_rate: float,
        proposed_leverage: float, max_leverage: float, entry_tag: str | None,
        side: str, **kwargs
    ) -> float:
        # Hard cap for mover trades — high risk, low leverage
        all_mover_tags = self.mover_tags_gainer + self.mover_tags_loser + [
            "mover_gainer_short", "mover_loser_short"
        ]
        if entry_tag and entry_tag in all_mover_tags:
            lev = min(self.futures_leverage_mover_cap, max_leverage)
            logger.info(f"Mover leverage for {pair}: {lev}x (capped)")
            return float(lev)

        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(df) < 1:
            return float(self.futures_leverage_default)

        atr = df.iloc[-1].get("ATR_14", 0)
        if not atr or current_rate <= 0:
            return float(self.futures_leverage_default)

        atr_ratio = atr / current_rate
        if atr_ratio > 0.03:
            lev = self.futures_leverage_high_vol   # 2x — high volatility
        elif atr_ratio < 0.015:
            lev = self.futures_leverage_low_vol    # 5x — low volatility
        else:
            lev = self.futures_leverage_default    # 3x — normal

        lev = min(lev, max_leverage)
        logger.info(f"Leverage for {pair}: {lev}x (atr_ratio={atr_ratio:.4f})")
        return float(lev)

    # -------------------------------------------------------------------------
    # TWAP-style DCA — scale into positions over time (NautilusTrader pattern)
    # -------------------------------------------------------------------------
    def adjust_trade_position(
        self, trade: Trade, current_time: datetime, current_rate: float,
        current_profit: float, min_stake: float | None, max_stake: float,
        current_entry_rate: float, current_exit_rate: float,
        current_entry_profit: float, current_exit_profit: float, **kwargs
    ) -> float | None:
        # Skip for mover trades — short-lived scalps
        enter_tag = trade.enter_tag or ""
        mover_tags_all = self.mover_tags_gainer + self.mover_tags_loser + [
            "mover_gainer_short", "mover_loser_short"
        ]
        if enter_tag in mover_tags_all:
            return None

        # Max 3 entries per trade
        if trade.nr_of_successful_entries >= 3:
            return None

        # Min 30 min between fills
        filled = trade.select_filled_orders(trade.entry_side)
        if filled:
            last_fill = filled[-1].order_filled_date
            if last_fill and (current_time - last_fill).total_seconds() < 1800:
                return None

        # Only DCA if dipping -2% or more
        if current_profit > -0.02:
            return None

        # Check cached TA score — only DCA if still favorable
        df, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
        if len(df) > 0:
            if trade.is_short:
                score = df.iloc[-1].get("ta_score_short", 0)
            else:
                score = df.iloc[-1].get("ta_score", 0)
            if score < 45:
                return None  # Don't DCA into a weak setup

        logger.info(f"DCA fill #{trade.nr_of_successful_entries + 1} for {trade.pair} "
                    f"({'short' if trade.is_short else 'long'}) at {current_profit:.2%}")
        return min_stake

    # -------------------------------------------------------------------------
    # Profit-tiered RSI threshold (from NFI long_exit_main)
    # -------------------------------------------------------------------------
    def _get_exit_rsi_threshold(self, profit: float, above_ema200: bool) -> float:
        """
        Higher profit → more willing to exit on RSI dip.
        Below EMA200 → stricter (lower RSI required to exit).
        """
        offset = 0 if above_ema200 else 2
        if profit < 0.01:
            return 10.0 + offset
        elif profit < 0.02:
            return 28.0 + offset
        elif profit < 0.03:
            return 30.0 + offset
        elif profit < 0.04:
            return 32.0 + offset
        elif profit < 0.05:
            return 34.0 + offset
        elif profit < 0.06:
            return 36.0 + offset
        elif profit < 0.08:
            return 38.0 + offset
        elif profit < 0.10:
            return 42.0 + offset
        elif profit < 0.12:
            return 46.0 + offset
        elif profit < 0.20:
            return 44.0 + offset
        else:
            return 42.0 + offset

    # -------------------------------------------------------------------------
    # Mover gainer exit — ride momentum, exit on exhaustion
    # -------------------------------------------------------------------------
    def _exit_mover_gainer(self, last, prev, current_profit: float):
        rsi14 = last.get("RSI_14", 50)
        rsi3 = last.get("RSI_3", 50)
        rsi14_1h = last.get("RSI_14_1h", 50)

        # Hard stoploss
        if current_profit < -self.mover_gainer_sl:
            return "exit_mover_gainer_sl"

        # Momentum exhaustion: RSI was high, now dropping while in profit
        if current_profit > self.mover_gainer_tp_min:
            # RSI_14 declining from overbought
            if rsi14 < 60 and prev.get("RSI_14", 50) > 65:
                return "exit_mover_gainer_momentum_fade"
            # Extreme overbought — take profit
            if rsi14 > 80:
                return "exit_mover_gainer_overbought"
            # Multi-TF overbought
            if rsi14 > 70 and rsi14_1h > 70:
                return "exit_mover_gainer_multi_tf_ob"

        # Bigger profit — wider trailing via RSI
        if current_profit > 0.05:
            if rsi3 < 20:
                return "exit_mover_gainer_trail_5pct"
        if current_profit > 0.10:
            if rsi14 < 50:
                return "exit_mover_gainer_trail_10pct"

        return None

    # -------------------------------------------------------------------------
    # Mover loser exit — quick scalp on bounce, tight stoploss
    # -------------------------------------------------------------------------
    def _exit_mover_loser(self, last, prev, current_profit: float):
        rsi14 = last.get("RSI_14", 50)
        rsi3 = last.get("RSI_3", 50)

        # Hard stoploss — tight, losers can keep losing
        if current_profit < -self.mover_loser_sl:
            return "exit_mover_loser_sl"

        # Quick profit-take on bounce — don't get greedy with losers
        if current_profit > self.mover_loser_tp_min:
            # RSI recovering from oversold to neutral — take the bounce
            if rsi14 > 45:
                return "exit_mover_loser_bounce"
            # RSI_3 spike — short-term momentum exhaustion
            if rsi3 > 80:
                return "exit_mover_loser_rsi3_spike"

        # Moderate profit — secure it
        if current_profit > 0.02:
            if rsi14 > 55:
                return "exit_mover_loser_secure"

        # Larger bounce — wider target
        if current_profit > 0.05:
            if rsi3 < 30:
                return "exit_mover_loser_trail_5pct"

        return None
