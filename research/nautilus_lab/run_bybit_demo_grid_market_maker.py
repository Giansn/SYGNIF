#!/usr/bin/env python3
"""
Run vendored ``GridMarketMaker`` on **Bybit demo** via Nautilus live adapters (``demo=True``).

Submits **post-only** limit grid orders. Requires demo keys + explicit ACK.

Env:

- ``NAUTILUS_GRID_MM_DEMO_ACK=YES``
- **Isolated vs** ``freqtrade-btc-0-1``: ``BYBIT_DEMO_GRID_API_KEY`` / ``BYBIT_DEMO_GRID_API_SECRET`` (recommended).
- Or shared demo account: ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET`` only (grid and 0.1 then share one wallet).

Bybit **hedge (default)** for USDT linear / inverse: ``BybitExecClientConfig.position_mode`` is set to
``BothSides`` (mode 3) for this symbol at client start (Nautilus calls Bybit ``switch-mode``).
Use ``--merged-single`` to force one-way (``MergedSingle``) instead.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_LAB = Path(__file__).resolve().parent
if str(_LAB) not in sys.path:
    sys.path.insert(0, str(_LAB))


def _require_demo_grid_credentials() -> tuple[str, str, bool]:
    """ACK + demo keys. Prefer ``BYBIT_DEMO_GRID_*`` so grid does not share orders/position with ``freqtrade-btc-0-1``."""
    ack = os.environ.get("NAUTILUS_GRID_MM_DEMO_ACK", "").strip().upper()
    gk = os.environ.get("BYBIT_DEMO_GRID_API_KEY", "").strip()
    gs = os.environ.get("BYBIT_DEMO_GRID_API_SECRET", "").strip()
    dk = os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
    ds = os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
    missing: list[str] = []
    if ack != "YES":
        missing.append("NAUTILUS_GRID_MM_DEMO_ACK=YES (confirms demo post-only grid)")
    if gk and gs:
        pass
    elif dk and ds:
        pass
    else:
        missing.append(
            "BYBIT_DEMO_GRID_API_KEY + BYBIT_DEMO_GRID_API_SECRET (isolated grid) "
            "or BYBIT_DEMO_API_KEY + BYBIT_DEMO_API_SECRET (shared demo account)"
        )
    if missing:
        print(
            "Refusing to run: Nautilus GridMarketMaker on Bybit **demo** needs:\n  "
            + "\n  ".join(missing),
            file=sys.stderr,
        )
        raise SystemExit(2)
    if gk and gs:
        return gk, gs, True
    return dk, ds, False


def _product_types_for_instrument(s: str) -> tuple:
    from nautilus_trader.core.nautilus_pyo3 import BybitProductType

    u = s.upper()
    if "LINEAR" in u:
        return (BybitProductType.LINEAR,)
    if "INVERSE" in u:
        return (BybitProductType.INVERSE,)
    if "SPOT" in u:
        return (BybitProductType.SPOT,)
    return (BybitProductType.LINEAR,)


def _bybit_position_mode_for_symbol(
    instrument_id: InstrumentId,
    product_types: tuple,
    *,
    hedge: bool,
):
    """Return ``position_mode`` dict for ``BybitExecClientConfig`` (linear / inverse only)."""
    from nautilus_trader.core.nautilus_pyo3 import BybitPositionMode
    from nautilus_trader.core.nautilus_pyo3 import BybitProductType

    if not product_types:
        return None
    pt0 = product_types[0]
    if pt0 not in (BybitProductType.LINEAR, BybitProductType.INVERSE):
        return None
    mode = BybitPositionMode.BOTH_SIDES if hedge else BybitPositionMode.MERGED_SINGLE
    return {str(instrument_id.symbol): mode}


def main() -> int:
    grid_key, grid_secret, isolated_account = _require_demo_grid_credentials()
    os.environ["BYBIT_DEMO_API_KEY"] = grid_key
    os.environ["BYBIT_DEMO_API_SECRET"] = grid_secret
    if not isolated_account:
        print(
            "[grid-mm-live] WARNING: Grid uses the same BYBIT_DEMO_* as freqtrade-btc-0-1 — one demo account; "
            "orders and net position interact. For a fair A/B vs BTC_Strategy_0_1, set "
            "BYBIT_DEMO_GRID_API_KEY and BYBIT_DEMO_GRID_API_SECRET (second Bybit demo API key).",
            file=sys.stderr,
            flush=True,
        )
    else:
        print(
            "[grid-mm-live] Using BYBIT_DEMO_GRID_* (isolated from freqtrade-btc-0-1 default demo keys).",
            flush=True,
        )

    ap = argparse.ArgumentParser(description="Bybit demo + GridMarketMaker (post-only grid).")
    ap.add_argument("--instrument", default="ETHUSDT-LINEAR.BYBIT")
    ap.add_argument(
        "--max-position",
        default="0.1",
        help="Max net exposure per side (base qty string, e.g. ETH)",
    )
    ap.add_argument(
        "--trade-size",
        default="0.01",
        help="Per-level order size; must meet venue min lot / step",
    )
    ap.add_argument("--num-levels", type=int, default=2)
    ap.add_argument("--grid-step-bps", type=int, default=15)
    ap.add_argument("--skew-factor", type=float, default=0.0)
    ap.add_argument(
        "--requote-threshold-bps",
        type=int,
        default=4,
        help="Mid move (bps) before full re-quote; lower = more frequent grid updates",
    )
    ap.add_argument(
        "--merged-single",
        action="store_true",
        help="Use Bybit one-way (MergedSingle) instead of default hedge (BothSides)",
    )
    ap.add_argument(
        "--on-cancel-resubmit",
        action="store_true",
        help="Pass through to GridMarketMakerConfig.on_cancel_resubmit",
    )
    args = ap.parse_args()

    from grid_market_maker import GridMarketMaker
    from grid_market_maker import GridMarketMakerConfig
    from nautilus_trader.adapters.bybit import BYBIT
    from nautilus_trader.adapters.bybit import BybitDataClientConfig
    from nautilus_trader.adapters.bybit import BybitExecClientConfig
    from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory
    from nautilus_trader.adapters.bybit import BybitLiveExecClientFactory
    from nautilus_trader.config import InstrumentProviderConfig
    from nautilus_trader.config import LiveExecEngineConfig
    from nautilus_trader.core.nautilus_pyo3 import BybitProductType
    from nautilus_trader.config import LoggingConfig
    from nautilus_trader.config import TradingNodeConfig
    from nautilus_trader.live.config import LiveRiskEngineConfig
    from nautilus_trader.live.config import RoutingConfig
    from nautilus_trader.live.node import TradingNode
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.identifiers import TraderId
    from nautilus_trader.model.objects import Quantity
    from nautilus_trader.portfolio.config import PortfolioConfig

    instrument_id = InstrumentId.from_str(args.instrument)
    product_types = _product_types_for_instrument(args.instrument)
    is_spot = product_types == (BybitProductType.SPOT,)
    hedge = not args.merged_single
    position_mode = _bybit_position_mode_for_symbol(
        instrument_id, product_types, hedge=hedge
    )

    routing = RoutingConfig(default=True, venues=frozenset({BYBIT}))
    inst_prov = InstrumentProviderConfig(load_all=False, load_ids=frozenset({instrument_id}))

    config_node = TradingNodeConfig(
        trader_id=TraderId("SYGNIF-GRIDMM-001"),
        logging=LoggingConfig(log_level="INFO", use_pyo3=True),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_instrument_ids=[instrument_id],
            open_check_interval_secs=10.0,
            open_check_open_only=True,
            graceful_shutdown_on_exception=True,
        ),
        risk_engine=LiveRiskEngineConfig(bypass=True),
        portfolio=PortfolioConfig(),
        data_clients={
            BYBIT: BybitDataClientConfig(
                # Mainnet public WS (``wss://stream.bybit.com/v5/public/linear``): Bybit demo host has no
                # public linear path (404 on ``stream-demo``). Exec client stays ``demo=True`` for api-demo orders.
                demo=False,
                testnet=False,
                product_types=product_types,
                instrument_provider=inst_prov,
                routing=routing,
            ),
        },
        exec_clients={
            BYBIT: BybitExecClientConfig(
                demo=True,
                testnet=False,
                product_types=product_types,
                instrument_provider=inst_prov,
                routing=routing,
                use_spot_position_reports=bool(is_spot),
                position_mode=position_mode,
            ),
        },
    )

    grid_cfg = GridMarketMakerConfig(
        instrument_id=instrument_id,
        max_position=Quantity.from_str(args.max_position),
        trade_size=Quantity.from_str(args.trade_size),
        num_levels=args.num_levels,
        grid_step_bps=args.grid_step_bps,
        skew_factor=args.skew_factor,
        requote_threshold_bps=args.requote_threshold_bps,
        on_cancel_resubmit=args.on_cancel_resubmit,
    )

    node = TradingNode(config=config_node)
    node.trader.add_strategy(GridMarketMaker(config=grid_cfg))
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.build()

    if not position_mode:
        pm = "n/a"
    elif hedge:
        pm = "hedge(BothSides)"
    else:
        pm = "one-way(MergedSingle)"
    print(
        f"[grid-mm-live] demo=True | {instrument_id} | position_mode={pm} | trade_size={args.trade_size} | "
        f"max_position={args.max_position} | levels={args.num_levels} | step_bps={args.grid_step_bps} | "
        f"requote_bps={args.requote_threshold_bps}",
        flush=True,
    )
    try:
        node.run()
    except KeyboardInterrupt:
        print("[grid-mm-live] interrupted", flush=True)
    finally:
        node.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
