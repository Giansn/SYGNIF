#!/usr/bin/env python3
"""
**Fresh predict → print (flush) → Bybit demo order ASAP** so signal decay does not eat the edge.

1. Pulls latest **5m** linear klines (public), runs ``btc_predict_live.fit_predict_live`` (same stack as
   the Nautilus live-predict path).
2. Writes ``prediction_agent/btc_prediction_output.json`` (optional ``--no-write-json``).
3. Prints a compact summary + JSON to **stdout** with ``flush=True``.
4. If ``--execute`` and ``SYGNIF_PREDICTION_ASAP_ORDER_ACK=YES``: **immediately** ``set-leverage`` +
   **market** long or short on **api-demo** (``trade_overseer/bybit_linear_hedge.py``), then **TP/SL**
   (defaults: ``SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL=1``, ``SYGNIF_SWARM_TPSL_PROFILE=reward_risk``,
   ``SYGNIF_SWARM_TP_USDT_TARGET`` / ``SYGNIF_SWARM_SL_USDT_TARGET`` = **600** / **360** USDT if unset — set TP to ``0`` to disable).

**Direction (aggressive, short-horizon):** vote from RF Δ, XGB Δ, and direction_logistic (≥52%).
Ties broken by logreg ≥58%. **Long** blocked when ``r01_bearish_from_training`` is true (same as analysis
helper). **Short** is allowed when the vote is bearish (no symmetric R01 short block in this script).

**Leverage vs expected move:** mean(|RFΔ|, |XGBΔ|) as % of spot → map linearly to
``ASAP_MOVE_LEV_FLOOR_PCT`` … ``ASAP_MOVE_LEV_CAP_PCT`` into **high** … **low** leverage (small expected
move → higher leverage; large expected move → lower leverage). Bounds: ``BYBIT_DEMO_ORDER_MIN_LEVERAGE`` /
``BYBIT_DEMO_ORDER_MAX_LEVERAGE``.

**Size:** free USDT × ``BYBIT_DEMO_ORDER_STAKE_FRAC`` × (0.45 + 0.55 × move_index) × logreg conviction
scale, clamped by min/max qty — all automated unless ``--manual-qty`` / ``--manual-notional-usdt``.
With ``--execute`` and no manual qty/notional, defaults are ``SYGNIF_PREDICT_DEFAULT_NOTIONAL_USDT`` (**100000**)
and ``SYGNIF_PREDICT_DEFAULT_MANUAL_LEVERAGE`` (**50×**); set ``SYGNIF_PREDICT_EXECUTE_AUTO_SIZING_OFF=1`` to keep
auto stake sizing instead.

**Continuous loop** (entries + exits): ``scripts/btc_predict_protocol_loop.py``.

**Trade saved JSON (e.g. 1h ``btc_predict_runner`` output):** ``--from-json [PATH]`` skips the 5m refit and
loads ``btc_prediction_output.json`` (default path when the flag is given bare). Use with ``--execute``
to align the venue with that file’s ``decide_side`` signal.

**Larger / “max profit” sizing (demo, bounded):** ``--max-leverage`` sets leverage to
``BYBIT_DEMO_MANUAL_LEVERAGE_MAX`` (default **100**, clamped 1..125). Optional ``--manual-notional-usdt``
for qty ≈ USDT/close. After a successful open, best-effort **TP/SL** matches the predict loop
(``apply_btc_future_tpsl`` + ``SYGNIF_SWARM_TP_USDT_TARGET`` / ``SYGNIF_SWARM_SL_USDT_TARGET`` fallback).

Canonical **Nautilus** bar-synced flow remains separate; this path optimises **latency** between model
output and venue submit.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PA = _REPO / "prediction_agent"
_DATA = _REPO / "finance_agent" / "btc_specialist" / "data"
sys.path.insert(0, str(_PA))
sys.path.insert(0, str(_REPO / "trade_overseer"))

import bybit_linear_hedge as blh  # noqa: E402
from btc_asap_predict_core import decide_side  # noqa: E402
from btc_asap_predict_core import env_float  # noqa: E402
from btc_asap_predict_core import env_int  # noqa: E402
from btc_asap_predict_core import leverage_from_move_pct  # noqa: E402
from btc_asap_predict_core import logreg_confidence  # noqa: E402
from btc_asap_predict_core import move_pct_and_close  # noqa: E402
from btc_asap_predict_core import parse_usdt_available  # noqa: E402
from btc_asap_predict_core import qty_btc  # noqa: E402
from btc_asap_predict_core import run_live_fit  # noqa: E402


def _avg_entry_from_position_list(pr: dict, symbol: str) -> float | None:
    sym = (symbol or "").replace("/", "").upper().strip() or "BTCUSDT"
    if pr.get("retCode") != 0:
        return None
    best: float | None = None
    best_sz = 0.0
    for row in (pr.get("result") or {}).get("list") or []:
        if str(row.get("symbol", "")).upper() != sym:
            continue
        try:
            sz = float(str(row.get("size") or "0").strip() or 0)
        except (TypeError, ValueError):
            continue
        if abs(sz) <= 1e-9:
            continue
        if abs(sz) > best_sz:
            best_sz = abs(sz)
            try:
                best = float(str(row.get("avgPrice") or 0).strip() or 0)
            except (TypeError, ValueError):
                best = None
    return best if best and best > 0 else None


def _asap_post_open_tp_sl(
    symbol: str,
    position_idx: int,
    pos_side: str,
    qty_s: str,
    out: dict,
    *,
    json_out: Path,
) -> None:
    """Persist prediction JSON, then Swarm TP/SL (if enabled) + USDT TP/SL fallback like predict loop."""
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sym_u = symbol.replace("/", "").upper().strip() or "BTCUSDT"
    os.environ.setdefault("SYGNIF_SWARM_TPSL_SYMBOL", sym_u)
    fa = str(_REPO / "finance_agent")
    if fa not in sys.path:
        sys.path.insert(0, fa)
    tpsl_full_ok = False
    try:
        from swarm_btc_future_tpsl_apply import apply_btc_future_tpsl  # noqa: PLC0415
    except ImportError:
        apply_btc_future_tpsl = None  # type: ignore[assignment]
    if apply_btc_future_tpsl is not None:
        sleep_sec = max(0.0, env_float("SYGNIF_SWARM_TPSL_POST_OPEN_SLEEP_SEC", 1.0))
        retries = env_int("SYGNIF_SWARM_TPSL_POST_OPEN_RETRIES", 8, lo=1, hi=99)
        last: dict = {}
        for attempt in range(retries):
            if sleep_sec > 0:
                time.sleep(sleep_sec)
            try:
                last = apply_btc_future_tpsl(dry_run=False)
            except Exception as exc:  # noqa: BLE001
                print(f"ASAP_TPSL_WARN attempt={attempt + 1}: {exc}", flush=True)
                continue
            if last.get("ok"):
                tpsl_full_ok = True
                print(f"ASAP_TPSL ok {json.dumps(last, default=str)}", flush=True)
                break
            if last.get("skipped") != "flat":
                print(f"ASAP_TPSL skip={last.get('skipped')!r}", flush=True)
                break
    tp_tgt = env_float("SYGNIF_SWARM_TP_USDT_TARGET", 0.0)
    if tp_tgt > 0 and not tpsl_full_ok:
        try:
            avg_px: float | None = None
            pr_tp: dict = {}
            for _ in range(10):
                pr_tp = blh.position_list(symbol)
                avg_px = _avg_entry_from_position_list(pr_tp, symbol)
                if avg_px and avg_px > 0:
                    break
                time.sleep(0.35)
            qf = float(qty_s)
            if avg_px and qf > 1e-12:
                sl_tgt = env_float("SYGNIF_SWARM_SL_USDT_TARGET", tp_tgt)
                if sl_tgt <= 0:
                    sl_tgt = tp_tgt
                delta_tp = tp_tgt / qf
                delta_sl = sl_tgt / qf
                if pos_side == "long":
                    tp_px = avg_px + delta_tp
                    sl_px = avg_px - delta_sl
                else:
                    tp_px = avg_px - delta_tp
                    sl_px = avg_px + delta_sl
                tsr = blh.set_trading_stop_linear(
                    symbol,
                    position_idx=position_idx,
                    take_profit=f"{tp_px:.2f}",
                    stop_loss=f"{sl_px:.2f}",
                    tp_trigger_by="MarkPrice",
                    sl_trigger_by="MarkPrice",
                )
                print(
                    f"ASAP_TP_SL {json.dumps(tsr, default=str)} "
                    f"target_tp_usdt={tp_tgt} target_sl_usdt={sl_tgt}",
                    flush=True,
                )
            else:
                print(
                    "ASAP_TP_SL_WARN no avg entry after open (increase wait or set TP manually); "
                    f"position_list retCode={pr_tp.get('retCode')}",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001
            print(f"ASAP_TP_SL_WARN {exc}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Live 5m predict then immediate Bybit demo market order")
    ap.add_argument("--symbol", default="BTCUSDT", help="Bybit linear symbol")
    ap.add_argument(
        "--kline-limit",
        type=int,
        default=max(120, min(1000, int(os.environ.get("ASAP_KLINE_LIMIT", "320") or 320))),
        help="5m klines (smaller = faster; env ASAP_KLINE_LIMIT)",
    )
    ap.add_argument("--window", type=int, default=5, help="Feature window for live fit")
    ap.add_argument(
        "--rf-trees",
        type=int,
        default=max(10, int(os.environ.get("ASAP_RF_TREES", "32") or 32)),
        help="RF estimators (lower = faster; env ASAP_RF_TREES)",
    )
    ap.add_argument(
        "--xgb-estimators",
        type=int,
        default=max(20, int(os.environ.get("ASAP_XGB_N_ESTIMATORS", "60") or 60)),
        help="XGB n_estimators (lower = faster; env ASAP_XGB_N_ESTIMATORS)",
    )
    ap.add_argument(
        "--data-dir",
        type=Path,
        default=_DATA,
        help="Nautilus sidecar/bundle dir for enhanced consensus",
    )
    ap.add_argument(
        "--training-json",
        type=Path,
        default=_PA / "training_channel_output.json",
        help="R01 governance (optional file)",
    )
    ap.add_argument(
        "--write-json",
        default=str(_PA / "btc_prediction_output.json"),
        metavar="PATH",
        help="Write prediction JSON (empty to skip)",
    )
    ap.add_argument("--no-write-json", action="store_true", help="Do not write btc_prediction_output.json")
    ap.add_argument("--manual-qty", default=None, metavar="QTY")
    ap.add_argument(
        "--manual-notional-usdt",
        type=float,
        default=None,
        metavar="USDT",
        help="Approximate USDT notional for qty (qty ≈ USDT / close); optional",
    )
    ap.add_argument("--manual-leverage", type=float, default=None, metavar="N")
    ap.add_argument(
        "--max-leverage",
        action="store_true",
        help="Use BYBIT_DEMO_MANUAL_LEVERAGE_MAX (default 100, clamped 1..125) instead of move-based auto",
    )
    ap.add_argument(
        "--from-json",
        nargs="?",
        const=str(_PA / "btc_prediction_output.json"),
        default=None,
        metavar="PATH",
        help="Skip 5m fit; load prediction from JSON (default: btc_prediction_output.json when flag is bare)",
    )
    ap.add_argument(
        "--position-idx",
        type=int,
        default=int(os.environ.get("BYBIT_DEMO_POSITION_IDX", "0") or 0),
    )
    ap.add_argument("--execute", action="store_true", help="Submit set-leverage + market order")
    args = ap.parse_args()
    if bool(getattr(args, "execute", False)):
        _off = (os.environ.get("SYGNIF_PREDICT_EXECUTE_AUTO_SIZING_OFF") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if not _off:
            _mq0 = args.manual_qty is not None and str(args.manual_qty).strip() != ""
            _mn0 = args.manual_notional_usdt is not None and float(args.manual_notional_usdt) > 0
            if not _mq0 and not _mn0:
                args.manual_notional_usdt = env_float("SYGNIF_PREDICT_DEFAULT_NOTIONAL_USDT", 100_000.0)
            if args.manual_leverage is None:
                args.manual_leverage = env_float("SYGNIF_PREDICT_DEFAULT_MANUAL_LEVERAGE", 50.0)
    mq = args.manual_qty is not None and str(args.manual_qty).strip() != ""
    mn = args.manual_notional_usdt is not None and float(args.manual_notional_usdt) > 0
    if mq and mn:
        ap.error("use either --manual-qty or --manual-notional-usdt, not both")

    def _load_json(path: Path) -> dict | None:
        if not path.is_file():
            return None
        try:
            o = json.loads(path.read_text(encoding="utf-8"))
            return o if isinstance(o, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    training = _load_json(args.training_json)

    wj = str(args.write_json).strip() if not args.no_write_json else ""
    wpath = wj or None
    json_out_path = Path(wpath) if wpath else (_PA / "btc_prediction_output.json")

    pred_ms = 0.0
    if args.from_json is not None:
        jpath = Path(args.from_json)
        out = _load_json(jpath)
        if not out:
            print(f"ASAP_ERR missing_or_invalid_json {jpath}", file=sys.stderr)
            return 9
        preds = out.get("predictions") or {}
        enhanced = str(preds.get("consensus_nautilus_enhanced") or preds.get("consensus") or "")
        allow_buy = enhanced.upper().strip() in ("BULLISH", "STRONG_BULLISH")
        print(f"ASAP_FROM_JSON path={jpath} pred_ms=0 (no refit)", flush=True)
    else:
        allow_buy, enhanced, out, pred_ms = run_live_fit(
            symbol=args.symbol,
            kline_limit=args.kline_limit,
            window=args.window,
            data_dir=str(args.data_dir),
            rf_trees=args.rf_trees,
            xgb_estimators=args.xgb_estimators,
            write_json_path=wpath,
        )

    move_pct, close = move_pct_and_close(out)
    lev_auto, t_move = leverage_from_move_pct(move_pct)
    if args.manual_leverage is not None:
        lo = env_int("BYBIT_DEMO_ORDER_MIN_LEVERAGE", 5, lo=1, hi=125)
        hi = env_int("BYBIT_DEMO_ORDER_MAX_LEVERAGE", 25, lo=1, hi=125)
        if lo > hi:
            lo, hi = hi, lo
        lev = max(lo, min(hi, int(round(float(args.manual_leverage)))))
        lev_src = "manual"
    elif args.max_leverage:
        lo = env_int("BYBIT_DEMO_ORDER_MIN_LEVERAGE", 1, lo=1, hi=125)
        lev_hi = env_int("BYBIT_DEMO_MANUAL_LEVERAGE_MAX", 100, lo=1, hi=125)
        lev = max(lo, lev_hi)
        lev_src = f"max_leverage BYBIT_DEMO_MANUAL_LEVERAGE_MAX={lev_hi}"
    else:
        lev, lev_src = lev_auto, f"auto move_pct={move_pct:.4f}% t={t_move:.3f}"

    side, why = decide_side(out, training)
    lconf = logreg_confidence(out)
    preds = out.get("predictions") or {}
    cons = str(preds.get("consensus_nautilus_enhanced") or preds.get("consensus") or "")

    summary = {
        "asap_predict_ms": round(pred_ms, 1),
        "allow_buy_consensus_only": allow_buy,
        "consensus_nautilus_enhanced": enhanced,
        "entry_side": side,
        "entry_reason": why,
        "expected_move_pct_mean_abs_delta": round(move_pct, 6),
        "leverage": lev,
        "leverage_source": lev_src,
        "prediction": out,
    }

    print("=== SYGNIF_ASAP_PREDICT ===", flush=True)
    print(json.dumps({k: v for k, v in summary.items() if k != "prediction"}, indent=2), flush=True)
    print("=== SYGNIF_ASAP_PREDICTION_JSON ===", flush=True)
    print(json.dumps(out, indent=2), flush=True)
    print("=== END_ASAP ===", flush=True)

    if side is None:
        print("No entry side — skip order.", flush=True)
        return 2 if args.execute else 0

    if args.manual_qty is not None and str(args.manual_qty).strip():
        qty_s = str(args.manual_qty).strip()
        qty_src = "manual"
    elif args.manual_notional_usdt is not None and float(args.manual_notional_usdt) > 0:
        if close <= 0:
            print(f"ASAP_ERR manual-notional needs positive close, got {close}", flush=True)
            return 6
        mn = float(args.manual_notional_usdt)
        raw_qty = mn / close
        step = 0.001
        q = math.floor(raw_qty / step) * step
        min_q = max(1e-9, env_float("BYBIT_DEMO_ORDER_MIN_QTY", 0.001))
        if q + 1e-12 < min_q:
            print(f"ASAP_ERR notional→qty below min_qty {min_q} (got {q})", flush=True)
            return 6
        qty_s = f"{q:.6f}".rstrip("0").rstrip(".") or str(min_q)
        qty_src = f"manual_notional_usdt≈{mn}"
    else:
        try:
            w = blh.wallet_balance_unified_coin("USDT")
            free = parse_usdt_available(w)
        except RuntimeError as exc:
            w = {"retCode": -1, "retMsg": str(exc)}
            free = None
        if free is None or free <= 0:
            qty_s = ""
            qty_src = "auto (need BYBIT_DEMO_* for wallet; or pass --manual-qty)"
            print(
                "ASAP: could not read free USDT (demo keys missing or API error).",
                json.dumps(w)[:800],
                flush=True,
            )
        else:
            qty_s, eff = qty_btc(free_usdt=free, close=close, t_move=t_move, logreg_conf=lconf)
            if not qty_s:
                print("Automated qty: below minimum after rounding.", flush=True)
                return 4
            qty_src = f"auto eff_frac≈{eff:.6f} free≈{free:.2f} close≈{close:.2f}"

    order_side = "Buy" if side == "long" else "Sell"
    print(
        f"=== SYGNIF_ASAP_ORDER_PLAN symbol={args.symbol} side={order_side} qty={qty_s or 'N/A'} "
        f"({qty_src}) leverage={lev}x ({lev_src}) positionIdx={args.position_idx} ===",
        flush=True,
    )

    if cons.upper() == "MIXED":
        print(
            "Note: consensus is MIXED — RF/XGB next_mean may disagree with direction_logistic; "
            "automation still keys off logreg confidence.",
            flush=True,
        )

    if not args.execute:
        print("Dry-run: set SYGNIF_PREDICTION_ASAP_ORDER_ACK=YES and --execute to submit.", flush=True)
        return 0

    if not qty_s:
        print("Refusing --execute without qty (set demo keys or --manual-qty).", flush=True)
        return 8

    if os.environ.get("SYGNIF_PREDICTION_ASAP_ORDER_ACK", "").strip().upper() != "YES":
        print("Refusing --execute: set SYGNIF_PREDICTION_ASAP_ORDER_ACK=YES", flush=True)
        return 5

    # Nach Fill: Swarm-TP/SL aus ``btc_prediction_output.json`` (wenn aktiv) + USDT-Abstands-Fallback.
    os.environ.setdefault("SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL", "1")
    os.environ.setdefault("SYGNIF_SWARM_TPSL_PROFILE", "reward_risk")
    os.environ.setdefault("SYGNIF_SWARM_TP_USDT_TARGET", "600")
    os.environ.setdefault("SYGNIF_SWARM_SL_USDT_TARGET", "360")

    lr = blh.set_linear_leverage(args.symbol, str(lev))
    print("set-leverage:", json.dumps(lr), flush=True)
    # Bybit 110043 = leverage already at requested value
    if lr.get("retCode") not in (0, 110043):
        return 6

    mo = blh.create_market_order(
        args.symbol,
        order_side,
        qty_s,
        args.position_idx,
        reduce_only=False,
    )
    print("order/create:", json.dumps(mo), flush=True)
    if mo.get("retCode") != 0:
        return 7
    _asap_post_open_tp_sl(
        args.symbol,
        args.position_idx,
        side,
        qty_s,
        out,
        json_out=json_out_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
