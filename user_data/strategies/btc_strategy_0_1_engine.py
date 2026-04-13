"""
BTC_Strategy_0.1 — R01/R02/R03 helpers (registry + training JSON).

Paths are resolved from ``user_data/strategies/`` → repo root. Missing files → safe defaults.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TAG_R01 = "BTC-0.1-R01"
TAG_R02 = "BTC-0.1-R02"
TAG_R03 = "BTC-0.1-R03"  # R03 = **scalping** sleeve (PAC pullback proxy + scalp TP/RSI exits)
RULE_TAGS = (TAG_R01, TAG_R02, TAG_R03)

# --- L3 / ruleprediction first-trade risk box (see letscrash/BTC_Strategy_0.1.md §7.1) ---
# R03 scalping box: tight TP / RSI exit / SL floor vs parent
R03_SCALP_TP_PROFIT_PCT = 0.012  # × max(1, leverage) → exit_btc01_r03_scalp_take
R03_SCALP_RSI_OVERBOUGHT = 62.0  # → exit_btc01_r03_scalp_overbought
R01_R03_STACK_GUARD_LOSS_PCT = 0.008  # × max(1, leverage) → exit_btc01_r01_stack_guard
# custom_stoploss: never looser than this floor vs parent Sygnif doom (same FT ratio units as parent return)
R03_STOPLOSS_FLOOR_VS_PARENT = -0.025


def _repo_root() -> Path:
    # strategies/ → user_data/ → SYGNIF repo root
    return Path(__file__).resolve().parent.parent.parent


def registry_path() -> Path:
    return _repo_root() / "letscrash" / "btc_strategy_0_1_rule_registry.json"


def _registry_raw() -> dict[str, Any]:
    try:
        return json.loads(registry_path().read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("btc01 registry read: %s", e)
        return {}


def tuning_config() -> dict[str, Any]:
    """Live-tunable R01/R02 section (``tuning``) in ``btc_strategy_0_1_rule_registry.json``."""
    return _registry_raw().get("tuning") or {}


def training_channel_path() -> Path:
    return _repo_root() / "prediction_agent" / "training_channel_output.json"


@lru_cache(maxsize=1)
def load_notional_cap_usdt() -> float:
    try:
        raw = _registry_raw()
        cap = float((raw.get("rule_proof_bucket") or {}).get("notional_cap_usdt") or 3333.33)
        return max(100.0, cap)
    except Exception as e:
        logger.debug("btc01 registry load: %s", e)
        return 3333.33


def _read_training_channel() -> dict[str, Any]:
    p = training_channel_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("btc01 training_channel read: %s", e)
        return {}


def r01_training_runner_bearish() -> bool:
    """
    R01 governance proxy: strong next-bar-down probability + runner bearish consensus
    (see RULE_GENERATION §5). Used to block aggressive long *timing* on BTC.

    Thresholds come from registry ``tuning.r01_governance`` (defaults preserve legacy 90 / BEARISH).
    """
    g = (tuning_config().get("r01_governance") or {})
    try:
        p_min = float(g.get("p_down_min_pct", 90.0))
    except (TypeError, ValueError):
        p_min = 90.0
    cons_need = str(g.get("runner_consensus_equals", "BEARISH") or "BEARISH").upper()

    doc = _read_training_channel()
    rec = doc.get("recognition") or {}
    try:
        p_down = float(rec.get("last_bar_probability_down_pct") or 0.0)
    except (TypeError, ValueError):
        p_down = 0.0
    snap = rec.get("btc_predict_runner_snapshot") or {}
    pred = snap.get("predictions") or {}
    cons = str(pred.get("consensus", "") or "").upper()
    return p_down >= p_min and cons == cons_need


def btc01_r02_trend_long_row(row: pd.Series) -> bool:
    """
    R02 HTF trend gate for **BTC-0.1** only — same geometry as ``btc_trend_regime.btc_trend_long_row``,
    but thresholds from registry ``tuning.r02_regime`` (finetune without changing global Sygnif defaults).
    """
    t = (tuning_config().get("r02_regime") or {})
    try:
        rsi_min = float(t.get("rsi_bull_min", 50.0))
    except (TypeError, ValueError):
        rsi_min = 50.0
    try:
        adx_min = float(t.get("adx_min", 25.0))
    except (TypeError, ValueError):
        adx_min = 25.0

    r1 = float(row.get("RSI_14_1h", 50) or 50)
    r4 = float(row.get("RSI_14_4h", 50) or 50)
    adx = float(row.get("ADX_14", 0) or 0)
    close = float(row.get("close", 0) or 0)
    ema1h = float(row.get("EMA_200_1h", np.nan) or np.nan)
    if not np.isfinite(ema1h) or ema1h <= 0 or close <= 0:
        return False
    return bool(
        r1 > rsi_min
        and r4 > rsi_min
        and close > ema1h
        and adx > adx_min
    )


def r03_pullback_long(df: pd.DataFrame) -> bool:
    """
    **R03 = scalping pattern** (tagged sleeve): shallow RSI rebound + compressed trend (PAC-ish),
    last bar only — not full Pine replay.

    Research siblings (Pine, not wired): ``justunclel_scalping_pullback_tool_r1_1_v4.pine``,
    ``bullbyte_pro_scalper_ai_mpl2.pine`` (composite oscillator + latching — MPL 2.0).
    """
    if len(df) < 6 or "RSI_14" not in df.columns:
        return False
    rsi = df["RSI_14"].astype(float)
    adx = df.get("ADX_14", pd.Series(20.0, index=df.index)).astype(float).fillna(20.0)
    close = df["close"].astype(float)
    i = len(df) - 1
    rsi_now = float(rsi.iloc[i])
    rsi_prev = float(rsi.iloc[i - 1])
    rsi_3 = float(rsi.iloc[i - 3])
    if rsi_3 >= 38.0:
        return False
    if not (rsi_now > 42.0 and rsi_now > rsi_prev):
        return False
    if float(adx.iloc[i]) >= 34.0:
        return False
    if float(close.iloc[i]) <= float(close.iloc[i - 1]):
        return False
    return True


def bucket_used_stake_usdt(open_trades) -> float:
    total = 0.0
    for t in open_trades:
        tag = (t.enter_tag or "").strip()
        if not tag.startswith("BTC-0.1-R"):
            continue
        try:
            total += float(t.stake_amount or 0.0)
        except (TypeError, ValueError):
            continue
    return total
