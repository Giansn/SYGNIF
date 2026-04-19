"""
Shared **live 5m predict + sizing** helpers for ``btc_predict_asap_order`` and
``btc_predict_protocol_loop`` (Bybit demo REST paths).

**Modeled “profit” / edge (research only, not P/L guarantees):**

- ``consensus_mid_and_close`` — blend of RF/XGB ``next_mean`` vs ``current_close``.
- ``modeled_edge_usdt_per_btc`` — favorable USDT move per 1 BTC at that mid vs close (long vs short).
- ``modeled_profit_usdt_at_qty`` — ``edge_per_btc * qty`` (same linear scale as the open-edge gate).
- ``min_predict_edge_profit_usdt`` + gate in ``btc_predict_protocol_loop`` — skip flat opens when modeled
  USDT edge is below floor (fees/slippage not modeled).
- ``relative_modeled_edge_pct`` — favorable move as %% of spot close (telemetry / risk context).
- ``per_trade_fee_usdt`` / ``open_modeled_edge_floor_usdt`` — ``SYGNIF_PREDICT_PER_TRADE_COST_USDT`` (default **1**)
  plus optional ``SYGNIF_PREDICT_EDGE_PLUS_FEE`` (default on) added to ``min_predict_edge_profit_usdt()`` for flat opens.
- ``effective_open_edge_floor_usdt(move_pct)`` — optional **vol relax**: when RF/XGB mean ``move_pct`` is high, scales the
  modeled-edge floor down (``SYGNIF_PREDICT_EDGE_VOL_RELAX`` and ``SYGNIF_PREDICT_EDGE_VOL_*`` knobs).
- ``linear_leg_unrealised_usdt`` — parse ``unrealisedPnl`` for the venue leg (``positionIdx`` when hedge).
- **Heavy91 failure swing (Pine-style):** ``run_live_fit`` attaches ``predictions.failure_swing_heavy91`` from
  the same 5m OHLC tape. ``SYGNIF_PREDICT_FAILURE_SWING_HEAVY91_ENTRIES`` — when on, ``decide_side`` may emit
  long/short from that counter-break logic after the ML stack. Tunables: ``SYGNIF_FS_HEAVY91_PERIOD`` (default **84**),
  ``SYGNIF_FS_HEAVY91_EMA`` (**120**), ``SYGNIF_FS_HEAVY91_VOL_THRESHOLD`` (**5** = 5%% distance open vs EMA).
- **Panic → reverse (predict loop):** ``SYGNIF_PREDICT_FAILURE_SWING_PANIC_REVERSE`` — when ML target is ``None``
  (would flatten if ``hold_on_no_edge`` is off) but Heavy91 signals a counter-trade, set target to flip instead
  of cashing out; Swarm gate is re-evaluated when enabled.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from btc_analysis_order_signal import r01_bearish_from_training
from btc_predict_live import fetch_linear_5m_klines
from btc_predict_live import fit_predict_live


def env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = os.environ.get(name, str(default)).strip() or str(default)
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip() or str(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def parse_usdt_available(resp: dict[str, Any]) -> float | None:
    if resp.get("retCode") != 0:
        return None
    lst = (resp.get("result") or {}).get("list") or []
    if not lst:
        return None
    coins = lst[0].get("coin") or []
    for c in coins:
        if str(c.get("coin", "")).upper() != "USDT":
            continue
        for key in ("availableToWithdraw", "availableBalance", "transferBalance"):
            raw = c.get(key)
            if raw is not None and str(raw).strip() != "":
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    pass
        try:
            return float(c.get("walletBalance") or 0.0)
        except (TypeError, ValueError):
            return None
    return None


def parse_usdt_equity(resp: dict[str, Any]) -> float | None:
    """Best-effort USDT **equity** from Bybit ``wallet-balance`` (UNIFIED) coin row."""
    if resp.get("retCode") != 0:
        return None
    lst = (resp.get("result") or {}).get("list") or []
    if not lst:
        return None
    coins = lst[0].get("coin") or []
    for c in coins:
        if str(c.get("coin", "")).upper() != "USDT":
            continue
        for key in ("equity", "totalEquity", "usdValue"):
            raw = c.get(key)
            if raw is not None and str(raw).strip() != "":
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    pass
    return None


def consensus_mid_and_close(out: dict[str, Any]) -> tuple[float, float]:
    """Return ``(mid_next_mean, current_close)`` from RF/XGB tree predictions (same blend as TP/SL helpers)."""
    close = 0.0
    try:
        close = float(out.get("current_close") or 0.0)
    except (TypeError, ValueError):
        pass
    pr = out.get("predictions") if isinstance(out.get("predictions"), dict) else {}
    rf = pr.get("random_forest") if isinstance(pr.get("random_forest"), dict) else {}
    xg = pr.get("xgboost") if isinstance(pr.get("xgboost"), dict) else {}
    try:
        rf_m = float(rf.get("next_mean") or 0.0)
    except (TypeError, ValueError):
        rf_m = 0.0
    try:
        xg_m = float(xg.get("next_mean") or 0.0)
    except (TypeError, ValueError):
        xg_m = 0.0
    if rf_m > 0 and xg_m > 0:
        mid = (rf_m + xg_m) / 2.0
    elif rf_m > 0:
        mid = rf_m
    elif xg_m > 0:
        mid = xg_m
    else:
        mid = close
    return mid, close


def modeled_edge_usdt_per_btc(out: dict[str, Any], side: str) -> float:
    """
    Rough favorable USDT price move per **1 BTC** notional at consensus ``next_mean`` vs ``current_close``.

    Long: ``max(0, mid - close)``; short: ``max(0, close - mid)``. Linear BTCUSDT approximation; no fees.
    """
    mid, cl = consensus_mid_and_close(out)
    if cl <= 0:
        return 0.0
    s = (side or "").strip().lower()
    if s == "long":
        return max(0.0, mid - cl)
    if s == "short":
        return max(0.0, cl - mid)
    return 0.0


def per_trade_fee_usdt() -> float:
    """
    Assumed **round-trip** venue cost per full open+close in USDT (spread/fees/taker model).

    Used only for gates / hold-until-profit defaults — not fetched from the exchange.
    """
    return max(0.0, env_float("SYGNIF_PREDICT_PER_TRADE_COST_USDT", 1.0))


def _edge_gate_includes_fee() -> bool:
    raw = (os.environ.get("SYGNIF_PREDICT_EDGE_PLUS_FEE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def open_modeled_edge_floor_usdt() -> float:
    """
    Minimum **modeled** favorable USDT P/L required before a flat **open**.

    ``min_predict_edge_profit_usdt()`` plus ``per_trade_fee_usdt()`` when
    ``SYGNIF_PREDICT_EDGE_PLUS_FEE`` is on (default), so a $1/trade assumption is
    not eaten by the fee model.
    """
    base = min_predict_edge_profit_usdt()
    if _edge_gate_includes_fee():
        return base + per_trade_fee_usdt()
    return base


def effective_open_edge_floor_usdt(move_pct: float) -> float:
    """
    Modeled-edge **floor** for flat opens, optionally reduced when predicted move
    (``move_pct`` from ``move_pct_and_close``, %% of close) is high — vol proxy for
    “larger implied next-bar move → relax min edge”.

    Off when ``SYGNIF_PREDICT_EDGE_VOL_RELAX`` is ``0``/``false`` or when base floor is ``0``.
    """
    base = open_modeled_edge_floor_usdt()
    if base <= 0.0:
        return 0.0
    raw = (os.environ.get("SYGNIF_PREDICT_EDGE_VOL_RELAX") or "1").strip().lower()
    if raw in ("0", "false", "no", "off", ""):
        return base
    lo = max(0.0, env_float("SYGNIF_PREDICT_EDGE_VOL_REF_LO_PCT", 0.05))
    hi = max(lo + 1e-6, env_float("SYGNIF_PREDICT_EDGE_VOL_REF_HI_PCT", 0.35))
    max_relax = max(0.0, min(0.95, env_float("SYGNIF_PREDICT_EDGE_VOL_RELAX_MAX", 0.5)))
    min_scale = max(0.05, min(1.0, env_float("SYGNIF_PREDICT_EDGE_VOL_RELAX_MIN_FACTOR", 0.25)))
    try:
        m = float(move_pct)
    except (TypeError, ValueError):
        m = 0.0
    t = (m - lo) / (hi - lo)
    if t <= 0.0:
        return base
    t = min(1.0, t)
    factor = max(min_scale, 1.0 - max_relax * t)
    return base * factor


def min_predict_edge_profit_usdt() -> float:
    """
    Minimum modeled edge (USDT) required before a **flat** market open.

    - If ``SYGNIF_PREDICT_MIN_EDGE_PROFIT_USDT`` is set in the environment (any value, including ``0``),
      that numeric value is used (``0`` = gate **off**).
    - If unset: use ``SYGNIF_SWARM_TP_USDT_TARGET`` when > 0 (aligns e.g. **50** USDT TP target with a
      **50** USDT modeled-edge floor), else ``0`` (off).
    """
    raw = os.environ.get("SYGNIF_PREDICT_MIN_EDGE_PROFIT_USDT")
    if raw is not None and str(raw).strip() != "":
        try:
            return max(0.0, float(str(raw).strip()))
        except (TypeError, ValueError):
            return 0.0
    return max(0.0, env_float("SYGNIF_SWARM_TP_USDT_TARGET", 0.0))


def modeled_profit_usdt_at_qty(out: dict[str, Any], side: str, qty_btc: float) -> float:
    """
    Modeled favorable USDT P/L for ``qty_btc`` at the consensus mid vs close (same units as
    ``modeled_edge_usdt_per_btc``). Does **not** include fees, funding, or adverse selection.
    """
    try:
        q = float(qty_btc)
    except (TypeError, ValueError):
        return 0.0
    if q <= 0.0:
        return 0.0
    return modeled_edge_usdt_per_btc(out, side) * q


def relative_modeled_edge_pct(out: dict[str, Any], side: str) -> float:
    """
    Favorable price move implied by ``consensus_mid_and_close`` vs ``current_close``, as a
    percentage of close (long: ``max(0, mid-close)/close*100``; short: symmetric).
    """
    mid, cl = consensus_mid_and_close(out)
    if cl <= 0:
        return 0.0
    s = (side or "").strip().lower()
    if s == "long":
        return max(0.0, (mid - cl) / cl * 100.0)
    if s == "short":
        return max(0.0, (cl - mid) / cl * 100.0)
    return 0.0


def move_pct_and_close(out: dict[str, Any]) -> tuple[float, float]:
    close = float(out.get("current_close") or 0.0)
    preds = out.get("predictions") or {}
    rf = preds.get("random_forest") or {}
    xg = preds.get("xgboost") or {}
    try:
        d1 = abs(float(rf.get("delta") or 0.0))
        d2 = abs(float(xg.get("delta") or 0.0))
    except (TypeError, ValueError):
        d1, d2 = 0.0, 0.0
    mean_abs = (d1 + d2) / 2.0
    if close <= 0:
        return 0.0, close
    return (mean_abs / close) * 100.0, close


def leverage_from_move_pct(move_pct: float) -> tuple[int, float]:
    lo = env_int("BYBIT_DEMO_ORDER_MIN_LEVERAGE", 5, lo=1, hi=125)
    hi = env_int("BYBIT_DEMO_ORDER_MAX_LEVERAGE", 25, lo=1, hi=125)
    if lo > hi:
        lo, hi = hi, lo
    floor_p = max(1e-6, env_float("ASAP_MOVE_LEV_FLOOR_PCT", 0.03))
    cap_p = max(floor_p + 1e-6, env_float("ASAP_MOVE_LEV_CAP_PCT", 1.0))
    t = (move_pct - floor_p) / (cap_p - floor_p)
    t = max(0.0, min(1.0, t))
    lev = int(round(hi - t * (hi - lo)))
    return max(lo, min(hi, lev)), t


def decide_side(out: dict[str, Any], training: dict[str, Any] | None) -> tuple[str | None, str]:
    preds = out.get("predictions") or {}
    rf = preds.get("random_forest") or {}
    xg = preds.get("xgboost") or {}
    dlr = preds.get("direction_logistic") or {}
    try:
        d_rf = float(rf.get("delta") or 0.0)
        d_xg = float(xg.get("delta") or 0.0)
    except (TypeError, ValueError):
        d_rf, d_xg = 0.0, 0.0
    label = str(dlr.get("label", "") or "").strip().upper()
    try:
        conf = float(dlr.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0

    v = 0
    if d_rf > 0:
        v += 1
    elif d_rf < 0:
        v -= 1
    if d_xg > 0:
        v += 1
    elif d_xg < 0:
        v -= 1
    thr = env_float("ASAP_LOGREG_VOTE_CONF", 52.0)
    if label == "UP" and conf >= thr:
        v += 1
    if label == "DOWN" and conf >= thr:
        v -= 1

    bear = r01_bearish_from_training(training if isinstance(training, dict) else {})
    if (os.environ.get("SYGNIF_PREDICT_BYPASS_R01_LONG_BLOCK") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        bear = False

    if v > 0:
        if bear:
            return None, "long blocked (R01 bearish stack)"
        return "long", f"vote={v} RFΔ={d_rf:.2f} XGBΔ={d_xg:.2f} logreg={label}/{conf:.1f}%"
    if v < 0:
        return "short", f"vote={v} RFΔ={d_rf:.2f} XGBΔ={d_xg:.2f} logreg={label}/{conf:.1f}%"

    brk = env_float("ASAP_LOGREG_TIEBREAK_CONF", 58.0)
    if label == "UP" and conf >= brk:
        if bear:
            return None, "tiebreak long blocked (R01 bearish stack)"
        return "long", f"tiebreak logreg UP {conf:.1f}%"
    if label == "DOWN" and conf >= brk:
        return "short", f"tiebreak logreg DOWN {conf:.1f}%"

    sf = preds.get("swing_failure") if isinstance(preds.get("swing_failure"), dict) else {}
    if (os.environ.get("SYGNIF_PREDICT_SWING_FAILURE_ENTRIES") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        if sf.get("sf_long") and not bear:
            return "long", "swing_failure_sf_long"
        if sf.get("sf_short"):
            return "short", "swing_failure_sf_short"

    fs91 = preds.get("failure_swing_heavy91") if isinstance(preds.get("failure_swing_heavy91"), dict) else {}
    if (os.environ.get("SYGNIF_PREDICT_FAILURE_SWING_HEAVY91_ENTRIES") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        if fs91.get("ok") and fs91.get("entry_long") and not bear:
            return "long", "failure_swing_heavy91_long"
        if fs91.get("ok") and fs91.get("entry_short"):
            return "short", "failure_swing_heavy91_short"

    return None, f"no edge (vote=0 logreg={label}/{conf:.1f}%)"


def qty_btc(
    *,
    free_usdt: float,
    close: float,
    t_move: float,
    logreg_conf: float,
) -> tuple[str, float]:
    min_q = max(1e-9, env_float("BYBIT_DEMO_ORDER_MIN_QTY", 0.001))
    max_q = max(1e-9, env_float("BYBIT_DEMO_ORDER_MAX_QTY", 0.08))
    if max_q < min_q:
        max_q = min_q
    base_frac = max(1e-6, min(1.0, env_float("BYBIT_DEMO_ORDER_STAKE_FRAC", 0.002)))
    move_boost = 0.45 + 0.55 * t_move
    conf_floor = env_float("ASAP_LOGREG_VOTE_CONF", 52.0)
    span = max(1.0, 100.0 - conf_floor)
    conf_t = max(0.0, min(1.0, (logreg_conf - conf_floor) / span))
    conv = 0.55 + 0.45 * conf_t
    eff = base_frac * move_boost * conv
    eff = min(eff, 0.25)
    notional = free_usdt * eff
    if close <= 0:
        return "", eff
    raw_qty = notional / close
    raw_qty = max(min_q, min(max_q, raw_qty))
    step = 0.001
    q = math.floor(raw_qty / step) * step
    if q + 1e-12 < min_q:
        return "", eff
    s = f"{q:.6f}".rstrip("0").rstrip(".")
    return (s if s else str(min_q)), eff


def run_live_fit(
    *,
    symbol: str,
    kline_limit: int,
    window: int,
    data_dir: str,
    rf_trees: int,
    xgb_estimators: int,
    write_json_path: str | None,
) -> tuple[bool, str, dict[str, Any], float]:
    """
    Fetch klines, ``fit_predict_live``, return (allow_buy, enhanced, out_dict, predict_ms).
    """
    import time

    t0 = time.perf_counter()
    df = fetch_linear_5m_klines(symbol, limit=min(1000, max(120, kline_limit)))
    allow_buy, enhanced, out = fit_predict_live(
        df,
        window=max(3, window),
        data_dir=data_dir,
        rf_trees=rf_trees,
        xgb_estimators=xgb_estimators,
        write_json_path=write_json_path,
        linear_symbol=symbol,
    )
    try:
        from live_trading_calibration import apply_direction_logistic_calibration  # noqa: PLC0415

        apply_direction_logistic_calibration(out, repo_root=Path(__file__).resolve().parents[1])
    except Exception:
        pass
    try:
        from btc_failure_swing_heavy91 import failure_swing_heavy91_snapshot  # noqa: PLC0415

        _fs91 = failure_swing_heavy91_snapshot(df)
        _preds = out.setdefault("predictions", {})
        if isinstance(_preds, dict):
            _preds["failure_swing_heavy91"] = _fs91
    except Exception:
        pass
    try:
        from btc_forecast_eval import append_forecast_pending  # noqa: PLC0415

        append_forecast_pending(out, symbol=symbol)
    except Exception:
        pass
    pred_ms = (time.perf_counter() - t0) * 1000.0
    return allow_buy, enhanced, out, pred_ms


def parse_linear_position(
    resp: dict[str, Any],
    symbol: str,
    position_idx: int | None = None,
) -> tuple[str | None, float, str]:
    """
    Return (side: long|short|None, abs size, raw size string for close orders).

    ``position_idx`` **1** / **2** = hedge long / short leg (filters ``positionIdx`` on the row).
    ``None`` / **0** = largest non-zero size for the symbol (legacy one-way / mixed).
    """
    sym = symbol.upper().strip()
    if resp.get("retCode") != 0:
        return None, 0.0, ""
    best_raw = ""
    best_sz = 0.0
    best_side: str | None = None
    for row in (resp.get("result") or {}).get("list") or []:
        if str(row.get("symbol", "")).upper() != sym:
            continue
        if position_idx is not None and int(position_idx) in (1, 2):
            try:
                if int(row.get("positionIdx") or 0) != int(position_idx):
                    continue
            except (TypeError, ValueError):
                continue
        raw_sz = str(row.get("size") or "").strip()
        try:
            sz = float(raw_sz)
        except (TypeError, ValueError):
            continue
        if abs(sz) < 1e-12:
            continue
        side_api = str(row.get("side") or "").strip().lower()
        if side_api == "buy":
            s = "long"
        elif side_api == "sell":
            s = "short"
        else:
            continue
        if abs(sz) > best_sz:
            best_sz = abs(sz)
            best_side = s
            best_raw = raw_sz
    return best_side, best_sz, best_raw


def linear_leg_unrealised_usdt(
    resp: dict[str, Any],
    symbol: str,
    position_idx: int | None = None,
) -> float | None:
    """Best matching leg's ``unrealisedPnl`` (USDT) or ``None`` if flat / missing."""
    sym = symbol.upper().strip()
    if resp.get("retCode") != 0:
        return None
    best_row: dict[str, Any] | None = None
    best_sz = 0.0
    for row in (resp.get("result") or {}).get("list") or []:
        if str(row.get("symbol", "")).upper() != sym:
            continue
        if position_idx is not None and int(position_idx) in (1, 2):
            try:
                if int(row.get("positionIdx") or 0) != int(position_idx):
                    continue
            except (TypeError, ValueError):
                continue
        raw_sz = str(row.get("size") or "").strip()
        try:
            sz = float(raw_sz)
        except (TypeError, ValueError):
            continue
        if abs(sz) < 1e-12:
            continue
        side_api = str(row.get("side") or "").strip().lower()
        if side_api not in ("buy", "sell"):
            continue
        if abs(sz) > best_sz:
            best_sz = abs(sz)
            best_row = row
    if best_row is None:
        return None
    try:
        return float(best_row.get("unrealisedPnl") or 0.0)
    except (TypeError, ValueError):
        return None


def logreg_confidence(out: dict[str, Any]) -> float:
    dlr = (out.get("predictions") or {}).get("direction_logistic") or {}
    try:
        return float(dlr.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def logreg_label(out: dict[str, Any]) -> str:
    """``UP`` / ``DOWN`` from ``predictions.direction_logistic`` (empty if missing)."""
    dlr = (out.get("predictions") or {}).get("direction_logistic") or {}
    return str(dlr.get("label") or "").strip().upper()


def logreg_aligns_target(out: dict[str, Any], target: str | None) -> bool:
    """True when LogReg direction matches a long/short **target** (``UP``→long, ``DOWN``→short)."""
    if target not in ("long", "short"):
        return False
    lab = logreg_label(out)
    if target == "long":
        return lab == "UP"
    return lab == "DOWN"
