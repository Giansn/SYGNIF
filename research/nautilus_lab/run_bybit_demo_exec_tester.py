#!/usr/bin/env python3
"""
Run Nautilus upstream ``ExecTester`` against **Bybit demo** (``demo=True`` on both clients).

**Places real orders on the demo venue** (still requires ``NAUTILUS_EXEC_TESTER_DEMO_ACK=YES``).

Default: **post-only** limits away from touch (``use_post_only=True``), no market open on start.
Use ``--open-on-start QTY`` to mirror upstream ``bybit_exec_tester.py`` (market open at strategy start).

Env:

- ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET`` — demo trading keys
- ``NAUTILUS_EXEC_TESTER_DEMO_ACK=YES`` — mandatory safety gate
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

_LAB = Path(__file__).resolve().parent
if str(_LAB) not in sys.path:
    sys.path.insert(0, str(_LAB))


def _require_ack() -> None:
    if os.environ.get("NAUTILUS_EXEC_TESTER_DEMO_ACK", "").strip().upper() != "YES":
        print(
            "Refusing to run: set NAUTILUS_EXEC_TESTER_DEMO_ACK=YES to confirm demo venue "
            "order flow (ExecTester submits orders).",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _require_demo_keys() -> None:
    k = os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
    s = os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
    if not k or not s:
        print(
            "Missing BYBIT_DEMO_API_KEY or BYBIT_DEMO_API_SECRET.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _bybit_position_mode_for_symbol(
    instrument_id,
    product_types: tuple,
    *,
    hedge: bool,
):
    from nautilus_trader.core.nautilus_pyo3 import BybitPositionMode
    from nautilus_trader.core.nautilus_pyo3 import BybitProductType

    if not product_types:
        return None
    if product_types[0] not in (BybitProductType.LINEAR, BybitProductType.INVERSE):
        return None
    mode = BybitPositionMode.BOTH_SIDES if hedge else BybitPositionMode.MERGED_SINGLE
    return {str(instrument_id.symbol): mode}


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


def _exec_defaults_for_linear() -> dict:
    return {
        "enable_limit_sells": True,
        "use_spot_position_reports": False,
        "order_params": {},
        "reduce_only_on_stop": False,
    }


def _exec_defaults_for_spot() -> dict:
    return {
        "enable_limit_sells": False,
        "use_spot_position_reports": True,
        "order_params": {"is_leverage": True},
        "reduce_only_on_stop": True,
    }


def main() -> int:
    _require_ack()
    _require_demo_keys()

    ap = argparse.ArgumentParser(description="Bybit demo + Nautilus ExecTester (orders).")
    ap.add_argument(
        "--instrument",
        default="ETHUSDT-LINEAR.BYBIT",
        help="Full InstrumentId (default: small linear ETH per Nautilus upstream example)",
    )
    ap.add_argument(
        "--order-qty",
        default="0.01",
        help="Base quantity string for limit orders / optional market open",
    )
    ap.add_argument(
        "--open-on-start",
        default=None,
        metavar="QTY",
        help="If set, submit market order for this signed net qty at strategy start (e.g. 0.01 long)",
    )
    ap.add_argument(
        "--product",
        choices=("auto", "linear", "spot", "inverse", "all"),
        default="auto",
        help="Bybit product_types for HTTP instrument load (auto: infer from --instrument)",
    )
    ap.add_argument(
        "--verbose-data",
        action="store_true",
        help="Log every quote/trade tick (noisy)",
    )
    ap.add_argument(
        "--merged-single",
        action="store_true",
        help="Use Bybit one-way (MergedSingle); default is hedge (BothSides) for linear/inverse",
    )
    args = ap.parse_args()

    from nautilus_trader.adapters.bybit import BYBIT
    from nautilus_trader.adapters.bybit import BybitDataClientConfig
    from nautilus_trader.adapters.bybit import BybitExecClientConfig
    from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory
    from nautilus_trader.adapters.bybit import BybitLiveExecClientFactory
    from nautilus_trader.config import InstrumentProviderConfig
    from nautilus_trader.config import LiveExecEngineConfig
    from nautilus_trader.config import LoggingConfig
    from nautilus_trader.config import TradingNodeConfig
    from nautilus_trader.core.nautilus_pyo3 import BybitProductType
    from nautilus_trader.live.config import LiveRiskEngineConfig
    from nautilus_trader.live.config import RoutingConfig
    from nautilus_trader.live.node import TradingNode
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.identifiers import TraderId
    from nautilus_trader.portfolio.config import PortfolioConfig
    from nautilus_trader.test_kit.strategies.tester_exec import ExecTester
    from nautilus_trader.test_kit.strategies.tester_exec import ExecTesterConfig

    instrument_id = InstrumentId.from_str(args.instrument)
    if args.product == "auto":
        product_types = _product_types_for_instrument(args.instrument)
    else:
        pmap = {
            "linear": (BybitProductType.LINEAR,),
            "spot": (BybitProductType.SPOT,),
            "inverse": (BybitProductType.INVERSE,),
            "all": (
                BybitProductType.SPOT,
                BybitProductType.LINEAR,
                BybitProductType.INVERSE,
                BybitProductType.OPTION,
            ),
        }
        product_types = pmap[args.product]

    if BybitProductType.SPOT in product_types and len(product_types) > 1:
        print(
            "SPOT mixed with other product types is discouraged; use --product spot or linear alone.",
            file=sys.stderr,
        )

    is_spot = len(product_types) == 1 and product_types[0] == BybitProductType.SPOT
    xdefs = _exec_defaults_for_spot() if is_spot else _exec_defaults_for_linear()
    hedge = not args.merged_single
    position_mode = _bybit_position_mode_for_symbol(
        instrument_id, product_types, hedge=hedge
    )

    order_qty = Decimal(str(args.order_qty))
    open_start = Decimal(str(args.open_on_start)) if args.open_on_start is not None else None

    routing = RoutingConfig(default=True, venues=frozenset({BYBIT}))
    inst_prov = InstrumentProviderConfig(load_all=False, load_ids=frozenset({instrument_id}))

    config_node = TradingNodeConfig(
        trader_id=TraderId("SYGNIF-EXECTEST-001"),
        logging=LoggingConfig(log_level="INFO", use_pyo3=True),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_instrument_ids=[instrument_id],
            open_check_interval_secs=5.0,
            open_check_open_only=False,
            position_check_interval_secs=5.0,
            graceful_shutdown_on_exception=True,
        ),
        risk_engine=LiveRiskEngineConfig(bypass=True),
        portfolio=PortfolioConfig(min_account_state_logging_interval_ms=1_000),
        data_clients={
            BYBIT: BybitDataClientConfig(
                # Public market data: mainnet WS; exec stays demo (see run_bybit_demo_grid_market_maker.py).
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
                use_spot_position_reports=xdefs["use_spot_position_reports"],
                position_mode=position_mode,
            ),
        },
    )

    cfg_tester = ExecTesterConfig(
        instrument_id=instrument_id,
        order_qty=order_qty,
        order_params=xdefs["order_params"] or None,
        subscribe_quotes=True,
        subscribe_trades=True,
        enable_limit_sells=xdefs["enable_limit_sells"],
        open_position_on_start_qty=open_start,
        use_post_only=True,
        reduce_only_on_stop=xdefs["reduce_only_on_stop"],
        log_data=args.verbose_data,
        log_rejected_due_post_only_as_warning=False,
    )

    node = TradingNode(config=config_node)
    node.trader.add_strategy(ExecTester(config=cfg_tester))
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
        f"[exec-tester] demo=True | instrument={instrument_id} | position_mode={pm} | "
        f"order_qty={order_qty} | open_on_start={open_start} | post_only=True",
        flush=True,
    )
    try:
        node.run()
    except KeyboardInterrupt:
        print("[exec-tester] interrupted", flush=True)
    finally:
        node.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
