#!/usr/bin/env python3
"""
Sygnif **BTC live TradingNode** (Bybit **demo** or **testnet**), ``SygnifBtcBarNodeStrategy``.

Default: **bars only, no orders**. With ``--exec-order-qty`` **or** ``--exec-adaptive`` (or
``NAUTILUS_SYGNIF_NODE_EXEC_ADAPTIVE=1``) and ``NAUTILUS_SYGNIF_NODE_EXEC_ACK=YES``, places **post-only**
limit buys below mid on bar closes (see strategy). **Adaptive** sizes from free USDT (quote) × stake
fraction ÷ mid. **Demo** uses **mainnet** public WS for data (``demo=False`` on data client) for **all** modes — required
because Bybit demo public linear WS returns 404; exec stays ``demo=True``. Same idea as ``run_bybit_demo_grid_market_maker.py``.
**Testnet** (``--testnet``) uses ``testnet=True`` on **both** data and exec clients and
``BYBIT_TESTNET_*`` keys (separate Bybit testnet API).

Env:

- **Demo (default):** ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET``
- **Testnet:** ``BYBIT_TESTNET_API_KEY`` / ``BYBIT_TESTNET_API_SECRET`` + ``--testnet``
- ``NAUTILUS_SYGNIF_NODE_EXEC_ACK=YES`` — required when exec is enabled (fixed or adaptive)
- ``NAUTILUS_SYGNIF_NODE_EXEC_ADAPTIVE`` — ``1``/``yes``/``true`` → adaptive qty (with ACK)
- ``NAUTILUS_SYGNIF_NODE_EXEC_STAKE_FRAC`` — notional fraction of free quote per order (default ``0.001``)
- ``NAUTILUS_SYGNIF_NODE_EXEC_MAX_ORDERS`` — max submits (``0`` = unlimited); overrides ``--exec-max-orders`` if set
- ``NAUTILUS_SYGNIF_NODE_EXEC_ADAPTIVE_MIN_QTY`` / ``_MAX_QTY`` — optional base-qty clamps
- ``NAUTILUS_SYGNIF_NODE_EXEC_HEDGE`` — ``1``/``yes`` for hedge mode (else merged-single is default when exec on)
- ``NAUTILUS_SYGNIF_NODE_SIDECAR_GATE`` — ``1``/``yes`` → read ``nautilus_strategy_signal.json`` (from ``nautilus-research``) to skip BUY on ``short`` bias and scale stake on ``neutral``
- ``NAUTILUS_SYGNIF_NODE_SIDECAR_JSON`` — override path (default ``/lab/btc_specialist_data/nautilus_strategy_signal.json``)
- ``NAUTILUS_SYGNIF_NODE_SIDECAR_NEUTRAL_MULT`` — adaptive stake multiplier when sidecar bias is neutral (default ``0.75``)
- ``NAUTILUS_SYGNIF_NODE_PREDICTION_GATE`` — ``1``/``yes`` → only BUY when ``btc_prediction_output.json`` is bullish (see ``prediction_agent/btc_predict_runner.py``)
- ``NAUTILUS_SYGNIF_NODE_PREDICTION_JSON`` — override path (default ``/lab/prediction_agent/btc_prediction_output.json`` in Docker)
- ``NAUTILUS_SYGNIF_NODE_PREDICTION_SIGNAL`` — ``consensus_nautilus_enhanced`` (default), ``consensus``, or ``direction_logistic``
- ``NAUTILUS_SYGNIF_NODE_PREDICTION_MIN_LOGREG_CONF`` — min LogReg confidence 0–100 when signal is ``direction_logistic``
- ``NAUTILUS_SYGNIF_NODE_PREDICTION_MAX_AGE_MIN`` — skip if ``generated_utc`` older than N minutes; ``0`` = off
- **Live train + predict** (``btc_predict_live`` in-process): ``NAUTILUS_SYGNIF_NODE_LIVE_PREDICT=1`` refits light
  RF/XGB/LogReg on rolling **5m** OHLCV after each bar (background thread); gates BUY from live consensus (sidecar
  still from JSON). Optional: ``NAUTILUS_SYGNIF_NODE_LIVE_DATA_DIR``, ``_LIVE_WINDOW``, ``_LIVE_MIN_OHLCV_ROWS``,
  ``_LIVE_RF_TREES``, ``_LIVE_XGB_N_ESTIMATORS``, ``_LIVE_DIR_C``, ``_LIVE_SYMBOL``, ``_LIVE_SEED_LIMIT``,
  ``_LIVE_MAX_OHLCV_ROWS``, ``NAUTILUS_SYGNIF_NODE_LIVE_PREDICT_JSON`` (``none``/``-`` to disable JSON write).
- **Nautilus ``LiveRiskEngine``** (not ``trade_overseer/risk_manager``): default **engaged** (``bypass=False``).
  Escape hatch: ``NAUTILUS_SYGNIF_NODE_RISK_BYPASS=1`` or ``--risk-bypass``.
  Optional: ``NAUTILUS_SYGNIF_NODE_RISK_MAX_SUBMIT_RATE`` (default ``30/00:00:01``),
  ``NAUTILUS_SYGNIF_NODE_RISK_MAX_MODIFY_RATE`` (default ``100/00:00:01``),
  ``NAUTILUS_SYGNIF_NODE_RISK_MAX_NOTIONAL_USDT`` (per-order cap for ``--instrument``; NT may skip this on margin accounts until upstream implements it).
- Optional: ``NAUTILUS_DEMO_SMOKE_AUTO_EXIT_SEC`` — SIGALRM → clean shutdown (Linux)

Examples (inside ``nautilus-research`` image, cwd ``/lab/workspace``)::

    python3 run_sygnif_btc_trading_node.py --bar-minutes 5 --max-bars 3
    python3 run_sygnif_btc_trading_node.py --testnet --bar-minutes 5
    NAUTILUS_SYGNIF_NODE_EXEC_ACK=YES python3 run_sygnif_btc_trading_node.py \\
        --exec-order-qty 0.001 --exec-offset-bps 150 --bar-minutes 5
    NAUTILUS_SYGNIF_NODE_EXEC_ACK=YES NAUTILUS_SYGNIF_NODE_EXEC_ADAPTIVE=1 \\
        python3 run_sygnif_btc_trading_node.py --exec-adaptive --bar-minutes 5
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
from decimal import Decimal
from decimal import InvalidOperation
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
            "Missing BYBIT_DEMO_API_KEY or BYBIT_DEMO_API_SECRET in environment.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _require_testnet_keys() -> None:
    k = os.environ.get("BYBIT_TESTNET_API_KEY", "").strip()
    s = os.environ.get("BYBIT_TESTNET_API_SECRET", "").strip()
    if not k or not s:
        print(
            "Missing BYBIT_TESTNET_API_KEY or BYBIT_TESTNET_API_SECRET "
            "(create keys at https://testnet.bybit.com/).",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _env_truthy(name: str) -> bool:
    v = os.environ.get(name, "").strip().lower()
    return v in ("1", "yes", "true", "on")


def _require_exec_ack() -> None:
    if os.environ.get("NAUTILUS_SYGNIF_NODE_EXEC_ACK", "").strip().upper() != "YES":
        print(
            "Refusing exec: set NAUTILUS_SYGNIF_NODE_EXEC_ACK=YES to confirm Bybit **demo/testnet** "
            "order flow (post-only limits from SygnifBtcBarNodeStrategy).",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _product_types_for_instrument(s: str):
    from nautilus_trader.core.nautilus_pyo3 import BybitProductType

    u = s.upper()
    if "LINEAR" in u:
        return (BybitProductType.LINEAR,)
    if "INVERSE" in u:
        return (BybitProductType.INVERSE,)
    if "SPOT" in u:
        return (BybitProductType.SPOT,)
    return (BybitProductType.LINEAR,)


def _bybit_position_mode_for_symbol(instrument_id, product_types: tuple, *, hedge: bool):
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
    _auto_exit_alarm()
    ap = argparse.ArgumentParser(
        description="Sygnif Bybit demo or testnet TradingNode with bar-logging strategy (no orders by default).",
    )
    ap.add_argument(
        "--instrument",
        default="BTCUSDT-LINEAR.BYBIT",
        help="InstrumentId string (USDT linear perp default)",
    )
    ap.add_argument(
        "--bar-minutes",
        type=int,
        default=5,
        help="Bar aggregation step in minutes (LAST-EXTERNAL from venue)",
    )
    ap.add_argument(
        "--max-bars",
        type=int,
        default=0,
        help="Stop after N bars (0 = run until interrupt)",
    )
    ap.add_argument(
        "--product",
        choices=("linear", "spot", "inverse", "all"),
        default="linear",
        help="Which Bybit product types to load (ignored when exec is on; type inferred from --instrument)",
    )
    ap.add_argument(
        "--exec-order-qty",
        default=None,
        metavar="QTY",
        help="Fixed base qty for post-only limit buys (requires NAUTILUS_SYGNIF_NODE_EXEC_ACK=YES)",
    )
    ap.add_argument(
        "--exec-adaptive",
        action="store_true",
        help="Size each order from free quote balance × stake fraction (requires ACK; env ADAPTIVE=1 also works)",
    )
    ap.add_argument(
        "--exec-stake-frac",
        type=float,
        default=None,
        metavar="FRAC",
        help="Adaptive: fraction of free quote notional per order (default 0.001 or NAUTILUS_SYGNIF_NODE_EXEC_STAKE_FRAC)",
    )
    ap.add_argument(
        "--exec-offset-bps",
        type=int,
        default=100,
        help="Buy limit below mid, in basis points (when exec is on)",
    )
    ap.add_argument(
        "--exec-max-orders",
        type=int,
        default=1,
        help="Max bar-triggered entry orders to submit (0 = unlimited)",
    )
    ap.add_argument(
        "--merged-single",
        action="store_true",
        help="Bybit one-way (MergedSingle) for linear/inverse when exec is on",
    )
    ap.add_argument(
        "--hedge",
        action="store_true",
        help="Bybit hedge (both sides) when exec is on; default without this is merged-single for linear/inverse",
    )
    ap.add_argument(
        "--testnet",
        action="store_true",
        help="Use Bybit testnet (BYBIT_TESTNET_API_KEY / BYBIT_TESTNET_API_SECRET); not demo trading",
    )
    ap.add_argument(
        "--risk-bypass",
        action="store_true",
        help="Disable Nautilus LiveRiskEngine pre-trade checks and throttles (not recommended)",
    )
    ap.add_argument(
        "--prediction-gate",
        action="store_true",
        help="Only BUY when btc_prediction_output.json agrees (or NAUTILUS_SYGNIF_NODE_PREDICTION_GATE=1)",
    )
    ap.add_argument(
        "--prediction-json",
        default=None,
        metavar="PATH",
        help="Path to btc_prediction_output.json (default: env or Docker /lab/prediction_agent/...)",
    )
    ap.add_argument(
        "--prediction-signal",
        choices=("consensus_nautilus_enhanced", "consensus", "direction_logistic"),
        default=None,
        help="Which field gates BUY (default: consensus_nautilus_enhanced or env)",
    )
    ap.add_argument(
        "--prediction-min-logreg-conf",
        type=float,
        default=None,
        metavar="PCT",
        help="With direction_logistic: min confidence 0-100 (default 0 or env)",
    )
    ap.add_argument(
        "--prediction-max-age-min",
        type=int,
        default=None,
        metavar="MIN",
        help="Max age of generated_utc in minutes; 0 disables (default env or 0)",
    )
    ap.add_argument(
        "--live-predict",
        action="store_true",
        help="Retrain + predict in-process each bar (or NAUTILUS_SYGNIF_NODE_LIVE_PREDICT=1)",
    )
    args = ap.parse_args()
    if args.testnet:
        _require_testnet_keys()
    else:
        _require_demo_keys()
    exec_adaptive = bool(args.exec_adaptive) or _env_truthy("NAUTILUS_SYGNIF_NODE_EXEC_ADAPTIVE")
    fixed_qty = args.exec_order_qty is not None and str(args.exec_order_qty).strip() != ""
    if exec_adaptive and fixed_qty:
        print(
            "Use either --exec-adaptive or --exec-order-qty, not both.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    use_exec = fixed_qty or exec_adaptive
    if use_exec:
        _require_exec_ack()
    stake_raw = os.environ.get("NAUTILUS_SYGNIF_NODE_EXEC_STAKE_FRAC", "0.001").strip()
    if not stake_raw:
        stake_raw = "0.001"
    try:
        stake_frac = float(stake_raw)
    except ValueError:
        print(f"Invalid NAUTILUS_SYGNIF_NODE_EXEC_STAKE_FRAC={stake_raw!r}", file=sys.stderr)
        raise SystemExit(2) from None
    if args.exec_stake_frac is not None:
        stake_frac = float(args.exec_stake_frac)
    if exec_adaptive and stake_frac <= 0.0:
        print("Adaptive stake fraction must be > 0.", file=sys.stderr)
        raise SystemExit(2)
    max_orders = max(0, int(args.exec_max_orders))
    mo_env = os.environ.get("NAUTILUS_SYGNIF_NODE_EXEC_MAX_ORDERS", "").strip()
    if mo_env:
        try:
            max_orders = max(0, int(mo_env))
        except ValueError:
            print(f"Invalid NAUTILUS_SYGNIF_NODE_EXEC_MAX_ORDERS={mo_env!r}", file=sys.stderr)
            raise SystemExit(2) from None
    adaptive_min = os.environ.get("NAUTILUS_SYGNIF_NODE_EXEC_ADAPTIVE_MIN_QTY", "").strip() or None
    adaptive_max = os.environ.get("NAUTILUS_SYGNIF_NODE_EXEC_ADAPTIVE_MAX_QTY", "").strip() or None
    sidecar_gate = _env_truthy("NAUTILUS_SYGNIF_NODE_SIDECAR_GATE")
    sidecar_path = os.environ.get("NAUTILUS_SYGNIF_NODE_SIDECAR_JSON", "").strip()
    if not sidecar_path:
        sidecar_path = "/lab/btc_specialist_data/nautilus_strategy_signal.json"
    sn_raw = os.environ.get("NAUTILUS_SYGNIF_NODE_SIDECAR_NEUTRAL_MULT", "0.75").strip()
    try:
        sidecar_neutral_mult = float(sn_raw)
    except ValueError:
        print(
            f"Invalid NAUTILUS_SYGNIF_NODE_SIDECAR_NEUTRAL_MULT={sn_raw!r}",
            file=sys.stderr,
        )
        raise SystemExit(2) from None

    prediction_gate = bool(args.prediction_gate) or _env_truthy("NAUTILUS_SYGNIF_NODE_PREDICTION_GATE")
    pred_json = (args.prediction_json or "").strip()
    if not pred_json:
        pred_json = os.environ.get("NAUTILUS_SYGNIF_NODE_PREDICTION_JSON", "").strip()
    if not pred_json:
        pred_json = "/lab/prediction_agent/btc_prediction_output.json"
    pred_signal = (args.prediction_signal or os.environ.get("NAUTILUS_SYGNIF_NODE_PREDICTION_SIGNAL", "")).strip()
    if not pred_signal:
        pred_signal = "consensus_nautilus_enhanced"
    pred_signal_l = pred_signal.lower().strip()
    allowed_sig = frozenset({"consensus_nautilus_enhanced", "consensus", "direction_logistic"})
    if pred_signal_l not in allowed_sig:
        print(
            f"Invalid NAUTILUS_SYGNIF_NODE_PREDICTION_SIGNAL={pred_signal!r} "
            f"(use one of {sorted(allowed_sig)})",
            file=sys.stderr,
        )
        raise SystemExit(2)
    pm_raw = os.environ.get("NAUTILUS_SYGNIF_NODE_PREDICTION_MIN_LOGREG_CONF", "0").strip()
    try:
        pred_min_logreg = float(pm_raw)
    except ValueError:
        print(f"Invalid NAUTILUS_SYGNIF_NODE_PREDICTION_MIN_LOGREG_CONF={pm_raw!r}", file=sys.stderr)
        raise SystemExit(2) from None
    if args.prediction_min_logreg_conf is not None:
        pred_min_logreg = float(args.prediction_min_logreg_conf)
    pa_env = os.environ.get("NAUTILUS_SYGNIF_NODE_PREDICTION_MAX_AGE_MIN", "").strip()
    pred_max_age = 0
    if pa_env:
        try:
            pred_max_age = max(0, int(pa_env))
        except ValueError:
            print(f"Invalid NAUTILUS_SYGNIF_NODE_PREDICTION_MAX_AGE_MIN={pa_env!r}", file=sys.stderr)
            raise SystemExit(2) from None
    if args.prediction_max_age_min is not None:
        pred_max_age = max(0, int(args.prediction_max_age_min))

    live_predict_train = bool(args.live_predict) or _env_truthy(
        "NAUTILUS_SYGNIF_NODE_LIVE_PREDICT"
    )
    live_data_dir = os.environ.get("NAUTILUS_SYGNIF_NODE_LIVE_DATA_DIR", "").strip()
    if not live_data_dir:
        live_data_dir = "/lab/btc_specialist_data"
    try:
        live_window = max(3, int(os.environ.get("NAUTILUS_SYGNIF_NODE_LIVE_WINDOW", "5").strip() or "5"))
    except ValueError:
        print("Invalid NAUTILUS_SYGNIF_NODE_LIVE_WINDOW", file=sys.stderr)
        raise SystemExit(2) from None
    try:
        live_min_ohlcv = max(
            50,
            int(os.environ.get("NAUTILUS_SYGNIF_NODE_LIVE_MIN_OHLCV_ROWS", "320").strip() or "320"),
        )
    except ValueError:
        print("Invalid NAUTILUS_SYGNIF_NODE_LIVE_MIN_OHLCV_ROWS", file=sys.stderr)
        raise SystemExit(2) from None
    try:
        live_rf = max(10, int(os.environ.get("NAUTILUS_SYGNIF_NODE_LIVE_RF_TREES", "64").strip() or "64"))
    except ValueError:
        print("Invalid NAUTILUS_SYGNIF_NODE_LIVE_RF_TREES", file=sys.stderr)
        raise SystemExit(2) from None
    try:
        live_xgb = max(
            20, int(os.environ.get("NAUTILUS_SYGNIF_NODE_LIVE_XGB_N_ESTIMATORS", "120").strip() or "120")
        )
    except ValueError:
        print("Invalid NAUTILUS_SYGNIF_NODE_LIVE_XGB_N_ESTIMATORS", file=sys.stderr)
        raise SystemExit(2) from None
    try:
        live_dir_c = float(os.environ.get("NAUTILUS_SYGNIF_NODE_LIVE_DIR_C", "1.0").strip() or "1.0")
    except ValueError:
        print("Invalid NAUTILUS_SYGNIF_NODE_LIVE_DIR_C", file=sys.stderr)
        raise SystemExit(2) from None
    live_symbol = os.environ.get("NAUTILUS_SYGNIF_NODE_LIVE_SYMBOL", "BTCUSDT").strip() or "BTCUSDT"
    try:
        live_seed = max(
            50, int(os.environ.get("NAUTILUS_SYGNIF_NODE_LIVE_SEED_LIMIT", "800").strip() or "800")
        )
    except ValueError:
        print("Invalid NAUTILUS_SYGNIF_NODE_LIVE_SEED_LIMIT", file=sys.stderr)
        raise SystemExit(2) from None
    try:
        live_max_rows = max(
            500, int(os.environ.get("NAUTILUS_SYGNIF_NODE_LIVE_MAX_OHLCV_ROWS", "1500").strip() or "1500")
        )
    except ValueError:
        print("Invalid NAUTILUS_SYGNIF_NODE_LIVE_MAX_OHLCV_ROWS", file=sys.stderr)
        raise SystemExit(2) from None
    lo_raw = os.environ.get(
        "NAUTILUS_SYGNIF_NODE_LIVE_PREDICT_JSON",
        "/lab/prediction_agent/btc_prediction_output.json",
    ).strip()
    if lo_raw.lower() in ("-", "none", "off", "0"):
        live_out_json = None
    else:
        live_out_json = lo_raw

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

    from sygnif_btc_bar_node_strategy import SygnifBtcBarNodeConfig
    from sygnif_btc_bar_node_strategy import SygnifBtcBarNodeStrategy

    instrument_id = InstrumentId.from_str(args.instrument)
    bar_type_str = f"{instrument_id}-{args.bar_minutes}-MINUTE-LAST-EXTERNAL"

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
    if use_exec:
        product_types = _product_types_for_instrument(args.instrument)
    else:
        product_types = product_map[args.product]

    is_spot = len(product_types) == 1 and product_types[0] == BybitProductType.SPOT
    merged_single = bool(args.merged_single)
    if use_exec and not merged_single and not args.hedge and not _env_truthy("NAUTILUS_SYGNIF_NODE_EXEC_HEDGE"):
        merged_single = True
    if args.hedge or _env_truthy("NAUTILUS_SYGNIF_NODE_EXEC_HEDGE"):
        merged_single = False
    hedge = not merged_single
    position_mode = (
        _bybit_position_mode_for_symbol(instrument_id, product_types, hedge=hedge)
        if use_exec
        else None
    )

    routing = RoutingConfig(default=True, venues=frozenset({BYBIT}))
    inst_prov = InstrumentProviderConfig(load_all=False, load_ids=frozenset({instrument_id}))

    risk_bypass = bool(args.risk_bypass) or _env_truthy("NAUTILUS_SYGNIF_NODE_RISK_BYPASS")
    submit_rate = (
        os.environ.get("NAUTILUS_SYGNIF_NODE_RISK_MAX_SUBMIT_RATE", "").strip() or "30/00:00:01"
    )
    modify_rate = (
        os.environ.get("NAUTILUS_SYGNIF_NODE_RISK_MAX_MODIFY_RATE", "").strip() or "100/00:00:01"
    )
    max_notional_per_order: dict[str, int] = {}
    raw_nom = os.environ.get("NAUTILUS_SYGNIF_NODE_RISK_MAX_NOTIONAL_USDT", "").strip()
    if raw_nom:
        try:
            max_notional_per_order[str(instrument_id)] = int(Decimal(raw_nom))
        except (InvalidOperation, ValueError) as exc:
            print(
                f"Invalid NAUTILUS_SYGNIF_NODE_RISK_MAX_NOTIONAL_USDT={raw_nom!r}: {exc}",
                file=sys.stderr,
            )
            raise SystemExit(2) from None

    risk_engine_cfg = LiveRiskEngineConfig(
        bypass=risk_bypass,
        max_order_submit_rate=submit_rate,
        max_order_modify_rate=modify_rate,
        max_notional_per_order=max_notional_per_order,
    )

    # Demo: **always** mainnet public WS for market data (`stream-demo` public linear returns 404); exec stays demo.
    # Testnet: testnet endpoints for both clients.
    if args.testnet:
        data_demo = False
        data_testnet = True
        exec_demo = False
        exec_testnet = True
    else:
        data_demo = False
        data_testnet = False
        exec_demo = True
        exec_testnet = False

    exec_engine_kw = dict(
        reconciliation=True,
        reconciliation_instrument_ids=[instrument_id],
        graceful_shutdown_on_exception=True,
    )
    if use_exec:
        exec_engine_kw.update(
            open_check_interval_secs=10.0,
            open_check_open_only=True,
        )

    exec_client_kw = dict(
        demo=exec_demo,
        testnet=exec_testnet,
        product_types=product_types,
        instrument_provider=inst_prov,
        routing=routing,
    )
    if use_exec:
        exec_client_kw["use_spot_position_reports"] = bool(is_spot)
        exec_client_kw["position_mode"] = position_mode

    config_node = TradingNodeConfig(
        trader_id=TraderId("SYGNIF-BTC-NODE-001"),
        logging=LoggingConfig(log_level="INFO", use_pyo3=True),
        exec_engine=LiveExecEngineConfig(**exec_engine_kw),
        risk_engine=risk_engine_cfg,
        portfolio=PortfolioConfig(),
        data_clients={
            BYBIT: BybitDataClientConfig(
                demo=data_demo,
                testnet=data_testnet,
                api_key=None,
                api_secret=None,
                product_types=product_types,
                instrument_provider=inst_prov,
                routing=routing,
            ),
        },
        exec_clients={
            BYBIT: BybitExecClientConfig(**exec_client_kw),
        },
    )

    node = TradingNode(config=config_node)
    strat = SygnifBtcBarNodeStrategy(
        config=SygnifBtcBarNodeConfig(
            bar_type=bar_type_str,
            max_bars=max(0, args.max_bars),
            enable_exec=use_exec,
            order_qty_str=str(args.exec_order_qty).strip() if fixed_qty else None,
            exec_adaptive=exec_adaptive,
            adaptive_stake_fraction=stake_frac,
            adaptive_min_qty_str=adaptive_min,
            adaptive_max_qty_str=adaptive_max,
            limit_offset_bps=max(1, args.exec_offset_bps),
            max_entry_orders=max_orders,
            sidecar_gate=sidecar_gate,
            sidecar_signal_path=sidecar_path,
            sidecar_neutral_stake_mult=sidecar_neutral_mult,
            prediction_gate=prediction_gate,
            prediction_json_path=pred_json,
            prediction_signal=pred_signal_l,
            prediction_min_logreg_confidence=pred_min_logreg,
            prediction_max_age_minutes=pred_max_age,
            live_predict_train=live_predict_train,
            live_predict_data_dir=live_data_dir,
            live_predict_window=live_window,
            live_predict_min_ohlcv_rows=live_min_ohlcv,
            live_predict_rf_trees=live_rf,
            live_predict_xgb_n_estimators=live_xgb,
            live_predict_dir_C=live_dir_c,
            live_predict_symbol=live_symbol,
            live_predict_seed_limit=live_seed,
            live_predict_output_json=live_out_json,
            live_predict_max_ohlcv_rows=live_max_rows,
        ),
    )
    node.trader.add_strategy(strat)

    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.build()

    if use_exec:
        if exec_adaptive:
            exec_desc = f"post_only BUY adaptive stake_frac={stake_frac}"
        else:
            exec_desc = f"post_only BUY qty={args.exec_order_qty}"
    else:
        exec_desc = "off"
    print(
        f"[sygnif-btc-node] TradingNode built | instrument={instrument_id} | "
        f"bar_type={bar_type_str} | max_bars={args.max_bars or '∞'} | "
        f"exec={exec_desc} | merged_single={merged_single} | "
        f"risk_engine_bypass={risk_bypass} | sidecar_gate={sidecar_gate} | "
        f"prediction_gate={prediction_gate} signal={pred_signal_l} | "
        f"live_predict_train={live_predict_train} | "
        f"venue={'testnet' if args.testnet else 'demo'} | "
        f"data_demo={data_demo} data_testnet={data_testnet}",
        flush=True,
    )
    try:
        node.run()
    except KeyboardInterrupt:
        print("[sygnif-btc-node] interrupted", flush=True)
    finally:
        node.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
