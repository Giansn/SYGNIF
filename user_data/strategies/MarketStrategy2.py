"""
MarketStrategy2 — NFI-style stack + AI sentiment (parallel to SygnifStrategy).

Architecture (inspired by NostalgiaForInfinityX7):
- Multi-timeframe analysis: 5m base + 15m/1h/4h/1d informative
- BTC correlation: BTC/USDT indicators merged into all pairs
- NFI-style indicators: RSI_3/14, Aroon, StochRSI, CMF, CCI, ROC, BB, EMA, Williams %R
- Global protections: Multi-TF cascade prevents buying during crashes
- Failure swing (Heavy91-style): `sf_*` columns; tunables `sf_lookback_bars`, `sf_vol_filter_min`,
  `sf_sl_base`, `sf_sl_vol_scale`, `sf_tp_vol_scale`, `sf_ta_split`, `max_slots_swing` via
  **user_data/strategy_adaptation.json** (clamped in strategy_adaptation.py), hot-reload ~60s.
- Sentiment layer: **MarketStrategy2Sentiment** = live Bybit snapshot + news + LLM
  (Cursor Cloud if `CURSOR_*` set; else two-step Haiku loop by default).
- NFI-style exit logic: Profit-tiered RSI exits + Williams %R + doom stoploss; swing tags use
  `_exit_swing_failure` / on-exchange SL tiers.

Cost: Cursor Cloud task pricing + optional Haiku fallback; see `SENTIMENT_BACKEND` in `.env`.
"""

import logging
import os
import sys
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import importlib.util
from pathlib import Path
from typing import Optional

import feedparser
import requests
import numpy as np
import pandas as pd

try:
    from trade_overseer.event_log import EventLog
    from trade_overseer.risk_manager import RiskManager, RiskEngineConfig
    _HAS_OVERSEER = True
except ImportError:
    _HAS_OVERSEER = False
import pandas_ta as pta
import talib.abstract as ta
from freqtrade.strategy import IStrategy, merge_informative_pair
from freqtrade.persistence import Trade
from pandas import DataFrame

from cursor_cloud_completion import cursor_cloud_completion
from live_market_snapshot import fetch_finance_agent_market_context
from sentiment_constants import FINANCE_AGENT_SENTIMENT_INSTRUCTIONS

logger = logging.getLogger(__name__)

# Freqtrade may not put this directory on sys.path for dynamic imports inside analyze_sentiment().
_strategies_dir = str(Path(__file__).resolve().parent)
if _strategies_dir not in sys.path:
    sys.path.insert(0, _strategies_dir)

_sentiment_http_mod = None


def _get_sentiment_http_client():
    """Load sibling sentiment_http_client.py by path (avoids ModuleNotFoundError in Freqtrade workers)."""
    global _sentiment_http_mod
    if _sentiment_http_mod is None:
        mod_path = Path(__file__).resolve().parent / "sentiment_http_client.py"
        spec = importlib.util.spec_from_file_location("_sygnif_sentiment_http_client", mod_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load sentiment_http_client from {mod_path}")
        _sentiment_http_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_sentiment_http_mod)
    return _sentiment_http_mod


# ---------------------------------------------------------------------------
# Sygnif Sentiment Layer
# ---------------------------------------------------------------------------

class SygnifSentiment:
    """Sentiment via Cursor Cloud Agent (primary) or Anthropic Haiku (fallback), with
    prompts framed to match the /finance-agent skill (implementation-grounded Sygnif).

    Use **MarketStrategy2Sentiment** in Docker futures — adds live Bybit context + optional
    two-step Haiku loop (`SENTIMENT_MS2_FINANCE_AGENT_LOOP`, default on).
    """

    def __init__(self):
        # auto | cursor_cloud | anthropic — auto prefers Cursor when CURSOR_* are set
        self._sentiment_backend = os.environ.get("SENTIMENT_BACKEND", "auto").strip().lower()
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
        # Monitoring counters
        self.non_zero_calls = 0      # Calls that returned a non-zero score
        self.parse_errors = 0         # JSON/regex parse failures
        self.api_errors = 0           # HTTP/network errors
        # Connection pooling — keep-alive for faster repeat calls
        self._session = requests.Session()
        # Circuit breaker — pause sentiment after N consecutive failures
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._circuit_threshold = 5       # failures before opening
        self._circuit_cooldown = 300       # 5 min pause when open

    def _use_cursor_cloud(self) -> bool:
        b = self._sentiment_backend
        if b in ("cursor", "cursor_cloud"):
            return True
        if b == "anthropic":
            return False
        # auto: match finance_agent — same CURSOR_* when both set
        if os.environ.get("CURSOR_API_KEY", "").strip() and os.environ.get(
            "CURSOR_AGENT_REPOSITORY", ""
        ).strip():
            return True
        return False

    def _parse_sentiment_score(self, text: str) -> Optional[float]:
        """Extract {\"score\": ...} from model output."""
        if not text:
            return None
        match = re.search(r"\{[^{}]*\"score\"[^{}]*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            result = json.loads(match.group(0))
            return max(-20.0, min(20.0, float(result["score"])))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def _call_api_with_retry(self, payload: dict, max_attempts: int = 3) -> Optional[requests.Response]:
        """POST to Anthropic API with exponential backoff and circuit breaker.

        Returns the Response object on success, or None on failure.
        Honors circuit breaker — returns None immediately if open.
        """
        # Circuit breaker check
        now = time.time()
        if now < self._circuit_open_until:
            return None

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        for attempt in range(max_attempts):
            try:
                resp = self._session.post(
                    self.base_url, headers=headers, json=payload, timeout=20
                )
                # 429 rate limit — long backoff
                if resp.status_code == 429:
                    wait = 5 * (2 ** attempt)  # 5s, 10s, 20s
                    logger.warning(f"Sentiment 429 rate limit, waiting {wait}s")
                    time.sleep(wait)
                    continue
                # 5xx server error — short backoff
                if 500 <= resp.status_code < 600:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(f"Sentiment {resp.status_code}, retrying in {wait}s")
                    time.sleep(wait)
                    continue
                # 4xx other than 429 — fail fast, no retry
                if 400 <= resp.status_code < 500:
                    self._consecutive_failures += 1
                    self._maybe_trip_breaker()
                    return resp
                # Success
                self._consecutive_failures = 0
                return resp
            except (requests.Timeout, requests.ConnectionError) as e:
                wait = 2 ** attempt
                logger.warning(f"Sentiment {type(e).__name__}, retrying in {wait}s")
                time.sleep(wait)
            except Exception as e:
                logger.error(f"Sentiment unexpected error: {e}")
                break

        # All retries exhausted
        self._consecutive_failures += 1
        self._maybe_trip_breaker()
        return None

    def _maybe_trip_breaker(self):
        """Open the circuit breaker if too many consecutive failures."""
        if self._consecutive_failures >= self._circuit_threshold:
            self._circuit_open_until = time.time() + self._circuit_cooldown
            logger.warning(
                f"Sentiment circuit breaker OPEN for {self._circuit_cooldown}s "
                f"after {self._consecutive_failures} consecutive failures"
            )
            self._consecutive_failures = 0  # reset after opening

    def _live_market_context_for_prompt(self, token: str) -> str:
        """Override in MarketStrategy2Sentiment to inject real-time Bybit data."""
        return ""

    def _use_finance_agent_haiku_loop(self) -> bool:
        """Two Haiku calls: (1) synthesize live tape + news, (2) JSON score. MS2 enables."""
        return False

    def _anthropic_response_text(self, data: dict) -> str:
        try:
            return (data.get("content") or [{}])[0].get("text") or ""
        except (IndexError, TypeError, AttributeError):
            return ""

    def _finalize_parsed_score(
        self, score: Optional[float], text: str, backend: str, token: str
    ) -> Optional[float]:
        if score is None:
            self.parse_errors += 1
            logger.error("%s sentiment: no JSON in response: %s", backend, text[:200] if text else "")
            return None
        reason = ""
        try:
            m = re.search(r"\{[^{}]*\"score\"[^{}]*\}", text, re.DOTALL)
            if m:
                reason = json.loads(m.group(0)).get("reason", "") or ""
        except Exception:
            pass
        if score != 0:
            self.non_zero_calls += 1
        logger.info(
            "%s sentiment for %s: %s — %s [stats: %sc %snz %spe %sae]",
            backend,
            token,
            score,
            reason,
            self.daily_calls,
            self.non_zero_calls,
            self.parse_errors,
            self.api_errors,
        )
        self._cache[token] = (time.time(), float(score))
        self._consecutive_failures = 0
        return float(score)

    def _anthropic_finance_agent_loop(
        self,
        token: str,
        current_price: float,
        ta_score: float,
        news_text: str,
        live_raw: str,
    ) -> Optional[float]:
        """Haiku step 1: synthesis. Step 2: JSON score (-20..20). Uses 2 API calls."""
        live_section = (
            f"\n--- Live Bybit snapshot (real-time) ---\n{live_raw}\n"
            if (live_raw or "").strip()
            else "\n--- Live Bybit snapshot: unavailable ---\n"
        )
        step1 = f"""{FINANCE_AGENT_SENTIMENT_INSTRUCTIONS}

MarketStrategy2 / finance-agent loop — STEP 1 of 2 (synthesis only; no JSON).

Token: {token}
Sygnif TA score (authoritative, from Freqtrade): {ta_score:.0f}/100
Last close from strategy pipeline: ${current_price:.6f}
{live_section}
Headlines:
{news_text}

Reply with ≤6 short bullets: (1) spot/perp tape vs 24h, (2) BTC context, (3) {token} narrative from headlines, (4) conflicts between tape and news, (5) does news add edge beyond TA?, (6) bull / bear / neutral lean. Plain text only."""

        self.daily_calls += 1
        resp1 = self._call_api_with_retry(
            {"model": self.model, "max_tokens": 400, "messages": [{"role": "user", "content": step1}]}
        )
        if resp1 is None or not resp1.ok:
            self.api_errors += 1
            logger.error("Haiku loop step1 failed for %s", token)
            return None
        try:
            text1 = self._anthropic_response_text(resp1.json())
        except Exception as e:
            self.parse_errors += 1
            logger.error("Haiku loop step1 parse error: %s", e)
            return None
        if not text1.strip():
            self.parse_errors += 1
            logger.error("Haiku loop step1 empty for %s", token)
            return None

        step2 = f"""MarketStrategy2 / finance-agent loop — STEP 2 of 2.

Step-1 synthesis:
{text1}

Sygnif TA score (still authoritative): {ta_score:.0f}/100
Token: {token}

Output ONLY a JSON object (no markdown):
{{"score": <integer -20..20>, "reason": "<one sentence linking TA + live tape + news>"}}

The score is ADDED to TA in the strategy. Prefer 0 when there is no edge."""

        self.daily_calls += 1
        resp2 = self._call_api_with_retry(
            {"model": self.model, "max_tokens": 150, "messages": [{"role": "user", "content": step2}]}
        )
        if resp2 is None or not resp2.ok:
            self.api_errors += 1
            logger.error("Haiku loop step2 failed for %s", token)
            return None
        try:
            text2 = self._anthropic_response_text(resp2.json())
        except Exception as e:
            self.parse_errors += 1
            logger.error("Haiku loop step2 parse error: %s", e)
            return None
        score = self._parse_sentiment_score(text2)
        return self._finalize_parsed_score(score, text2, "Haiku-loop", token)

    def _reset_daily_counter(self):
        today = datetime.now().date()
        if today > self._last_reset:
            logger.info(
                f"Sentiment daily stats: calls={self.daily_calls}, "
                f"non_zero={self.non_zero_calls}, "
                f"parse_errors={self.parse_errors}, "
                f"api_errors={self.api_errors}"
            )
            self.daily_calls = 0
            self.non_zero_calls = 0
            self.parse_errors = 0
            self.api_errors = 0
            self._last_reset = today

    def _get_cached(self, token: str) -> Optional[float]:
        if token in self._cache:
            ts, score = self._cache[token]
            if time.time() - ts < self.cache_ttl:
                return score
        return None

    def _fetch_rss(self, feed_url: str, token: str) -> list[str]:
        """Fetch headlines from a single RSS feed.
        Returns top 3 headlines regardless of token match — Claude decides relevance.
        Token-matching headlines are prioritized, then filled with general crypto news."""
        try:
            feed = feedparser.parse(feed_url)
            token_matches = []
            general = []
            for entry in feed.entries[:5]:
                title = entry.get("title", "")
                if not title:
                    continue
                if token.upper() in title.upper():
                    token_matches.append(title)
                else:
                    general.append(title)
            return (token_matches + general)[:3]
        except Exception as e:
            logger.warning(f"Feed error {feed_url}: {e}")
            return []

    def _fetch_reddit(self, token: str) -> list[str]:
        """Fetch top posts from r/CryptoCurrency mentioning the token."""
        try:
            url = f"https://www.reddit.com/r/CryptoCurrency/search.json?q={token}&sort=new&limit=5&restrict_sr=1"
            resp = requests.get(url, headers={"User-Agent": "sygnif/1.0"}, timeout=5)
            if resp.ok:
                posts = resp.json().get("data", {}).get("children", [])
                return [p.get("data", {}).get("title", "") for p in posts[:3] if p.get("data", {}).get("title")]
        except Exception as e:
            logger.warning(f"Reddit fetch error for {token}: {e}")
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
        with ThreadPoolExecutor(max_workers=5) as pool:
            rss_futures = {pool.submit(self._fetch_rss, url, token): url for url in feeds}
            gdelt_future = pool.submit(self._fetch_gdelt, token)
            reddit_future = pool.submit(self._fetch_reddit, token)
            for future in as_completed(rss_futures):
                headlines.extend(future.result())
            headlines.extend(gdelt_future.result())
            headlines.extend(reddit_future.result())

        # Dedupe while preserving order
        seen = set()
        deduped = []
        for h in headlines:
            if h and h not in seen:
                seen.add(h)
                deduped.append(h)

        result = deduped[:max_items]
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
        Cursor Cloud Agent (preferred) or Anthropic Haiku — score -20..+20.
        Subclasses may inject live market context and a two-step Haiku loop.
        """
        self._reset_daily_counter()

        cached = self._get_cached(token)
        if cached is not None:
            logger.info(f"Sentiment (cached) for {token}: {cached}")
            return cached

        http_url = os.environ.get("SYGNIF_SENTIMENT_HTTP_URL", "").strip()
        if http_url:
            ok, sc, err = _get_sentiment_http_client().post_sygnif_sentiment(
                http_url, token, current_price, ta_score, headlines
            )
            if ok and sc is not None:
                self.daily_calls += 1
                self._cache[token] = (time.time(), float(sc))
                if sc != 0:
                    self.non_zero_calls += 1
                logger.info("Sentiment HTTP (%s) for %s: %s", http_url, token, sc)
                return float(sc)
            logger.warning("SYGNIF_SENTIMENT_HTTP failed (%s): %s", token, err)
            if os.environ.get("SYGNIF_SENTIMENT_HTTP_ONLY", "").strip().lower() in (
                "1",
                "true",
                "yes",
            ):
                return None

        if self.daily_calls >= self.daily_limit:
            logger.warning("Sentiment daily limit reached, returning neutral")
            return 0.0

        use_cursor = self._use_cursor_cloud()

        if not use_cursor and not self.api_key:
            logger.warning(
                "Skipping sentiment: set CURSOR_API_KEY + CURSOR_AGENT_REPOSITORY (Cursor) "
                "and/or ANTHROPIC_API_KEY (Haiku fallback)"
            )
            return 0.0

        news_text = "\n".join(f"- {h}" for h in headlines) if headlines else "No recent headlines available."
        live_raw = (self._live_market_context_for_prompt(token) or "").strip()
        live_block = (
            f"\n--- Live Bybit snapshot (finance-agent market data) ---\n{live_raw}\n"
            if live_raw
            else ""
        )

        prompt = f"""{FINANCE_AGENT_SENTIMENT_INSTRUCTIONS}

Assess sentiment for trading {token}.

Current price: ${current_price:.4f}
Technical analysis score: {ta_score:.0f}/100 (50 = neutral, >60 = bullish, <40 = bearish)
{live_block}
Recent crypto headlines (mix of {token}-specific and general market):
{news_text}

Provide a sentiment adjustment score combining:
1. Any {token}-specific news (most weight if present)
2. General crypto market mood from the headlines
3. Macro/regulatory context implied by the news
4. How the live tape (if provided) agrees or disagrees with headlines

Rules:
- Score between -20 (strongly bearish) and +20 (strongly bullish)
- 0 = no edge / pure noise / contradictory signals
- It's OK to give a small score (±3 to ±8) based purely on general market mood when no {token}-specific news exists
- Reserve ±15 to ±20 for major specific events (regulatory action, listings, hacks, partnerships)
- If the headlines are pure noise or completely off-topic, return 0

Respond with ONLY a JSON object: {{"score": <number>, "reason": "<one sentence>"}}"""

        if use_cursor:
            self.daily_calls += 1
            text = cursor_cloud_completion(prompt, label="Sygnif sentiment")
            if text is None:
                self.api_errors += 1
                self._consecutive_failures += 1
                self._maybe_trip_breaker()
                logger.error(f"Cursor Cloud sentiment for {token}: failed or empty")
                if self._sentiment_backend == "auto" and self.api_key:
                    logger.info(f"Falling back to Anthropic Haiku for {token}")
                    use_cursor = False
                else:
                    return None

        if use_cursor:
            score = self._parse_sentiment_score(text)
            out = self._finalize_parsed_score(score, text, "Cursor", token)
            return out if out is not None else None

        # Anthropic (primary or Cursor auto-fallback): optional two-step finance-agent loop
        want_loop = self._use_finance_agent_haiku_loop() and bool(self.api_key)
        if want_loop and self.daily_calls > max(0, self.daily_limit - 2):
            logger.warning(
                "Sentiment: finance-agent Haiku loop needs 2 API calls; limit %s/%s — skip",
                self.daily_calls,
                self.daily_limit,
            )
            return 0.0
        if want_loop:
            return self._anthropic_finance_agent_loop(
                token, current_price, ta_score, news_text, live_raw
            )

        self.daily_calls += 1
        payload = {
            "model": self.model,
            "max_tokens": 100,
            "messages": [{"role": "user", "content": prompt}],
        }

        resp = self._call_api_with_retry(payload)

        if resp is None:
            self.api_errors += 1
            logger.error(f"Claude sentiment for {token}: all retries failed (or circuit open)")
            return None

        if not resp.ok:
            self.api_errors += 1
            logger.error(f"Claude API error: {resp.status_code} {resp.text[:200]}")
            return None

        try:
            data = resp.json()
            text = self._anthropic_response_text(data)
            score = self._parse_sentiment_score(text)
            return self._finalize_parsed_score(score, text, "Claude", token)
        except Exception as e:
            self.parse_errors += 1
            logger.error(f"Claude sentiment parse error: {e}")
            return None


class MarketStrategy2Sentiment(SygnifSentiment):
    """Live Bybit snapshot + shorter cache TTL; Haiku uses two-step finance-agent loop by default."""

    def __init__(self):
        super().__init__()
        self.cache_ttl = int(os.environ.get("SENTIMENT_MS2_CACHE_SEC", "180"))

    def _live_market_context_for_prompt(self, token: str) -> str:
        try:
            return fetch_finance_agent_market_context(token, session=self._session)
        except Exception as e:
            logger.warning("MarketStrategy2 live market context failed: %s", e)
            return ""

    def _use_finance_agent_haiku_loop(self) -> bool:
        return os.environ.get("SENTIMENT_MS2_FINANCE_AGENT_LOOP", "1").strip().lower() in (
            "1",
            "true",
            "yes",
        )


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class MarketStrategy2(IStrategy):
    """
    MarketStrategy2 — Futures variant of SygnifStrategy with NT risk-based sizing.

    Based on SygnifStrategy (NostalgiaForInfinityX7 patterns) with:
    - NT risk-based position sizing (2% equity per trade)
    - Ratcheting trail (+5%/+10% only, low tiers removed)
    - DCA scale-in for high-conviction entries
    - EventLog observability (SL tier tracking)
    - Global volume regime gate
    - Sentiment: **MarketStrategy2Sentiment** on `self.claude` — live Bybit snapshot + finance-agent Haiku loop
      (env: `SENTIMENT_MS2_FINANCE_AGENT_LOOP`, `SENTIMENT_MS2_CACHE_SEC`, `SENTIMENT_MS2_LINEAR_TICKER`)
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
        {"method": "CooldownPeriod", "stop_duration_candles": 2},
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

    # New pairs tracking — externally sourced (sentiment scanner, new listings, etc)
    _new_pairs: list[str] = []
    _new_pairs_last_update: float = 0.0
    _new_pairs_refresh_secs: int = 1800  # 30 min (newer signals = more frequent refresh)
    _new_pairs_path = "user_data/new_pairs.json"

    # Doom cooldown — per-pair lockout after stoploss hit
    _doom_cooldown: dict[str, float] = {}
    doom_cooldown_secs = 14400  # 4h
    _doom_loss_count: dict[str, list[float]] = {}  # consecutive loss tracking

    # Slot caps per entry type (prevent one type hogging all slots)
    max_slots_strong = 6      # strong_ta entries (TA >= strong_ta_min_score)
    max_slots_strong_short = 6  # strong_ta_short (futures) — mirrors long cap
    max_slots_swing = 4       # swing_failure, sygnif_swing (+ fa_/claude_ legacy aliases)
    _swing_tags = frozenset({
        "swing_failure",
        "swing_failure_short",
        "sygnif_swing",
        "sygnif_swing_short",
        "fa_swing",
        "fa_swing_short",
        "claude_swing",
        "claude_swing_short",
    })
    _swing_hybrid_long = frozenset({"sygnif_swing", "fa_swing", "claude_swing"})
    _swing_hybrid_short = frozenset({"sygnif_swing_short", "fa_swing_short", "claude_swing_short"})

    # --- Runtime tunables (defaults; overridden by user_data/strategy_adaptation.json) ---
    strong_ta_min_score = 65
    strong_ta_short_max_score = 25
    claude_long_score_low = 40
    claude_long_score_high = 64
    claude_short_score_low = 30
    claude_short_score_high = 60
    vol_strong_mult = 1.2
    # Failure swing (Heavy91-style) — overridden by strategy_adaptation.json
    sf_lookback_bars = 48
    sf_vol_filter_min = 0.03
    sf_sl_base = 0.02
    sf_sl_vol_scale = 0.02
    sf_tp_vol_scale = 0.05
    sf_ta_split = 50.0
    _adaptation_last_load: float = 0.0
    _adaptation_refresh_secs: int = 60

    # Premium tag reservation: non-premium entries are capped at
    # premium_nonreserved_max open trades. Remaining slots (max_open_trades -
    # premium_nonreserved_max) are reserved for tags in PREMIUM_TAGS only.
    PREMIUM_TAGS = frozenset(
        {"sygnif_s-5", "sygnif_swing_short", "claude_s-5", "claude_swing_short"}
    )
    premium_nonreserved_max = 10    # non-premium cap (used with max_open_trades=12)

    # Claude layer (same attr name as SygnifStrategy — populate_entry_trend uses self.claude)
    claude = MarketStrategy2Sentiment()

    # --- NT risk-based sizing (Lesson 2) ---
    RISK_PCT = 0.02  # 2% of equity risked per trade

    # --- DCA scale-in (Lesson 4) ---
    DCA_ELIGIBLE_TAGS = frozenset(
        {"sygnif_s-2", "sygnif_s-5", "claude_s-2", "claude_s-5"}
    )
    DCA_DRAWDOWN_STEP = -0.03
    DCA_MAX_ENTRIES = 1
    DCA_SCALE_FACTOR = 0.5

    # --- Volume regime gate (Lesson 5) ---
    MIN_ACTIVE_VOLUME_PAIRS = 3
    _active_volume_pairs: int = 999  # default high so gate is open until first scan

    # -------------------------------------------------------------------------
    # Enable shorts dynamically for futures mode
    # -------------------------------------------------------------------------
    def bot_start(self, **kwargs) -> None:
        if self.config.get("trading_mode", "") == "futures":
            self.can_short = True
        self._load_doom_cooldown()
        self._refresh_movers()
        self._refresh_new_pairs()
        self._refresh_strategy_adaptation(force=True)

        # NT Lesson 2+3: risk engine + event log
        self._risk_manager = None
        self._event_log = None
        self._last_sl_tier: dict[int, str] = {}
        if _HAS_OVERSEER:
            self._risk_manager = RiskManager(RiskEngineConfig(
                ratchet_tiers=(
                    (0.10, 0.015),
                    (0.05, 0.02),
                ),
            ))
            instance = "freqtrade-futures" if self.config.get("trading_mode", "") == "futures" else "freqtrade"
            self._event_log = EventLog(instance=instance)

    def _refresh_strategy_adaptation(self, force: bool = False) -> None:
        """Load bounded overrides from user_data/strategy_adaptation.json (hot-reload)."""
        now = time.time()
        if not force and (now - self._adaptation_last_load) < self._adaptation_refresh_secs:
            return
        self._adaptation_last_load = now
        try:
            ud = Path(__file__).resolve().parent.parent
            mod_path = ud / "strategy_adaptation.py"
            json_path = ud / "strategy_adaptation.json"
            spec = importlib.util.spec_from_file_location("_sygnif_adapt", mod_path)
            if spec is None or spec.loader is None or not mod_path.is_file():
                return
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            ovr = mod.load_adaptation_file(json_path)
            mod.apply_defaults_and_overrides(self, ovr)
        except Exception as e:
            logger.warning("strategy_adaptation: %s", e)

    def bot_loop_start(self, current_time=None, **kwargs) -> None:
        """Inject movers and externally-sourced new pairs into active whitelist."""
        self._refresh_strategy_adaptation(force=False)
        self._refresh_movers()
        self._refresh_new_pairs()
        if not self.dp:
            return
        current_wl = self.dp.current_whitelist()
        for pair in self._movers_pairs:
            if pair not in current_wl:
                current_wl.append(pair)
                logger.info(f"Mover {pair} added to whitelist")
        for pair in self._new_pairs:
            if pair not in current_wl:
                current_wl.append(pair)
                logger.info(f"New pair {pair} added to whitelist")

        # NT Lesson 5: scan volume regime for futures
        if self.config.get("trading_mode", "") == "futures":
            count = 0
            for p in current_wl:
                try:
                    df, _ = self.dp.get_analyzed_dataframe(p, self.timeframe)
                    if len(df) > 0 and df.iloc[-1].get("volume_sma_25", 0) > 50000:
                        count += 1
                except Exception:
                    pass
            self._active_volume_pairs = count

    # -------------------------------------------------------------------------
    # New pairs integration — externally sourced (sentiment scanner, listings)
    # -------------------------------------------------------------------------
    def _refresh_new_pairs(self):
        """Read user_data/new_pairs.json and inject pairs into whitelist.

        Expected file format (mirrors movers_pairlist.json):
        {
            "exchange": {"pair_whitelist": ["XRP/USDT", "DOGE/USDT", ...]},
            "_meta": {"source": "finance-agent", "updated": "2026-04-06T..."}
        }

        Pairs in this file get the same whitelist injection as movers,
        bypassing the static VolumePairList. Use this for:
          - Pairs identified by external scanners (finance-agent)
          - Recently listed coins below the AgeFilter threshold
          - Manual pair additions without editing config
        """
        now = time.time()
        if now - self._new_pairs_last_update < self._new_pairs_refresh_secs and self._new_pairs:
            return
        try:
            base = Path(__file__).resolve().parent
            while base != base.parent:
                candidate = base / self._new_pairs_path
                if candidate.exists():
                    break
                base = base.parent
            np_file = base / self._new_pairs_path
            if not np_file.exists():
                # Silent — file is optional
                self._new_pairs_last_update = now
                return
            data = json.loads(np_file.read_text())
            pairs = data.get("exchange", {}).get("pair_whitelist", [])
            # Filter for the right trading mode (spot vs futures syntax)
            is_futures = self.config.get("trading_mode", "") == "futures"
            valid = []
            for p in pairs:
                if is_futures:
                    # Futures pairs use BTC/USDT:USDT syntax
                    if ":" not in p:
                        p = f"{p}:USDT"
                    valid.append(p)
                else:
                    # Spot uses BTC/USDT (strip :USDT if present)
                    if ":" in p:
                        p = p.split(":")[0]
                    valid.append(p)
            if valid:
                self._new_pairs = valid
                self._new_pairs_last_update = now
                meta = data.get("_meta", {})
                logger.info(
                    f"New pairs loaded ({len(valid)}): {valid[:5]}{'...' if len(valid)>5 else ''} "
                    f"(source: {meta.get('source', 'unknown')})"
                )
        except Exception as e:
            logger.warning(f"New pairs refresh failed: {e}")

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
    # Informative pairs — BTC data + movers + new pairs
    # -------------------------------------------------------------------------
    def informative_pairs(self):
        self._refresh_movers()
        self._refresh_new_pairs()
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

        # New pairs (externally sourced — sentiment scanner, listings)
        for new_pair in self._new_pairs:
            for tf in [self.timeframe] + self.info_timeframes:
                pairs.append((new_pair, tf))

        # Dedupe
        pairs = list(dict.fromkeys(pairs))
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
                df[col] = df[col].astype(np.float64).fillna(50.0)

        # --- Failure Swing (Stop Hunt) indicators ---
        lb = max(8, int(self.sf_lookback_bars))
        df["sf_resistance"] = df["high"].shift(1).rolling(lb).max()
        df["sf_support"] = df["low"].shift(1).rolling(lb).min()
        # Stable level check: S/R unchanged for 2 bars (level is established)
        df["sf_resistance_stable"] = df["sf_resistance"] == df["sf_resistance"].shift(1)
        df["sf_support_stable"] = df["sf_support"] == df["sf_support"].shift(1)
        # EMA 120 for TP target
        df["EMA_120"] = pta.ema(df["close"], length=120)
        # Volatility filter: distance from EMA as fraction (blocks entries near EMA)
        df["sf_volatility"] = ((df["close"] - df["EMA_120"]).abs() / df["EMA_120"])
        df["sf_vol_filter"] = df["sf_volatility"] > float(self.sf_vol_filter_min)
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
        df["sf_sl_pct"] = float(self.sf_sl_base) + df["sf_volatility"] * float(self.sf_sl_vol_scale)
        df["sf_tp_ema"] = df["EMA_120"] * (
            1
            + df["sf_volatility"] * float(self.sf_tp_vol_scale) * np.sign(df["close"] - df["EMA_120"])
        )

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

        # BTC structural uptrend filter — block ALL shorts when BTC 4h RSI > 60
        # This is a hard structural filter, not just momentum-based
        if "btc_RSI_14_4h" in df.columns:
            prot &= df["btc_RSI_14_4h"].fillna(50.0) <= 60.0

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
        if enter_tag in self._swing_tags:
            try:
                df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                if len(df) > 0 and "sf_sl_pct" in df.columns:
                    sf_sl = df.iloc[-1].get("sf_sl_pct", 0.02)
                    if current_profit >= 0.05:
                        sl_val, tier = -0.02, "sf_ratchet_5pct"
                    elif current_profit >= 0.02:
                        sl_val, tier = -0.03, "sf_ratchet_2pct"
                    else:
                        sl_val, tier = -sf_sl, "sf_dynamic"
                    self._emit_sl_tier(trade, pair, tier, sl_val, current_profit)
                    return sl_val
            except Exception:
                pass
            self._emit_sl_tier(trade, pair, "sf_fallback", -0.03, current_profit)
            return -0.03

        # --- Ratcheting trail: tighten SL as profit grows ---
        # +1% and +2% tiers removed (NT Lesson 1): they clipped winners at +0.70% avg
        # while indicator exits capture +7.60%. With risk-based sizing, doom costs
        # a fixed % of equity so the safety net is the sizing, not the ratchet.
        if current_profit >= 0.10:
            sl_val, tier = -0.015, "ratchet_10pct"
        elif current_profit >= 0.05:
            sl_val, tier = -0.02, "ratchet_5pct"
        else:
            # Base SL: fixed doom stoploss
            sl = self.stop_threshold_doom_futures if is_futures else self.stop_threshold_doom_spot
            if is_futures:
                sl_val = -(sl / leverage)
            else:
                sl_val = -sl
            tier = "doom"

        self._emit_sl_tier(trade, pair, tier, sl_val, current_profit)
        return sl_val

    def _emit_sl_tier(self, trade: Trade, pair: str, tier: str,
                      sl_val: float, current_profit: float) -> None:
        """Emit order_updated event when the active SL tier changes (Lesson 3)."""
        if not hasattr(self, "_event_log") or self._event_log is None:
            return
        trade_id = getattr(trade, "id", None)
        if trade_id is None:
            return
        prev_tier = self._last_sl_tier.get(trade_id)
        if prev_tier == tier:
            return
        self._last_sl_tier[trade_id] = tier
        try:
            self._event_log.emit(
                "order_updated",
                instrument_id=pair,
                trade_id=trade_id,
                data={
                    "sl_tier": tier,
                    "sl_value": sl_val,
                    "current_profit": round(current_profit, 6),
                    "leverage": trade.leverage or 1.0,
                    "enter_tag": trade.enter_tag or "",
                },
            )
        except Exception:
            pass

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

        # Strong TA — TA >= min + volume; skip chasing when 1h RSI is extremely overbought
        vol_ok = df["volume"] > (df["volume_sma_25"] * self.vol_strong_mult)
        r1h = df.get("RSI_14_1h", pd.Series(50.0, index=df.index)).fillna(50.0)
        strong_ta_htf_ok = r1h < 72.0
        strong = (
            prot
            & empty_ok
            & (ta_score >= self.strong_ta_min_score)
            & vol_ok
            & strong_ta_htf_ok
        )
        df.loc[strong, "enter_long"] = 1
        df.loc[strong, "enter_tag"] = "strong_ta"

        # --- Claude sentiment long — ambiguous zone (TA 40-64), LAST candle only ---
        if len(df) > 0 and not df.iloc[-1].get("enter_long", 0):
            last_score = ta_score.iloc[-1]
            last_prot = prot.iloc[-1] if hasattr(prot, 'iloc') else True
            last_empty = empty_ok.iloc[-1] if hasattr(empty_ok, 'iloc') else True

            if last_prot and last_empty and self.claude_long_score_low <= last_score <= self.claude_long_score_high:
                pair = metadata.get("pair", "XRP/USDT")
                token = pair.split("/")[0]
                price = df.iloc[-1]["close"]

                headlines = self.claude.fetch_news(token)
                sentiment = self.claude.analyze_sentiment(token, price, last_score, headlines)

                # sentiment=None → API broken, skip sentiment entry (don't enter blind)
                # |sentiment| < 2 → noise / weak signal, skip (raised 2026-04-07
                # from "any non-zero" because tier-1 sentiment trades were
                # over-represented in losers without compensating wins)
                # |sentiment| >= 2 → real signal, combine with TA
                if sentiment is not None and abs(sentiment) >= 2:
                    final_score = last_score + sentiment
                    if final_score >= self.sentiment_threshold_buy:
                        df.iloc[-1, df.columns.get_loc("enter_long")] = 1
                        df.iloc[-1, df.columns.get_loc("enter_tag")] = f"sygnif_s{sentiment:.0f}"

        # --- Failure Swing entries (last candle only) ---
        if len(df) > 0 and not df.iloc[-1].get("enter_long", 0):
            last_prot = prot.iloc[-1] if hasattr(prot, 'iloc') else True
            last_empty = empty_ok.iloc[-1] if hasattr(empty_ok, 'iloc') else True
            sf_long = df.iloc[-1].get("sf_long", False)

            if last_prot and last_empty and sf_long:
                last_score = ta_score.iloc[-1]
                split = float(self.sf_ta_split)
                if last_score >= split:
                    # sygnif_swing: failure swing + TA confluence
                    df.iloc[-1, df.columns.get_loc("enter_long")] = 1
                    df.iloc[-1, df.columns.get_loc("enter_tag")] = "sygnif_swing"
                else:
                    # swing_failure: standalone, TA not confirming but pattern is clear
                    df.iloc[-1, df.columns.get_loc("enter_long")] = 1
                    df.iloc[-1, df.columns.get_loc("enter_tag")] = "swing_failure"


        # =====================================================================
        # SHORT ENTRIES (futures only — guarded by can_short in config)
        # =====================================================================
        df.loc[:, "enter_short"] = 0

        prot_short = df.get("protections_short_global", pd.Series(True, index=df.index))

        # Strong TA short — volume gate; skip when 4h is extremely oversold (violent bounce risk)
        vol_ok_short = df["volume"] > (df["volume_sma_25"] * self.vol_strong_mult)
        r4h = df.get("RSI_14_4h", pd.Series(50.0, index=df.index)).fillna(50.0)
        strong_short_htf_ok = r4h > 28.0
        strong_short = (
            prot_short
            & empty_ok
            & (ta_score <= self.strong_ta_short_max_score)
            & vol_ok_short
            & strong_short_htf_ok
        )
        df.loc[strong_short, "enter_short"] = 1
        df.loc[strong_short, "enter_tag"] = "strong_ta_short"

        # Ambiguous zone — Claude sentiment short on LAST candle only
        if len(df) > 0 and not df.iloc[-1].get("enter_short", 0):
            last_score = ta_score.iloc[-1]
            last_prot_s = prot_short.iloc[-1] if hasattr(prot_short, 'iloc') else True
            last_empty = empty_ok.iloc[-1] if hasattr(empty_ok, 'iloc') else True

            if last_prot_s and last_empty and self.claude_short_score_low <= last_score <= self.claude_short_score_high:
                pair = metadata.get("pair", "XRP/USDT")
                token = pair.split("/")[0]
                price = df.iloc[-1]["close"]

                headlines = self.claude.fetch_news(token)
                sentiment = self.claude.analyze_sentiment(token, price, last_score, headlines)

                # sentiment=None → API broken, skip
                # |sentiment| < 2 → noise / weak signal, skip (raised 2026-04-07)
                # |sentiment| >= 2 → real signal, combine with TA
                if sentiment is not None and abs(sentiment) >= 2:
                    final_score = last_score + sentiment
                    if final_score <= self.sentiment_threshold_sell:
                        df.iloc[-1, df.columns.get_loc("enter_short")] = 1
                        df.iloc[-1, df.columns.get_loc("enter_tag")] = f"sygnif_short_s{sentiment:.0f}"

        # --- Failure Swing short entries (last candle only) ---
        if len(df) > 0 and not df.iloc[-1].get("enter_short", 0):
            last_prot_s = prot_short.iloc[-1] if hasattr(prot_short, 'iloc') else True
            last_empty = empty_ok.iloc[-1] if hasattr(empty_ok, 'iloc') else True
            sf_short = df.iloc[-1].get("sf_short", False)

            if last_prot_s and last_empty and sf_short:
                last_score = ta_score.iloc[-1]
                split = float(self.sf_ta_split)
                if last_score <= split:
                    # sygnif_swing_short: failure swing + bearish TA confluence
                    df.iloc[-1, df.columns.get_loc("enter_short")] = 1
                    df.iloc[-1, df.columns.get_loc("enter_tag")] = "sygnif_swing_short"
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
    # NT risk-based position sizing (Lesson 2)
    # -------------------------------------------------------------------------
    def custom_stake_amount(self, pair: str, current_time: datetime,
                            current_rate: float, proposed_stake: float,
                            min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str],
                            side: str, **kwargs) -> float:
        if not self._risk_manager:
            return proposed_stake

        equity = None
        if hasattr(self, "wallets") and self.wallets:
            equity = self.wallets.get_free("USDT")
        if not equity or equity <= 0:
            return proposed_stake

        is_futures = self.config.get("trading_mode", "") == "futures"
        doom_sl = self.stop_threshold_doom_futures if is_futures else self.stop_threshold_doom_spot
        sl_distance_pct = (doom_sl / leverage) if is_futures else doom_sl

        if self.dp:
            try:
                df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                if len(df) > 0:
                    atr_pct = df.iloc[-1].get("atr_pct", 0)
                    if atr_pct > 0:
                        atr_sl = (atr_pct / 100.0) * 2.0
                        sl_distance_pct = max(sl_distance_pct, atr_sl)
            except Exception:
                pass

        if sl_distance_pct <= 0:
            return proposed_stake

        if side == "long":
            sl_price = current_rate * (1 - sl_distance_pct)
        else:
            sl_price = current_rate * (1 + sl_distance_pct)

        position_size = self._risk_manager.calculate_position_size(
            equity=equity, entry=current_rate, stop_loss=sl_price,
            risk_pct=self.RISK_PCT,
        )
        stake = (position_size * current_rate) / leverage

        stake = max(min_stake or 0, min(stake, max_stake))
        logger.info(
            "NT sizing: %s sl_dist=%.2f%% equity=%.1f → stake=%.2f (proposed=%.2f)",
            pair, sl_distance_pct * 100, equity, stake, proposed_stake,
        )
        return stake

    # -------------------------------------------------------------------------
    # DCA scale-in for high-conviction entries (Lesson 4)
    # -------------------------------------------------------------------------
    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: Optional[float], max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs) -> Optional[float]:
        tag = trade.enter_tag or ""
        if tag not in self.DCA_ELIGIBLE_TAGS:
            return None

        filled = trade.nr_of_successful_entries
        if filled > self.DCA_MAX_ENTRIES:
            return None
        if current_profit > self.DCA_DRAWDOWN_STEP:
            return None

        original_stake = trade.stake_amount / max(filled, 1)
        dca_stake = original_stake * self.DCA_SCALE_FACTOR
        dca_stake = max(min_stake or 0, min(dca_stake, max_stake))

        logger.info(
            "DCA scale-in: %s tag=%s entries=%d profit=%.2f%% → +%.2f USDT",
            trade.pair, tag, filled, current_profit * 100, dca_stake,
        )
        return dca_stake

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

        if tag == "strong_ta_short":
            count = sum(1 for t in open_trades if (t.enter_tag or "") == "strong_ta_short")
            if count >= self.max_slots_strong_short:
                logger.info(
                    f"Strong TA short slot cap: {count}/{self.max_slots_strong_short}, skipping {pair}"
                )
                return False

        if tag in self._swing_tags:
            count = sum(1 for t in open_trades if (t.enter_tag or "") in self._swing_tags)
            if count >= self.max_slots_swing:
                logger.info(f"Swing slot cap: {count}/{self.max_slots_swing}, skipping {pair} ({tag})")
                return False

        # Premium-tag slot reservation
        # Non-premium tags are hard-capped at `premium_nonreserved_max` open trades,
        # leaving the top slots available only for high-edge tags (e.g. sygnif_s-5).
        # Premium tags bypass this cap and may fill up to max_open_trades.
        if tag not in self.PREMIUM_TAGS:
            total_open = len(open_trades)
            if total_open >= self.premium_nonreserved_max:
                logger.info(
                    f"Premium reserve: {total_open}/{self.premium_nonreserved_max} non-premium "
                    f"slots full, blocking {tag} on {pair} (reserved for {self.PREMIUM_TAGS})"
                )
                return False

        # NT Lesson 5: global volume regime gate (premium tags bypass)
        if (self.config.get("trading_mode", "") == "futures"
                and tag not in self.PREMIUM_TAGS
                and self._active_volume_pairs < self.MIN_ACTIVE_VOLUME_PAIRS):
            logger.info(
                "Volume regime: only %d/%d pairs active, blocking %s on %s",
                self._active_volume_pairs, self.MIN_ACTIVE_VOLUME_PAIRS, tag, pair,
            )
            return False

        # Futures: minimum average volume gate (filter micro caps, swing bypasses)
        if self.config.get("trading_mode", "") == "futures" and tag not in self._swing_tags and self.dp:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if len(df) > 0 and "volume_sma_25" in df.columns:
                vol_avg = df.iloc[-1].get("volume_sma_25", 0)
                if vol_avg < 50000:
                    logger.info(f"Futures volume gate: {pair} vol_sma_25={vol_avg:.0f} < 50k, skipping")
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
            losses = self._doom_loss_count.get(pair, [])
            losses = [t for t in losses if now - t < 86400] + [now]
            self._doom_loss_count[pair] = losses
            logger.info(f"Doom cooldown set for {pair} after {exit_reason} ({len(losses)} losses in 24h)")

        # NT Lesson 3: emit position_closed with the last active SL tier
        if hasattr(self, "_event_log") and self._event_log is not None:
            try:
                trade_id = getattr(trade, "id", None)
                last_tier = self._last_sl_tier.pop(trade_id, "unknown") if trade_id else "unknown"
                leverage = trade.leverage or 1.0
                realized_pnl = (rate - trade.open_rate) * amount
                if trade.is_short:
                    realized_pnl = -realized_pnl
                self._event_log.emit_position_closed(
                    instrument_id=pair,
                    entry_side="short" if trade.is_short else "long",
                    avg_px_open=trade.open_rate,
                    avg_px_close=rate,
                    realized_pnl=realized_pnl,
                    trade_id=trade_id,
                    exit_reason=exit_reason,
                    last_sl_tier=last_tier,
                    leverage=leverage,
                    enter_tag=trade.enter_tag or "",
                )
            except Exception:
                pass

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

        if enter_tag in self._swing_hybrid_long:
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

        if enter_tag in self._swing_hybrid_short:
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

