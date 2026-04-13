#!/usr/bin/env python3
"""
Bybit **demo trading** live ``TradingNode``: register Nautilus Bybit adapters, connect, log quotes.

**Does not place orders** (uses ``BybitDemoQuoteSmoke``). Validates:

- ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET`` (Nautilus reads these when ``demo=True``)
- ``BybitLiveDataClientFactory`` + ``BybitLiveExecClientFactory`` registration
- Instrument load for ``BTCUSDT-LINEAR.BYBIT`` (override with ``--instrument``)

Run inside ``nautilus-research`` (``/lab/workspace``) or any env with ``nautilus_trader`` installed.

Optional auto-exit (Linux): ``NAUTILUS_DEMO_SMOKE_AUTO_EXIT_SEC=30`` sends SIGALRM → ``KeyboardInterrupt`` so ``node.dispose()`` runs after ``timeout``-style shutdown.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

_LAB = Path(__file__).resolve().parent
if str(_LAB) not in sys.path:
    sys.path.insert(0, str(_LAB))


def _auto_exit_alarm() -> None:
    raw = os.environ.get("NAUTILUS_DEMO_SMOKE_AUTO_EXIT_SEC", "").strip()
    if not raw:
        return
    try:
        sec = max(1, int(raw))
    except ValueError:
        return
    if hasattr(signal, "SIGALRM"):

        def _handler(_signum, _frame) -> None:  # noqa: ARG001
            raise KeyboardInterrupt

        signal.signal(signal.SIGALRM, _handler)
        signal.alarm(sec)


def _require_demo_keys() -> None:
    k = os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
    s = os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
    if not k or not s:
        print(
            "Missing BYBIT_DEMO_API_KEY or BYBIT_DEMO_API_SECRET in environment "
            "(Bybit Demo Trading keys from the Bybit demo UI).",
            file=sys.stderr,
        )
        raise SystemExit(2)


def main() -> int:
    _auto_exit_alarm()
    ap = argparse.ArgumentParser(description="Bybit demo TradingNode smoke (quotes only, no orders).")
    ap.add_argument(
        "--instrument",
        default="BTCUSDT-LINEAR.BYBIT",
        help="InstrumentId string (default: USDT linear BTC perp on Bybit venue)",
    )
    ap.add_argument(
        "--max-ticks",
        type=int,
        default=8,
        help="Stop strategy after this many quote ticks (then disconnect)",
    )
    ap.add_argument(
        "--product",
        choices=("linear", "spot", "inverse", "all"),
        default="linear",
        help="Which Bybit product types to load (smaller = faster startup)",
    )
    args = ap.parse_args()
    _require_demo_keys()

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

    from bybit_demo_quote_smoke import BybitDemoQuoteSmoke
    from bybit_demo_quote_smoke import BybitDemoQuoteSmokeConfig

    instrument_id = InstrumentId.from_str(args.instrument)
    product_map = {
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
    product_types = product_map[args.product]

    routing = RoutingConfig(default=True, venues=frozenset({BYBIT}))
    inst_prov = InstrumentProviderConfig(load_all=False, load_ids=frozenset({instrument_id}))

    config_node = TradingNodeConfig(
        trader_id=TraderId("SYGNIF-DEMO-001"),
        logging=LoggingConfig(log_level="INFO", use_pyo3=True),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_instrument_ids=[instrument_id],
            graceful_shutdown_on_exception=True,
        ),
        risk_engine=LiveRiskEngineConfig(bypass=True),
        portfolio=PortfolioConfig(),
        data_clients={
            BYBIT: BybitDataClientConfig(
                demo=True,
                testnet=False,
                api_key=None,
                api_secret=None,
                product_types=product_types,
                instrument_provider=inst_prov,
                routing=routing,
            ),
        },
        exec_clients={
            BYBIT: BybitExecClientConfig(
                demo=True,
                testnet=False,
                api_key=None,
                api_secret=None,
                product_types=product_types,
                instrument_provider=inst_prov,
                routing=routing,
            ),
        },
    )

    node = TradingNode(config=config_node)
    smoke = BybitDemoQuoteSmoke(
        config=BybitDemoQuoteSmokeConfig(
            instrument_id=instrument_id,
            max_ticks=args.max_ticks,
        ),
    )
    node.trader.add_strategy(smoke)

    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.build()

    print(
        f"[bybit-demo] TradingNode built | instrument={instrument_id} | "
        f"demo=True | max_ticks={args.max_ticks}",
        flush=True,
    )
    try:
        node.run()
    except KeyboardInterrupt:
        print("[bybit-demo] interrupted", flush=True)
    finally:
        node.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
