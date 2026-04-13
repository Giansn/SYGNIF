#!/usr/bin/env python3
"""
Start ``GridMarketMaker`` in fixed order: (1) build catalog → (2) run backtest → (3) summary.

Synthetic ``QuoteTick`` stream around mid 50k (drifting) on ``TestInstrumentProvider.btcusdt_binance()``.
Venue uses **MARGIN** + **USDT** base so ``CurrencyPair`` BTCUSDT is valid (CASH+USDT-only fails).

Usage (inside ``nautilus-research`` container, cwd ``/lab/workspace``):

  python3 run_grid_market_maker_backtest.py
  python3 run_grid_market_maker_backtest.py --step prepare
  python3 run_grid_market_maker_backtest.py --step run --catalog /tmp/nautilus_grid_cat

Env:

  NAUTILUS_GRID_CATALOG   — catalog directory (default: tempfile under /tmp)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure sibling strategy module resolves when cwd != this dir
_LAB = Path(__file__).resolve().parent
if str(_LAB) not in sys.path:
    sys.path.insert(0, str(_LAB))


def _emit(phase: int, label: str, detail: str | None = None) -> None:
    msg = f"[grid-mm] ({phase}/3) {label}"
    if detail:
        msg += f" — {detail}"
    print(msg, flush=True)


def step_prepare(
    *,
    tick_count: int,
    catalog_dir: Path | None,
    reuse: bool,
) -> tuple[Path, object, str, str]:
    import pandas as pd
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from nautilus_trader.persistence.wranglers import QuoteTickDataWrangler
    from nautilus_trader.test_kit.providers import TestInstrumentProvider

    if catalog_dir is None:
        raw = os.environ.get("NAUTILUS_GRID_CATALOG", "").strip()
        if raw:
            catalog_dir = Path(raw).expanduser()
        else:
            catalog_dir = Path(tempfile.mkdtemp(prefix="nautilus_grid_mm_"))
    catalog_dir = catalog_dir.resolve()

    if catalog_dir.exists() and not reuse:
        shutil.rmtree(catalog_dir)
    catalog_dir.mkdir(parents=True, exist_ok=True)

    ins = TestInstrumentProvider.btcusdt_binance()
    idx = pd.date_range("2024-01-01", periods=tick_count, freq="50ms", tz="UTC")
    bids = [50000.0 - i * 0.05 for i in range(tick_count)]
    asks = [50001.0 - i * 0.05 for i in range(tick_count)]
    df = pd.DataFrame({"bid_price": bids, "ask_price": asks}, index=idx)
    ticks = QuoteTickDataWrangler(ins).process(df)

    catalog = ParquetDataCatalog(catalog_dir)
    catalog.write_data([ins])
    catalog.write_data(ticks)

    all_ticks = catalog.quote_ticks(instrument_ids=[ins.id.value])
    if not all_ticks:
        raise RuntimeError("catalog wrote no quote ticks")
    first = pd.Timestamp(all_ticks[0].ts_init, unit="ns", tz="UTC")
    last = pd.Timestamp(all_ticks[-1].ts_init, unit="ns", tz="UTC")
    start_time = first.isoformat()
    end_time = last.isoformat()

    return catalog_dir, ins, start_time, end_time


def step_run(
    catalog_dir: Path,
    ins: object,
    start_time: str,
    end_time: str,
    *,
    max_position: str,
    trade_size: str,
    num_levels: int,
    grid_step_bps: int,
    requote_threshold_bps: int,
) -> list:
    from nautilus_trader.backtest.node import BacktestDataConfig
    from nautilus_trader.backtest.node import BacktestEngineConfig
    from nautilus_trader.backtest.node import BacktestNode
    from nautilus_trader.backtest.node import BacktestRunConfig
    from nautilus_trader.backtest.node import BacktestVenueConfig
    from nautilus_trader.config import ImportableStrategyConfig
    from nautilus_trader.model.data import QuoteTick

    venue_configs = [
        BacktestVenueConfig(
            name="BINANCE",
            oms_type="HEDGING",
            account_type="MARGIN",
            base_currency="USDT",
            starting_balances=["100_000 USDT"],
        ),
    ]
    data_configs = [
        BacktestDataConfig(
            catalog_path=str(catalog_dir),
            data_cls=QuoteTick,
            instrument_id=ins.id,
            start_time=start_time,
            end_time=end_time,
        ),
    ]
    strategies = [
        ImportableStrategyConfig(
            strategy_path="grid_market_maker:GridMarketMaker",
            config_path="grid_market_maker:GridMarketMakerConfig",
            config={
                "instrument_id": str(ins.id),
                "max_position": max_position,
                "trade_size": trade_size,
                "num_levels": num_levels,
                "grid_step_bps": grid_step_bps,
                "skew_factor": 0.0,
                "requote_threshold_bps": requote_threshold_bps,
            },
        ),
    ]
    config = BacktestRunConfig(
        engine=BacktestEngineConfig(strategies=strategies),
        data=data_configs,
        venues=venue_configs,
    )
    node = BacktestNode(configs=[config])
    return node.run()


def _result_to_dict(r) -> dict:
    return {
        "trader_id": r.trader_id,
        "run_id": r.run_id,
        "iterations": r.iterations,
        "total_events": r.total_events,
        "total_orders": r.total_orders,
        "total_positions": r.total_positions,
        "elapsed_time": r.elapsed_time,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Ordered grid market maker backtest (synthetic quotes).")
    ap.add_argument(
        "--step",
        choices=("all", "prepare", "run"),
        default="all",
        help="prepare = catalog only; run = backtest only (needs --catalog); all = both",
    )
    ap.add_argument("--catalog", type=Path, default=None, help="Catalog path (prepare output / run input)")
    ap.add_argument("--reuse-catalog", action="store_true", help="Do not delete existing catalog before prepare")
    ap.add_argument("--ticks", type=int, default=400, help="Synthetic quote tick count")
    ap.add_argument("--max-position", default="0.01", help="Strategy max net position qty string")
    ap.add_argument("--trade-size", default="0.001", help="Per-level order size qty string")
    ap.add_argument("--num-levels", type=int, default=2)
    ap.add_argument("--grid-step-bps", type=int, default=10)
    ap.add_argument("--requote-threshold-bps", type=int, default=1)
    args = ap.parse_args()

    catalog_dir: Path | None = args.catalog
    ins: object | None = None
    start_time: str | None = None
    end_time: str | None = None

    if args.step in ("all", "prepare"):
        _emit(1, "PREPARE", "synthetic QuoteTick → ParquetDataCatalog")
        catalog_dir, ins, start_time, end_time = step_prepare(
            tick_count=args.ticks,
            catalog_dir=catalog_dir,
            reuse=args.reuse_catalog,
        )
        print(json.dumps({"catalog": str(catalog_dir), "start_time": start_time, "end_time": end_time}), flush=True)
        if args.step == "prepare":
            _emit(3, "DONE", "catalog ready for --step run")
            return 0

    if args.step in ("all", "run"):
        if catalog_dir is None or not catalog_dir.is_dir():
            print(json.dumps({"ok": False, "error": "missing or invalid --catalog"}), file=sys.stderr)
            return 2
        if ins is None or start_time is None or end_time is None:
            # run-only: infer instrument + range from catalog
            import pandas as pd
            from nautilus_trader.persistence.catalog import ParquetDataCatalog
            from nautilus_trader.test_kit.providers import TestInstrumentProvider

            ins = TestInstrumentProvider.btcusdt_binance()
            cat = ParquetDataCatalog(catalog_dir)
            q = cat.quote_ticks(instrument_ids=[ins.id.value])
            if not q:
                print(json.dumps({"ok": False, "error": "no ticks in catalog"}), file=sys.stderr)
                return 2
            first = pd.Timestamp(q[0].ts_init, unit="ns", tz="UTC")
            last = pd.Timestamp(q[-1].ts_init, unit="ns", tz="UTC")
            start_time = first.isoformat()
            end_time = last.isoformat()

        _emit(2, "RUN", "BacktestNode + GridMarketMaker (post-only limits)")
        results = step_run(
            catalog_dir,
            ins,
            start_time,
            end_time,
            max_position=args.max_position,
            trade_size=args.trade_size,
            num_levels=args.num_levels,
            grid_step_bps=args.grid_step_bps,
            requote_threshold_bps=args.requote_threshold_bps,
        )
        if not results:
            print(json.dumps({"ok": False, "error": "backtest returned no results"}), file=sys.stderr)
            return 3
        out = {"ok": True, "catalog": str(catalog_dir), "results": [_result_to_dict(r) for r in results]}
        _emit(3, "DONE", "summary on stdout")
        print(json.dumps(out, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
