"""
BTC_Strategy_0.1 — R01/R02/R03 helpers (registry + training JSON).

Paths are resolved from ``user_data/strategies/`` → repo root. Missing files → safe defaults.
"""

from __future__ import annotations

import json
import logging
import os
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


def slot_cap_r01() -> int:
    t = (tuning_config().get("slot_caps") or {})
    try:
        return max(1, int(t.get("r01", 6)))
    except (TypeError, ValueError):
        return 6


def slot_cap_r02() -> int:
    t = (tuning_config().get("slot_caps") or {})
    try:
        return max(1, int(t.get("r02", 2)))
    except (TypeError, ValueError):
        return 2


def slot_cap_r03() -> int:
    t = (tuning_config().get("slot_caps") or {})
    try:
        return max(1, int(t.get("r03", 3)))
    except (TypeError, ValueError):
        return 3


def r03_scalp_tp_profit_pct() -> float:
    t = (tuning_config().get("r03_scalp") or {})
    try:
        v = float(t.get("tp_profit_pct", R03_SCALP_TP_PROFIT_PCT))
        return max(0.0005, min(v, 0.05))
    except (TypeError, ValueError):
        return R03_SCALP_TP_PROFIT_PCT


def r03_scalp_rsi_overbought() -> float:
    t = (tuning_config().get("r03_scalp") or {})
    try:
        v = float(t.get("rsi_overbought", R03_SCALP_RSI_OVERBOUGHT))
        return max(50.0, min(v, 90.0))
    except (TypeError, ValueError):
        return R03_SCALP_RSI_OVERBOUGHT


def r01_r03_stack_guard_loss_pct() -> float:
    t = (tuning_config().get("r03_scalp") or {})
    try:
        v = float(t.get("stack_guard_loss_pct", R01_R03_STACK_GUARD_LOSS_PCT))
        return max(0.001, min(v, 0.05))
    except (TypeError, ValueError):
        return R01_R03_STACK_GUARD_LOSS_PCT


def training_channel_path() -> Path:
    return _repo_root() / "prediction_agent" / "training_channel_output.json"


# --- Swarm snapshot (``prediction_agent/swarm_knowledge_output.json``) — optional BTC-0.1 gate ---
_swarm_snap_mtime: float = -1.0
_swarm_snap_doc: dict[str, Any] = {}


def swarm_knowledge_path() -> Path:
    d = (os.environ.get("SYGNIF_PREDICTION_AGENT_DIR") or os.environ.get("PREDICTION_AGENT_DIR") or "").strip()
    if d:
        return Path(d) / "swarm_knowledge_output.json"
    return _repo_root() / "prediction_agent" / "swarm_knowledge_output.json"


def load_swarm_snapshot() -> dict[str, Any]:
    """Read fused swarm JSON (mtime-cached). Empty if missing."""
    global _swarm_snap_mtime, _swarm_snap_doc
    p = swarm_knowledge_path()
    if not p.is_file():
        return {}
    try:
        st = p.stat().st_mtime
    except OSError:
        return {}
    if st == _swarm_snap_mtime and _swarm_snap_doc:
        return _swarm_snap_doc
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("btc01 swarm snapshot read: %s", e)
        return {}
    _swarm_snap_mtime = st
    _swarm_snap_doc = doc if isinstance(doc, dict) else {}
    return _swarm_snap_doc


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def swarm_trail_tp_tuning() -> dict[str, Any]:
    """Optional ``tuning.swarm_trail_tp`` in registry (overrides env defaults when set)."""
    raw = tuning_config().get("swarm_trail_tp")
    return raw if isinstance(raw, dict) else {}


def _env_float_swarm(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def swarm_adverse_to_long(row: pd.Series) -> bool:
    """
    Swarm snapshot disagrees with holding a long (bearish / weak / conflict).

    Used with ``SYGNIF_TRAILING_TPSL`` + ``BTC_Strategy_0_1`` to tighten take-profit
    (smaller callback from ``trade.max_rate``) and bank a win.
    """
    st = swarm_trail_tp_tuning()
    if st.get("enabled") is False:
        return False
    label = str(row.get("swarm_label") or "").upper()
    try:
        bear_needle = str(st.get("bear_label_substr", "BEAR") or "BEAR").upper()
    except Exception:
        bear_needle = "BEAR"
    if bear_needle and bear_needle in label:
        return True
    try:
        mean = float(row.get("swarm_mean") or 0.0)
    except (TypeError, ValueError):
        mean = 0.0
    raw_m = st.get("adverse_mean_max")
    if raw_m is not None and raw_m != "":
        try:
            mean_adverse = float(raw_m)
        except (TypeError, ValueError):
            mean_adverse = _env_float_swarm("SYGNIF_TS_SWARM_MEAN_MAX", -0.2)
    else:
        mean_adverse = _env_float_swarm("SYGNIF_TS_SWARM_MEAN_MAX", -0.2)
    if mean <= mean_adverse:
        return True
    conflict = bool(row.get("swarm_conflict"))
    raw_c = st.get("adverse_conflict_mean_max")
    if raw_c is not None and raw_c != "":
        try:
            c_mean = float(raw_c)
        except (TypeError, ValueError):
            c_mean = _env_float_swarm("SYGNIF_TS_SWARM_CONFLICT_MEAN_MAX", 0.08)
    else:
        c_mean = _env_float_swarm("SYGNIF_TS_SWARM_CONFLICT_MEAN_MAX", 0.08)
    if conflict and mean < c_mean:
        return True
    return False


def swarm_trail_callback_pct() -> float:
    """Pullback from peak (instrument %) to realize win when Swarm is adverse — tighter than generic TSL."""
    st = swarm_trail_tp_tuning()
    try:
        v = float(st.get("callback_pct", 0.0))
        if v > 0:
            return max(1e-6, min(v, 0.08))
    except (TypeError, ValueError):
        pass
    return max(1e-6, min(_env_float_swarm("SYGNIF_TS_SWARM_CALLBACK_PCT", 0.004), 0.08))


def swarm_trail_min_profit_gate(leverage: float) -> float:
    """Minimum ``current_profit`` (same units as Freqtrade / BTC-0.1 tag TP) before Swarm trail TP arms."""
    st = swarm_trail_tp_tuning()
    try:
        base = float(st.get("min_profit", 0.0))
        if base > 0:
            return base * max(1.0, float(leverage))
    except (TypeError, ValueError):
        pass
    base = _env_float_swarm("SYGNIF_TS_SWARM_MIN_PROFIT", 0.008)
    return base * max(1.0, float(leverage))


def attach_swarm_columns(df: pd.DataFrame) -> None:
    """Broadcast ``swarm_mean`` / ``swarm_label`` / ``swarm_conflict`` from snapshot onto ``df``."""
    if df.empty:
        return
    sw = load_swarm_snapshot()
    mean = float(sw.get("swarm_mean") or 0.0) if sw else 0.0
    label = str(sw.get("swarm_label") or "") if sw else ""
    conflict = bool(sw.get("swarm_conflict")) if sw else False
    if "swarm_mean" not in df.columns:
        df.loc[:, "swarm_mean"] = mean
    else:
        df.loc[:, "swarm_mean"] = mean
    if "swarm_label" not in df.columns:
        df.loc[:, "swarm_label"] = label
    else:
        df.loc[:, "swarm_label"] = label
    if "swarm_conflict" not in df.columns:
        df.loc[:, "swarm_conflict"] = conflict
    else:
        df.loc[:, "swarm_conflict"] = conflict


def swarm_root_blocks_long() -> bool:
    """
    When ``SYGNIF_BTC01_SWARM_ROOT=1``, Swarm may veto **all** new long entries on BTC
    (read-only signal from ``swarm_knowledge_output.json``).

    Blocks when: bearish label, strongly negative mean, or conflict + non-bull mean.
    """
    if not _env_truthy("SYGNIF_BTC01_SWARM_ROOT"):
        return False
    sw = load_swarm_snapshot()
    if not sw:
        return False
    try:
        mean = float(sw.get("swarm_mean") or 0.0)
    except (TypeError, ValueError):
        mean = 0.0
    label = str(sw.get("swarm_label") or "")
    conflict = bool(sw.get("swarm_conflict"))
    if "BEAR" in label.upper():
        return True
    if mean <= -0.35:
        return True
    if conflict and mean < 0.05:
        return True
    return False


def load_notional_cap_usdt() -> float:
    """No LRU cache — cap follows registry edits on Freqtrade /reload_config or restart."""
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
    Set ``enabled``: false to disable governance (demo / sampling only).
    """
    g = (tuning_config().get("r01_governance") or {})
    if g.get("enabled") is False:
        return False
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

    Thresholds: ``tuning.r03_pullback`` in registry (defaults match legacy §7).

    Research siblings (Pine, not wired): ``justunclel_scalping_pullback_tool_r1_1_v4.pine``,
    ``bullbyte_pro_scalper_ai_mpl2.pine`` (composite oscillator + latching — MPL 2.0).
    """
    if len(df) < 6 or "RSI_14" not in df.columns:
        return False
    t = (tuning_config().get("r03_pullback") or {})
    try:
        rsi_prior_max = float(t.get("rsi_prior_3bars_max", 38.0))
    except (TypeError, ValueError):
        rsi_prior_max = 38.0
    try:
        rsi_now_min = float(t.get("rsi_now_min", 42.0))
    except (TypeError, ValueError):
        rsi_now_min = 42.0
    try:
        adx_max = float(t.get("adx_max", 34.0))
    except (TypeError, ValueError):
        adx_max = 34.0

    rsi = df["RSI_14"].astype(float)
    adx = df.get("ADX_14", pd.Series(20.0, index=df.index)).astype(float).fillna(20.0)
    close = df["close"].astype(float)
    i = len(df) - 1
    rsi_now = float(rsi.iloc[i])
    rsi_prev = float(rsi.iloc[i - 1])
    rsi_3 = float(rsi.iloc[i - 3])
    if rsi_3 >= rsi_prior_max:
        return False
    if not (rsi_now > rsi_now_min and rsi_now > rsi_prev):
        return False
    if float(adx.iloc[i]) >= adx_max:
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


def entry_prediction_enabled() -> bool:
    g = (tuning_config().get("entry_prediction") or {})
    if g.get("enabled") is False:
        return False
    return True


def entry_prediction_extra_tags() -> list[str]:
    g = (tuning_config().get("entry_prediction") or {})
    extra = g.get("also_gate_tags")
    if extra is None:
        return ["orb_long"]
    if isinstance(extra, list):
        return [str(x) for x in extra]
    return []


def entry_prediction_blocks_long_under_bearish(tag: str) -> bool:
    """
    When ``r01_training_runner_bearish()`` is true, should this long ``enter_tag`` be blocked?

    - Always (legacy): R01 label, strong_ta, orb_long, sygnif_s* longs.
    - If ``entry_prediction.enabled``: also BTC-0.1-R02/R03 and ``also_gate_tags``.
    """
    t = (tag or "").strip()
    if t in (TAG_R01, "strong_ta", "orb_long"):
        return True
    if t.startswith("sygnif_s") and not t.startswith("sygnif_short"):
        return True
    if not entry_prediction_enabled():
        return False
    if t.startswith("BTC-0.1-R"):
        return True
    return t in frozenset(entry_prediction_extra_tags())


def _tp_sl_block_for_tag(tag: str) -> dict[str, Any]:
    raw = (tuning_config().get("tp_sl") or {})
    if not isinstance(raw, dict):
        return {}
    b = raw.get(tag)
    return b if isinstance(b, dict) else {}


def tag_sl_return_cap(trade, tag: str, *, is_futures: bool) -> float | None:
    """
    Tighter bound for ``custom_stoploss`` return (negative float), or ``None`` to use parent only.
    Uses ``sl_doom`` in the same spirit as Sygnif doom: futures → ``-(sl_doom / leverage)``.
    """
    if trade.is_short:
        return None
    t = (tag or "").strip()
    if not t.startswith("BTC-0.1-R"):
        return None
    block = _tp_sl_block_for_tag(t)
    try:
        d = float(block.get("sl_doom", 0) or 0)
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return None
    lev = float(getattr(trade, "leverage", None) or 1.0)
    lev = max(1.0, lev)
    if is_futures:
        return -(d / lev)
    return -d


def tag_takeprofit_profit_pct(tag: str) -> float | None:
    """ROI fraction for ``custom_exit`` TP (× max(1, leverage) in strategy). ``None`` = no tag TP."""
    t = (tag or "").strip()
    if not t.startswith("BTC-0.1-R"):
        return None
    block = _tp_sl_block_for_tag(t)
    if t == TAG_R03:
        if "tp_profit_pct" not in block or block.get("tp_profit_pct") is None:
            return r03_scalp_tp_profit_pct()
        try:
            v = float(block["tp_profit_pct"])
            return max(0.0005, min(v, 0.15))
        except (TypeError, ValueError):
            return r03_scalp_tp_profit_pct()
    if block.get("tp_profit_pct") is None:
        return None
    try:
        v = float(block["tp_profit_pct"])
        return max(0.0005, min(v, 0.25))
    except (TypeError, ValueError):
        return None
