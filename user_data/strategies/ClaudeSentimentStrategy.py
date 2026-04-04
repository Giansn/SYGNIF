"""
ClaudeSentimentStrategy v2 - NFI-Enhanced with Claude API Sentiment Layer

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
from datetime import datetime, timedelta
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
# Claude Sentiment Layer
# ---------------------------------------------------------------------------

class ClaudeSentiment:
    """Lightweight Claude API wrapper for crypto sentiment analysis."""

    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = "claude-haiku-4-5-20251001"
        self.base_url = "https://api.anthropic.com/v1/messages"
        self._cache: dict[str, tuple[float, float]] = {}
        self.cache_ttl = 900  # 15 min cache (shorter for 5m TF)
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

    def fetch_news(self, token: str, max_items: int = 5) -> list[str]:
        """Fetch recent crypto news from free RSS feeds."""
        feeds = [
            f"https://cryptopanic.com/news/{token.lower()}/rss/",
            "https://cointelegraph.com/rss",
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
        ]
        headlines = []
        for feed_url in feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:3]:
                    title = entry.get("title", "")
                    if token.upper() in title.upper() or len(headlines) < 2:
                        headlines.append(title)
            except Exception as e:
                logger.warning(f"Feed error {feed_url}: {e}")

        try:
            gdelt_url = (
                f"https://api.gdeltproject.org/api/v2/doc/doc"
                f"?query={token}%20crypto&mode=artlist&maxrecords=5&format=json"
            )
            resp = requests.get(gdelt_url, timeout=5)
            if resp.ok:
                data = resp.json()
                for art in data.get("articles", [])[:3]:
                    headlines.append(art.get("title", ""))
        except Exception:
            pass

        return headlines[:max_items]

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

class ClaudeSentimentStrategy(IStrategy):
    """
    NFI-Enhanced Freqtrade strategy with Claude API sentiment analysis.

    Based on NostalgiaForInfinityX7 patterns:
    - Multi-timeframe indicators (5m + 15m/1h/4h/1d)
    - BTC correlation
    - Global protections cascade
    - Profit-tiered RSI exits
    - Claude sentiment for ambiguous signals
    """

    INTERFACE_VERSION = 3

    # --- Core settings (NFI-style) ---
    stoploss = -0.99  # Disabled — managed internally
    trailing_stop = False
    use_custom_stoploss = False

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
    _movers_last_update: float = 0.0
    _movers_refresh_secs: int = 14400  # 4h

    # Claude layer
    claude = ClaudeSentiment()

    # -------------------------------------------------------------------------
    # Fetch top gainers/losers from Bybit API (refreshed every 4h)
    # -------------------------------------------------------------------------
    def _refresh_movers(self):
        now = time.time()
        if now - self._movers_last_update < self._movers_refresh_secs and self._movers_pairs:
            return
        try:
            resp = requests.get(
                "https://api.bybit.com/v5/market/tickers?category=spot", timeout=10
            )
            if not resp.ok:
                return
            tickers = resp.json().get("result", {}).get("list", [])

            exclude_bases = {
                "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD", "USDT",
                "USDP", "USDS", "XUSD", "USD1", "RLUSD", "AUSD", "EURI",
                "XAUT", "PAXG",
            }
            pairs_data = []
            for t in tickers:
                sym = t.get("symbol", "")
                if not sym.endswith("USDT"):
                    continue
                base = sym.replace("USDT", "")
                if base in exclude_bases or any(p in base for p in ("2L", "3L", "5L", "2S", "3S", "5S")):
                    continue
                try:
                    change = float(t.get("price24hPcnt", 0)) * 100
                    turnover = float(t.get("turnover24h", 0))
                except (ValueError, TypeError):
                    continue
                if turnover < 500_000:
                    continue
                pairs_data.append({"pair": f"{base}/USDT", "change": change})

            sorted_pairs = sorted(pairs_data, key=lambda x: x["change"], reverse=True)
            gainers = [p["pair"] for p in sorted_pairs[:3]]
            losers = [p["pair"] for p in sorted_pairs[-3:]]
            self._movers_pairs = list(dict.fromkeys(gainers + losers))
            self._movers_last_update = now
            logger.info(f"Movers updated: gainers={gainers}, losers={losers}")
        except Exception as e:
            logger.warning(f"Movers refresh failed: {e}")

    # -------------------------------------------------------------------------
    # Informative pairs — BTC data + movers
    # -------------------------------------------------------------------------
    def informative_pairs(self):
        self._refresh_movers()
        pairs = []
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
        btc_df["btc_EMA_200"] = pta.ema(btc_df["close"], length=200, fillna=False)
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
        df["EMA_200"] = pta.ema(df["close"], length=200, fillna=False)
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
        df["EMA_100"] = pta.ema(df["close"], length=100, fillna=False)
        df["EMA_200"] = pta.ema(df["close"], length=200, fillna=False)
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
        df["volume_sma_20"] = pta.sma(df["volume"], length=20)
        df["num_empty_288"] = (df["volume"] <= 0).rolling(window=288, min_periods=288).sum()

        # --- Handle NaN for merged columns ---
        for col in ["RSI_14_1h", "RSI_14_4h", "RSI_14_1d"]:
            if col in df.columns:
                df[col] = df[col].astype(np.float64).replace(to_replace=[np.nan, None], value=50.0)

        # --- Global protections (NFI-style) ---
        df["protections_long_global"] = self._calc_global_protections(df)

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

        return score.clip(0, 100)

    # -------------------------------------------------------------------------
    # Populate entry trend (vectorized — fast)
    # -------------------------------------------------------------------------
    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[:, "enter_long"] = 0
        df.loc[:, "enter_tag"] = ""

        # Global protections
        prot = df.get("protections_long_global", pd.Series(True, index=df.index))
        empty_ok = df.get("num_empty_288", pd.Series(0, index=df.index)).fillna(0) <= 60

        # TA score for all rows
        ta_score = self._calculate_ta_score_vectorized(df)

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

        return df

    # -------------------------------------------------------------------------
    # Populate exit trend (basic — main exits via custom_exit)
    # -------------------------------------------------------------------------
    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[:, "exit_long"] = 0
        return df

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

        # --- Doom stoploss ---
        entry_cost = filled_entries[0].cost
        profit_stake = trade.calc_profit(rate=current_rate) if hasattr(trade, 'calc_profit') else (current_rate - trade.open_rate) / trade.open_rate * entry_cost
        if isinstance(profit_stake, (int, float)) and profit_stake < -(entry_cost * self.stop_threshold_doom):
            return "exit_stoploss_doom"

        # --- Normal stoploss with conditions (NFI stoploss_u_e pattern) ---
        if (
            current_profit < -self.stop_threshold_normal
            and last["close"] < last.get("EMA_200", float("inf"))
            and last.get("CMF_20", 0) < 0
            and rsi14 > prev.get("RSI_14", 50)
            and rsi14 > (rsi14_1h + 20)
        ):
            return "exit_stoploss_conditional"

        return None

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
