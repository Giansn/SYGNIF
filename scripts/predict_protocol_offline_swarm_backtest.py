#!/usr/bin/env python3
"""
**Offline** replay: same **5m fit + decide_side** stack as ``btc_predict_protocol_loop``, plus optional
**Swarm / fusion gates** (``swarm_fusion_allows``) with a **simulated btc_future vote** = current
simulated position (flat=0, long=+1, short=-1) — mirrors live ``fusion.vote_btc_future``.

Optional **TP/SL monitoring** between refit steps using each bar's **High/Low** (open at signal close;
first touch SL or TP wins intrabar pessimistically: **SL before TP** on the same bar for a long).

**Hivemind (``sources.hm``):** default **synthetic** ``offline_hm_vote``; or ``--offline-hm-source demo_once``
/ ``demo_refresh`` to map **current** Bybit **demo** linear position (``BYBIT_DEMO_*``) to ``hm`` vote — **not**
a true per-bar history replay.

**Not** exchange-grade: no fees, slippage, funding; **Nautilus bias** is read from a **static** JSON file
(default ``btc_specialist/data/nautilus_strategy_signal.json``) for all bars — same limitation as
``predict_protocol_backtest_pnl`` using a fixed ``training_channel_output.json``.

**Parameter search:** ``--grid-mean-long`` sweeps ``SWARM_ORDER_MIN_MEAN_LONG``; pick best ``pnl_usdt_approx``.

Examples::

  cd ~/SYGNIF && python3 scripts/predict_protocol_offline_swarm_backtest.py --hours 48 --step 4 --json

  python3 scripts/predict_protocol_offline_swarm_backtest.py --apply-swarm-gate --hours 72 --step 4 \\
    --min-mean-long 0.15 --json

  python3 scripts/predict_protocol_offline_swarm_backtest.py --apply-swarm-gate --tp-pct 0.6 --sl-pct 0.35 \\
    --grid-mean-long --hours 48 --step 4 --json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", module="sklearn")

_REPO = Path(__file__).resolve().parents[1]
_PA = _REPO / "prediction_agent"
_DATA = _REPO / "finance_agent" / "btc_specialist" / "data"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        o = json.loads(path.read_text(encoding="utf-8"))
        return o if isinstance(o, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def fusion_label_from_sum(fused: int) -> str:
    if fused >= 2:
        return "strong_long"
    if fused <= -2:
        return "strong_short"
    if fused == 1:
        return "lean_long"
    if fused == -1:
        return "lean_short"
    return "neutral"


def build_offline_swarm_and_fusion(
    out: dict[str, Any],
    *,
    nautilus: dict[str, Any],
    sim_bf_vote: int,
    hm_vote: int = 0,
    hm_detail: str = "offline_synth",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Swarm + fusion_doc for ``swarm_fusion_allows`` (includes ``btc_prediction`` for ML gates).

    ``hm_detail`` documents ``sources.hm`` (e.g. ``offline_synth``, ``bybit_demo_once``, ``bybit_demo_refresh``).
    """
    sys.path.insert(0, str(_PA))
    from nautilus_protocol_fusion import _vote_ml_consensus  # noqa: PLC0415
    from nautilus_protocol_fusion import _vote_nautilus_bias  # noqa: PLC0415

    vn, _nl = _vote_nautilus_bias(nautilus)
    vm, _ml = _vote_ml_consensus(out)
    v_bf = int(max(-1, min(1, sim_bf_vote)))
    fused = vn + vm + v_bf
    flab = fusion_label_from_sum(fused)
    fusion_doc: dict[str, Any] = {
        "btc_prediction": out,
        "nautilus_sidecar": nautilus,
        "fusion": {
            "vote_nautilus": vn,
            "vote_ml": vm,
            "vote_btc_future": v_bf,
            "sum": fused,
            "label": flab,
            "nautilus_detail": _nl,
            "ml_detail": _ml,
            "btc_future_detail": "sim",
        },
    }
    swarm_mean = max(-1.0, min(1.0, fused / 3.0))
    conflict = bool(
        vn != 0
        and vm != 0
        and vn * vm < 0
        or (vm != 0 and v_bf != 0 and vm * v_bf < 0)
        or (vn != 0 and v_bf != 0 and vn * v_bf < 0)
    )
    if swarm_mean > 0.25:
        slabel = "SWARM_BULL"
    elif swarm_mean < -0.25:
        slabel = "SWARM_BEAR"
    else:
        slabel = "SWARM_MIXED"
    hm_v = int(max(-1, min(1, hm_vote)))
    hm_d = (hm_detail or "offline_synth").strip() or "offline_synth"
    swarm: dict[str, Any] = {
        "swarm_mean": swarm_mean,
        "swarm_label": slabel,
        "swarm_conflict": conflict,
        "sources": {
            "bf": {"vote": v_bf, "detail": "sim_btc_future"},
            "ml": {"vote": vm, "detail": "offline_ml"},
            "hm": {"vote": hm_v, "detail": hm_d},
        },
        "btc_future": {"enabled": True, "ok": True, "position": {"flat": v_bf == 0}},
    }
    return swarm, fusion_doc


def _qty_from_notional(notional: float, close: float) -> float:
    if close <= 0 or notional <= 0:
        return 0.0
    raw = notional / close
    step = 0.001
    q = math.floor(raw / step) * step
    return q if q >= step else 0.0


def _first_touch_exit_long(
    entry: float,
    df_path: Any,
    *,
    tp_px: float,
    sl_px: float,
) -> tuple[float, str]:
    """Walk forward bar-by-bar; long: SL before TP on same bar (conservative)."""
    for _, row in df_path.iterrows():
        low = float(row["Low"])
        high = float(row["High"])
        if low <= sl_px:
            return sl_px, "sl"
        if high >= tp_px:
            return tp_px, "tp"
    return float(df_path.iloc[-1]["Close"]), "hold"


def _first_touch_exit_short(
    entry: float,
    df_path: Any,
    *,
    tp_px: float,
    sl_px: float,
) -> tuple[float, str]:
    for _, row in df_path.iterrows():
        low = float(row["Low"])
        high = float(row["High"])
        if high >= sl_px:
            return sl_px, "sl"
        if low <= tp_px:
            return tp_px, "tp"
    return float(df_path.iloc[-1]["Close"]), "hold"


def normalize_sim_state(state: dict[str, Any] | None) -> dict[str, Any]:
    """Clamp ``pos`` / ``qty`` / ``entry_px`` / ``sim_bf`` for ``run_simulation(..., initial_sim_state=...)``."""
    if not state:
        return {"pos": 0, "qty": 0.0, "entry_px": 0.0, "sim_bf": 0}
    pos = int(state.get("pos") or 0)
    if pos not in (-1, 0, 1):
        pos = 0
    qty = max(0.0, float(state.get("qty") or 0.0))
    entry_px = max(0.0, float(state.get("entry_px") or 0.0))
    sim_bf = int(state.get("sim_bf") or 0)
    if sim_bf not in (-1, 0, 1):
        sim_bf = 0
    if pos == 0:
        return {"pos": 0, "qty": 0.0, "entry_px": 0.0, "sim_bf": 0}
    if sim_bf == 0:
        sim_bf = 1 if pos > 0 else -1
    return {"pos": pos, "qty": qty, "entry_px": entry_px, "sim_bf": sim_bf}


@contextmanager
def _patched_gate_env(overrides: dict[str, str]):
    saved: dict[str, str | None] = {}
    try:
        for k, v in overrides.items():
            saved[k] = os.environ.get(k)
            os.environ[k] = v
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def default_eval_bar_bounds(*, n: int, hours: float) -> tuple[int, int]:
    """Trailing window as bar indices: ``[start, end)`` (end exclusive), same semantics as ``run_simulation``."""
    bars_eval = int(math.ceil(max(1.0, float(hours)) * 12))
    es = max(0, int(n) - bars_eval)
    ee = max(es + 1, int(n) - 1)
    return es, ee


def walk_forward_bar_slices(eval_bar_start: int, eval_bar_end: int, folds: int) -> list[tuple[int, int]]:
    """Split ``[eval_bar_start, eval_bar_end)`` into ``folds`` contiguous half-open slices (equal width, remainder in last)."""
    folds = max(2, int(folds))
    es, ee = int(eval_bar_start), int(eval_bar_end)
    span = ee - es
    if span < folds:
        raise ValueError(f"walk_forward_bar_slices: span={span} < folds={folds}")
    out: list[tuple[int, int]] = []
    cur = es
    for f in range(folds):
        nxt = es + (span * (f + 1)) // folds
        if f == folds - 1:
            nxt = ee
        out.append((cur, nxt))
        cur = nxt
    return out


def build_default_gate_env(
    *,
    min_mean_long: float,
    max_mean_short: float,
    block_conflict: bool,
    fusion_align: bool,
) -> dict[str, str]:
    return {
        "SWARM_ORDER_MIN_MEAN_LONG": str(min_mean_long),
        "SWARM_ORDER_MAX_MEAN_SHORT": str(max_mean_short),
        "SWARM_ORDER_BLOCK_CONFLICT": "1" if block_conflict else "0",
        "SWARM_ORDER_REQUIRE_FUSION_ALIGN": "1" if fusion_align else "0",
        "SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE": os.environ.get("SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE", "0"),
        "SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS": os.environ.get("SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS", "1"),
        "SWARM_ORDER_REQUIRE_HIVEMIND_VOTE": os.environ.get("SWARM_ORDER_REQUIRE_HIVEMIND_VOTE", "0"),
        "SWARM_ORDER_FUSION_REQUIRE_STRONG": os.environ.get("SWARM_ORDER_FUSION_REQUIRE_STRONG", "0"),
        "SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY": os.environ.get(
            "SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY", "0"
        ),
        "SWARM_ORDER_ML_LOGREG_MIN_CONF": os.environ.get("SWARM_ORDER_ML_LOGREG_MIN_CONF", "0"),
    }


def run_simulation(
    *,
    hours: float,
    step: int,
    kline_limit: int,
    window: int,
    rf_trees: int,
    xgb_estimators: int,
    notional: float,
    leverage: float,
    margin_usdt: float | None,
    hold_on_no_edge: bool,
    training_path: Path,
    nautilus_path: Path,
    apply_swarm_gate: bool,
    gate_env: dict[str, str],
    tp_pct: float | None,
    sl_pct: float | None,
    symbol: str,
    patch_nautilus_generated_utc: bool = False,
    offline_hm_vote: int = 0,
    offline_hm_source: str = "synthetic",
    offline_hm_symbol: str | None = None,
    eval_bar_start: int | None = None,
    eval_bar_end: int | None = None,
    initial_sim_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sys.path.insert(0, str(_PA))
    sys.path.insert(0, str(_REPO / "finance_agent"))
    from btc_asap_predict_core import decide_side  # noqa: E402
    from btc_predict_live import fetch_linear_5m_klines  # noqa: E402
    from btc_predict_live import fit_predict_live  # noqa: E402
    from swarm_order_gate import swarm_fusion_allows  # noqa: E402

    training = _load_json(training_path)
    nautilus = dict(_load_json(nautilus_path) or {})
    if patch_nautilus_generated_utc:
        nautilus["generated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lim = max(200, min(1000, int(kline_limit)))
    step = max(1, int(step))
    hours = max(1.0, float(hours))
    bars_eval = int(math.ceil(hours * 12))

    df = fetch_linear_5m_klines(symbol, limit=lim)
    n = len(df)
    if n < bars_eval + 80:
        return {"ok": False, "error": "not_enough_klines", "n": n}

    if (eval_bar_start is None) ^ (eval_bar_end is None):
        return {"ok": False, "error": "eval_bar_start_and_end_both_required_or_both_omitted"}
    if eval_bar_start is not None and eval_bar_end is not None:
        es = int(eval_bar_start)
        ee = int(eval_bar_end)
        if ee <= es or es < 0 or ee > n - 1:
            return {
                "ok": False,
                "error": "eval_bar_range_invalid",
                "eval_bar_start": es,
                "eval_bar_end": ee,
                "n": n,
            }
    else:
        es = max(0, n - bars_eval)
        ee = n - 1

    min_fit = 120
    first_i = max(min_fit, es)
    idxs = list(range(first_i, n - 1, step))
    if not idxs:
        return {"ok": False, "error": "empty_idxs"}
    if idxs[-1] != n - 2:
        idxs.append(n - 2)
    idxs = [i for i in idxs if es <= i < ee]
    if not idxs:
        return {"ok": False, "error": "empty_idxs_after_eval_slice", "es": es, "ee": ee}

    hm_src = (offline_hm_source or "synthetic").strip().lower()
    if hm_src not in ("synthetic", "demo_once", "demo_refresh"):
        return {"ok": False, "error": "offline_hm_source_invalid", "offline_hm_source": hm_src}
    hm_sym = (offline_hm_symbol or symbol).upper().strip() or "BTCUSDT"

    _fdemo_hm: Any = None
    _hmvote_from_demo: Any = None
    if hm_src in ("demo_once", "demo_refresh"):
        has_demo = bool(
            os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
            and os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
        )
        if not has_demo:
            return {"ok": False, "error": "offline_hm_demo_requires_BYBIT_DEMO_keys"}
        from swarm_knowledge import fetch_demo_linear_position_list as _fdemo_hm  # noqa: PLC0415
        from swarm_knowledge import hivemind_vote_from_bybit_demo_position as _hmvote_from_demo  # noqa: PLC0415

    fixed_hm_vote = 0
    fixed_hm_detail = "offline_synth"
    if hm_src == "demo_once":
        resp0 = _fdemo_hm(hm_sym, cache_sec=0, bypass_cache=True)
        fv, dsub = _hmvote_from_demo(resp0)
        fixed_hm_vote = int(fv)
        fixed_hm_detail = f"bybit_demo_once:{dsub}"

    last_hm: dict[str, Any] = {}

    st0 = normalize_sim_state(initial_sim_state)
    pos = int(st0["pos"])
    qty = float(st0["qty"])
    entry_px = float(st0["entry_px"])
    sim_bf = int(st0["sim_bf"])
    pnl = 0.0
    closes = df["Close"].astype(float)
    times = df["Date"]
    lev = max(1.0, float(leverage))
    margin = float(margin_usdt) if margin_usdt is not None else notional / lev
    segments: list[dict[str, Any]] = []

    use_tpsl = tp_pct is not None and sl_pct is not None and tp_pct > 0 and sl_pct > 0

    for k in range(len(idxs) - 1):
        i = idxs[k]
        i2 = idxs[k + 1]
        sub = df.iloc[: i + 1].copy()
        try:
            _a, _e, out = fit_predict_live(
                sub,
                window=window,
                data_dir=str(_DATA),
                rf_trees=rf_trees,
                xgb_estimators=xgb_estimators,
                write_json_path=None,
            )
        except ValueError as exc:
            segments.append({"skip": str(exc), "from_idx": i})
            continue

        target, why = decide_side(out, training)
        c_i = float(closes.iloc[i])

        if hm_src == "synthetic":
            hm_v = int(max(-1, min(1, offline_hm_vote)))
            hm_d = "offline_synth"
        elif hm_src == "demo_once":
            hm_v = fixed_hm_vote
            hm_d = fixed_hm_detail
        else:
            resp_h = _fdemo_hm(hm_sym, cache_sec=0, bypass_cache=True)
            hv, dsub = _hmvote_from_demo(resp_h)
            hm_v = int(hv)
            hm_d = f"bybit_demo_refresh:{dsub}"
        last_hm = {"vote": hm_v, "detail": hm_d}

        if apply_swarm_gate and target in ("long", "short"):
            swarm, fusion_doc = build_offline_swarm_and_fusion(
                out,
                nautilus=nautilus,
                sim_bf_vote=sim_bf,
                hm_vote=hm_v,
                hm_detail=hm_d,
            )
            with _patched_gate_env(gate_env):
                ok_gate, gr = swarm_fusion_allows(
                    target=target, swarm=swarm, fusion_doc=fusion_doc, predict_out=out
                )
            if not ok_gate:
                target = None
                why = f"gated:{gr}|{why}"[:200]

        if use_tpsl and pos != 0 and qty > 0:
            path_df = df.iloc[i + 1 : i2 + 1]
            if len(path_df) == 0:
                exit_px, tag = c_i, "noop"
            elif pos > 0:
                tp_px = entry_px * (1.0 + tp_pct / 100.0)
                sl_px = entry_px * (1.0 - sl_pct / 100.0)
                exit_px, tag = _first_touch_exit_long(entry_px, path_df, tp_px=tp_px, sl_px=sl_px)
            else:
                tp_px = entry_px * (1.0 - tp_pct / 100.0)
                sl_px = entry_px * (1.0 + sl_pct / 100.0)
                exit_px, tag = _first_touch_exit_short(entry_px, path_df, tp_px=tp_px, sl_px=sl_px)
            dpx = exit_px - entry_px
            move_pnl = pos * qty * dpx
            pnl += move_pnl
            segments.append(
                {
                    "from_idx": i,
                    "to_idx": i2,
                    "pos": pos,
                    "qty": qty,
                    "tpsl_exit": tag,
                    "d_px": round(dpx, 4),
                    "pnl_usdt": round(move_pnl, 4),
                    "target_raw": target,
                    "reason": why[:120],
                }
            )
            if tag in ("sl", "tp"):
                pos = 0
                qty = 0.0
                sim_bf = 0
                entry_px = 0.0
            continue

        if not hold_on_no_edge and target is None:
            pos = 0
            qty = 0.0
            sim_bf = 0
            entry_px = 0.0
        elif target == "long":
            if pos < 0 and qty > 0:
                pnl += pos * qty * (c_i - entry_px)
            if pos <= 0:
                pos = 1
                qty = _qty_from_notional(notional, c_i)
                entry_px = c_i
                sim_bf = 1
        elif target == "short":
            if pos > 0 and qty > 0:
                pnl += pos * qty * (c_i - entry_px)
            if pos >= 0:
                pos = -1
                qty = _qty_from_notional(notional, c_i)
                entry_px = c_i
                sim_bf = -1
        else:
            if not hold_on_no_edge:
                pos = 0
                qty = 0.0
                sim_bf = 0
                entry_px = 0.0

        if not use_tpsl:
            c_i2 = float(closes.iloc[i2])
            dpx = c_i2 - c_i
            move_pnl = pos * qty * dpx
            pnl += move_pnl
            if pos != 0 and qty > 0:
                segments.append(
                    {
                        "from_idx": i,
                        "to_idx": i2,
                        "pos": pos,
                        "qty": qty,
                        "d_close": round(dpx, 2),
                        "pnl_usdt": round(move_pnl, 2),
                        "target": target,
                        "reason_tail": (why or "")[:120],
                    }
                )

    t0 = times.iloc[es].strftime("%Y-%m-%dT%H:%M:%SZ")
    t_hi = min(ee - 1, n - 1)
    t1 = times.iloc[t_hi].strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "ok": True,
        "symbol": symbol,
        "n_bars": n,
        "eval_window_utc": [t0, t1],
        "eval_bar_slice": [es, ee],
        "sim_state_in": dict(st0),
        "sim_state_out": {
            "pos": int(pos),
            "qty": round(float(qty), 6),
            "entry_px": round(float(entry_px), 6),
            "sim_bf": int(sim_bf),
        },
        "hours_requested": hours,
        "refit_step_bars": step,
        "apply_swarm_gate": apply_swarm_gate,
        "tp_sl_pct": {"tp": tp_pct, "sl": sl_pct} if use_tpsl else None,
        "gate_env_echo": gate_env if apply_swarm_gate else {},
        "notional_usdt": notional,
        "margin_usdt_for_roi": round(margin, 2),
        "pnl_usdt_approx": round(pnl, 2),
        "roi_on_margin_approx": round(pnl / margin, 6) if margin > 0 else None,
        "offline_hm": {"source": hm_src, "symbol": hm_sym, "last": last_hm},
        "disclaimer": (
            "Offline sim: static Nautilus file; sim bf vote = simulated position; optional intrabar TPSL; no fees."
            + (
                " HM from Bybit demo position/list (as-of API time; not per-bar historical replay)."
                if hm_src != "synthetic"
                else ""
            )
        ),
        "segments_sample": segments[:30],
        "segments_total": len(segments),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline predict-protocol + swarm gate + optional TPSL sim")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--hours", type=float, default=48.0)
    ap.add_argument("--kline-limit", type=int, default=1000)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--rf-trees", type=int, default=max(10, int(os.environ.get("ASAP_RF_TREES", "32") or 32)))
    ap.add_argument(
        "--xgb-estimators",
        type=int,
        default=max(20, int(os.environ.get("ASAP_XGB_N_ESTIMATORS", "60") or 60)),
    )
    ap.add_argument("--step", type=int, default=4)
    ap.add_argument("--notional-usdt", type=float, default=2000.0)
    ap.add_argument("--leverage", type=float, default=50.0)
    ap.add_argument("--margin-usdt", type=float, default=None)
    ap.add_argument("--hold-on-no-edge", action="store_true")
    ap.add_argument("--training-json", type=Path, default=_PA / "training_channel_output.json")
    ap.add_argument(
        "--nautilus-json",
        type=Path,
        default=_DATA / "nautilus_strategy_signal.json",
        help="Static Nautilus bias for all bars (offline limitation)",
    )
    ap.add_argument("--apply-swarm-gate", action="store_true")
    ap.add_argument("--min-mean-long", type=float, default=0.0)
    ap.add_argument("--max-mean-short", type=float, default=0.0)
    ap.add_argument("--block-conflict", action="store_true", help="Set SWARM_ORDER_BLOCK_CONFLICT=1")
    ap.add_argument(
        "--fusion-align",
        action="store_true",
        help="Set SWARM_ORDER_REQUIRE_FUSION_ALIGN=1 (default **off** offline — fusion label gate is strict)",
    )
    ap.add_argument("--tp-pct", type=float, default=None, help="Take-profit %% from entry (with --sl-pct)")
    ap.add_argument("--sl-pct", type=float, default=None, help="Stop-loss %% from entry (with --tp-pct)")
    ap.add_argument("--grid-mean-long", action="store_true", help="Sweep min_mean_long; print best")
    ap.add_argument(
        "--eval-bar-start",
        type=int,
        default=None,
        help="Optional half-open eval window [start, end); requires --eval-bar-end",
    )
    ap.add_argument("--eval-bar-end", type=int, default=None, help="Exclusive end bar index (see --eval-bar-start)")
    ap.add_argument(
        "--offline-hm-source",
        choices=("synthetic", "demo_once", "demo_refresh"),
        default="synthetic",
        help="Hivemind vote: synthetic int, or Bybit demo position/list (BYBIT_DEMO_*); not historical replay",
    )
    ap.add_argument(
        "--offline-hm-symbol",
        default=None,
        help="Linear symbol for demo HM (default: same as --symbol)",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    base_gate = build_default_gate_env(
        min_mean_long=args.min_mean_long,
        max_mean_short=args.max_mean_short,
        block_conflict=args.block_conflict,
        fusion_align=args.fusion_align,
    )

    if args.grid_mean_long:
        sweep = [0.0, 0.1, 0.15, 0.2, 0.25, 0.35, 0.5]
        results = []
        for m in sweep:
            ge = {**base_gate, "SWARM_ORDER_MIN_MEAN_LONG": str(m)}
            r = run_simulation(
                hours=args.hours,
                step=args.step,
                kline_limit=args.kline_limit,
                window=args.window,
                rf_trees=args.rf_trees,
                xgb_estimators=args.xgb_estimators,
                notional=args.notional_usdt,
                leverage=args.leverage,
                margin_usdt=args.margin_usdt,
                hold_on_no_edge=args.hold_on_no_edge,
                training_path=args.training_json,
                nautilus_path=args.nautilus_json,
                apply_swarm_gate=args.apply_swarm_gate,
                gate_env=ge,
                tp_pct=args.tp_pct,
                sl_pct=args.sl_pct,
                symbol=args.symbol,
                eval_bar_start=args.eval_bar_start,
                eval_bar_end=args.eval_bar_end,
                offline_hm_source=args.offline_hm_source,
                offline_hm_symbol=args.offline_hm_symbol,
            )
            results.append({"min_mean_long": m, "pnl": r.get("pnl_usdt_approx"), "ok": r.get("ok")})
        best = max((x for x in results if x.get("ok")), key=lambda x: float(x.get("pnl") or -1e18), default=None)
        out = {"grid": "min_mean_long", "results": results, "best": best}
        print(json.dumps(out, indent=2))
        return 0

    r = run_simulation(
        hours=args.hours,
        step=args.step,
        kline_limit=args.kline_limit,
        window=args.window,
        rf_trees=args.rf_trees,
        xgb_estimators=args.xgb_estimators,
        notional=args.notional_usdt,
        leverage=args.leverage,
        margin_usdt=args.margin_usdt,
        hold_on_no_edge=args.hold_on_no_edge,
        training_path=args.training_json,
        nautilus_path=args.nautilus_json,
        apply_swarm_gate=args.apply_swarm_gate,
        gate_env=base_gate,
        tp_pct=args.tp_pct,
        sl_pct=args.sl_pct,
        symbol=args.symbol,
        eval_bar_start=args.eval_bar_start,
        eval_bar_end=args.eval_bar_end,
        offline_hm_source=args.offline_hm_source,
        offline_hm_symbol=args.offline_hm_symbol,
    )
    if args.json:
        print(json.dumps(r, indent=2, default=str))
        return 0 if r.get("ok") else 1
    print(json.dumps(r, indent=2, default=str))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
