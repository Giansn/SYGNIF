"""
Sygnif **live bar** template for Nautilus ``TradingNode`` (Bybit demo).

**Order path:** entries go through Nautilus only — ``submit_order`` on this strategy →
``BybitLiveExecClient`` (demo/testnet). Sygnif BTC demo **does not** use Freqtrade
``/forceenter`` for this stack; see ``scripts/btc_analysis_forceenter.py`` for an optional
separate Freqtrade-only helper.

Subscribes to exchange OHLCV bars and logs OHLCV. Optional **demo execution**: with ``enable_exec``, subscribes to quotes and places up to
``max_entry_orders`` **post-only** limit **buy** orders priced ``limit_offset_bps``
below the last mid. Size is either fixed ``order_qty_str`` or **adaptive** from free
quote balance × ``adaptive_stake_fraction`` (simple probe, not production alpha).

Optional **research sidecar** (``nautilus-research`` → ``nautilus_strategy_signal.json``): with
``sidecar_gate``, skip BUY when bias is ``short``; scale adaptive notional by
``sidecar_neutral_stake_mult`` when bias is ``neutral``.

Optional **BTC predict gate** (``btc_predict_runner`` → ``btc_prediction_output.json``): with
``prediction_gate``, allow post-only BUY only when the chosen signal is bullish (``BULLISH`` /
``STRONG_BULLISH`` for consensus fields, or ``direction_logistic`` label ``UP`` with min confidence).

Optional **live train + predict** (``btc_predict_live``): with ``live_predict_train``, each bar
schedules a background refit on rolling **5m** OHLCV (seeded from public Bybit klines), updates the
same consensus fields, and optionally rewrites ``btc_prediction_output.json``. Trade decision on bar
*N* uses the model fit completed after bar *N-1* (no lookahead).

When ``max_bars > 0`` and the count is reached, raises ``KeyboardInterrupt`` so the
runner's ``finally: node.dispose()`` runs (same pattern as ``BybitDemoQuoteSmoke``).
"""

from __future__ import annotations

import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timezone
from pathlib import Path

from nautilus_trader.adapters.bybit import BYBIT
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.events import OrderRejected
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class SygnifBtcBarNodeConfig(StrategyConfig, frozen=True):
    bar_type: str
    """Full bar type string, e.g. ``BTCUSDT-LINEAR.BYBIT-5-MINUTE-LAST-EXTERNAL``."""
    max_bars: int = 0
    """0 = run until process stop; else exit node after this many bars."""
    enable_exec: bool = False
    """If True, subscribe to quotes and may submit orders (runner must set ACK)."""
    order_qty_str: str | None = None
    """Base quantity string for limit buys (venue lot); ignored when ``exec_adaptive``."""
    exec_adaptive: bool = False
    """If True, size each order from free quote balance × ``adaptive_stake_fraction`` ÷ mid."""
    adaptive_stake_fraction: float = 0.001
    """Fraction of free quote currency (e.g. USDT) notional per order (0.001 = 0.1%)."""
    adaptive_min_qty_str: str | None = None
    """Optional floor on base qty (string for ``Quantity.from_str``)."""
    adaptive_max_qty_str: str | None = None
    """Optional cap on base qty (string for ``Quantity.from_str``)."""
    adaptive_max_notional_usdt: float | None = None
    """If set (>0), clamp adaptive quote notional to this USDT cap per order (before qty rounding)."""
    limit_offset_bps: int = 100
    """Buy limit = mid * (1 - bps/10000), post-only away from touch."""
    max_entry_orders: int = 1
    """Cap bar-triggered entry submits; ``0`` = unlimited (not fill count)."""
    sidecar_gate: bool = False
    """If True, read ``sidecar_signal_path`` (Nautilus sidecar JSON) to filter/size demo buys."""
    sidecar_signal_path: str = "/lab/btc_specialist_data/nautilus_strategy_signal.json"
    """Path to ``nautilus_strategy_signal.json`` (same dir as training OHLCV in Docker)."""
    sidecar_neutral_stake_mult: float = 0.75
    """When sidecar bias is ``neutral``, multiply adaptive stake fraction by this (0–1)."""
    prediction_gate: bool = False
    """If True, require ``btc_prediction_output.json`` to allow a demo BUY (see ``prediction_signal``)."""
    prediction_json_path: str = "/lab/prediction_agent/btc_prediction_output.json"
    """Path to ``btc_prediction_output.json`` (Docker: RO mount of ``prediction_agent/``)."""
    prediction_signal: str = "consensus_nautilus_enhanced"
    """
    ``consensus_nautilus_enhanced`` | ``consensus`` | ``direction_logistic``.
    BUY only for ``BULLISH`` / ``STRONG_BULLISH``, or LogReg ``UP`` (with min confidence).
    """
    prediction_min_logreg_confidence: float = 0.0
    """If using ``direction_logistic``, require ``confidence`` ≥ this (0–100 scale in JSON)."""
    prediction_max_age_minutes: int = 0
    """Reject prediction if ``generated_utc`` is older than this many minutes; ``0`` = no age check."""
    live_predict_train: bool = False
    """If True, refit RF/XGB/LogReg on rolling 5m bars (``btc_predict_live``) and gate on live consensus."""
    live_predict_data_dir: str = "/lab/btc_specialist_data"
    """Directory for ``load_nautilus_research_hints`` (sidecar + bundle JSON)."""
    live_predict_window: int = 5
    """Windowed feature depth (bars), same semantics as ``btc_predict_runner``."""
    live_predict_min_ohlcv_rows: int = 320
    """Minimum OHLCV rows before live training runs (after TA ``dropna`` needs long history)."""
    live_predict_rf_trees: int = 64
    live_predict_xgb_n_estimators: int = 120
    live_predict_dir_C: float = 1.0
    live_predict_symbol: str = "BTCUSDT"
    """Bybit **linear** symbol for public kline seed."""
    live_predict_seed_limit: int = 800
    """Number of 5m klines to pull for cold start."""
    live_predict_output_json: str | None = "/lab/prediction_agent/btc_prediction_output.json"
    """Write-through JSON for dashboards; ``None`` to skip disk."""
    live_predict_max_ohlcv_rows: int = 1500
    """Trim in-memory OHLCV to this many rows to bound fit time."""


class SygnifBtcBarNodeStrategy(Strategy):
    def __init__(self, config: SygnifBtcBarNodeConfig) -> None:
        super().__init__(config)
        self._seen = 0
        self._bar_type = BarType.from_str(config.bar_type)
        self._instrument: Instrument | None = None
        self._price_precision: int | None = None
        self._last_mid: Price | None = None
        self._entry_submits = 0
        self._live_executor: ThreadPoolExecutor | None = None
        self._live_pd_df = None
        self._live_out: dict | None = None

    def _ensure_prediction_agent_path(self) -> None:
        raw = os.environ.get("NAUTILUS_PREDICTION_AGENT_PATH", "").strip()
        if raw and os.path.isdir(raw) and raw not in sys.path:
            sys.path.insert(0, raw)
            return
        if os.path.isdir("/lab/prediction_agent") and "/lab/prediction_agent" not in sys.path:
            sys.path.insert(0, "/lab/prediction_agent")
            return
        here = Path(__file__).resolve().parent
        repo_pa = (here.parent.parent / "prediction_agent").resolve()
        if repo_pa.is_dir() and str(repo_pa) not in sys.path:
            sys.path.insert(0, str(repo_pa))

    @staticmethod
    def _bar_px(x) -> float:
        if hasattr(x, "as_double"):
            return float(x.as_double())
        return float(x)

    def on_start(self) -> None:
        self.subscribe_bars(self._bar_type)
        ins_id = self._bar_type.instrument_id
        self._instrument = self.cache.instrument(ins_id)
        if self._instrument is None:
            self.log.error(f"No instrument in cache for {ins_id}")
            self.stop()
            return
        self._price_precision = self._instrument.price_precision
        if self.config.enable_exec:
            self.subscribe_quote_ticks(ins_id)

        if self.config.live_predict_train:
            self._ensure_prediction_agent_path()
            try:
                import btc_predict_live as bpl  # type: ignore[import-not-found]
            except ImportError as exc:
                self.log.error(f"Live predict: cannot import btc_predict_live ({exc!s})")
            else:
                self._live_executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="sygnif-live-ml",
                )
                try:
                    self._live_pd_df = bpl.fetch_linear_5m_klines(
                        self.config.live_predict_symbol,
                        limit=self.config.live_predict_seed_limit,
                    )
                    allow, enhanced, out = bpl.fit_predict_live(
                        self._live_pd_df,
                        window=self.config.live_predict_window,
                        data_dir=self.config.live_predict_data_dir,
                        rf_trees=self.config.live_predict_rf_trees,
                        xgb_estimators=self.config.live_predict_xgb_n_estimators,
                        dir_C=self.config.live_predict_dir_C,
                        write_json_path=self.config.live_predict_output_json,
                        linear_symbol=self.config.live_predict_symbol,
                    )
                    self._live_out = out
                    self.log.info(
                        f"Live predict primed rows={len(self._live_pd_df)} "
                        f"allow_buy={allow} enhanced={enhanced}"
                    )
                except Exception as exc:
                    self.log.error(f"Live predict seed/train failed: {exc!s}")
                    self._live_out = None

    def on_stop(self) -> None:
        if self._live_executor is not None:
            self._live_executor.shutdown(wait=False, cancel_futures=True)
            self._live_executor = None
        if self._instrument is not None and self.config.enable_exec:
            self.cancel_all_orders(self._instrument.id)
            self.unsubscribe_quote_ticks(self._instrument.id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if not self.config.enable_exec or self._price_precision is None:
            return
        mid_f = (float(tick.bid_price) + float(tick.ask_price)) / 2.0
        self._last_mid = Price(mid_f, self._price_precision)

    def on_order_filled(self, event: OrderFilled) -> None:
        trade_id = getattr(event, "trade_id", None)
        position_id = getattr(event, "position_id", None)
        parts = [
            "SYGNIF_OPENED_TRADE",
            f"instrument={event.instrument_id}",
            f"side={event.order_side}",
            f"qty={event.last_qty}",
            f"px={event.last_px}",
            f"client_order_id={event.client_order_id}",
        ]
        if trade_id is not None:
            parts.append(f"trade_id={trade_id}")
        if position_id is not None:
            parts.append(f"position_id={position_id}")
        print(" ".join(parts), flush=True)
        self.log.info(
            f"Order filled client_order_id={event.client_order_id} side={event.order_side} "
            f"last_qty={event.last_qty} last_px={event.last_px} trade_id={trade_id} "
            f"position_id={position_id}"
        )

    def on_order_rejected(self, event: OrderRejected) -> None:
        self.log.warning(
            f"Order rejected client_order_id={event.client_order_id} reason={event.reason}"
        )

    def on_bar(self, bar: Bar) -> None:
        self._seen += 1
        self.log.info(
            f"Bar #{self._seen} ts={bar.ts_event} O={bar.open} H={bar.high} L={bar.low} "
            f"C={bar.close} V={bar.volume}"
        )
        # Order: trade on model state from **previous** bar; then append this bar and retrain async.
        self._maybe_submit_demo_entry()
        if self.config.live_predict_train and self._live_executor is not None:
            self._append_bar_and_schedule_live_train(bar)
        max_bars = self.config.max_bars
        if max_bars > 0 and self._seen >= max_bars:
            self.log.info("max_bars reached — exiting node (KeyboardInterrupt)")
            raise KeyboardInterrupt

    def _append_bar_and_schedule_live_train(self, bar: Bar) -> None:
        if self._live_executor is None:
            return
        import btc_predict_live as bpl  # type: ignore[import-not-found]

        if self._live_pd_df is None:
            self._live_pd_df = bpl.fetch_linear_5m_klines(
                self.config.live_predict_symbol,
                limit=self.config.live_predict_seed_limit,
            )
        self._live_pd_df = bpl.append_bar_row(
            self._live_pd_df,
            ts_event_ns=int(bar.ts_event),
            open_=self._bar_px(bar.open),
            high=self._bar_px(bar.high),
            low=self._bar_px(bar.low),
            close=self._bar_px(bar.close),
            volume=self._bar_px(bar.volume),
        )
        cap = max(500, int(self.config.live_predict_max_ohlcv_rows))
        if len(self._live_pd_df) > cap:
            self._live_pd_df = self._live_pd_df.iloc[-cap:].reset_index(drop=True)

        df_snapshot = self._live_pd_df.copy()
        window = int(self.config.live_predict_window)
        data_dir = self.config.live_predict_data_dir
        rf_t = int(self.config.live_predict_rf_trees)
        xgb_n = int(self.config.live_predict_xgb_n_estimators)
        dir_c = float(self.config.live_predict_dir_C)
        out_path = self.config.live_predict_output_json
        min_rows = int(self.config.live_predict_min_ohlcv_rows)

        def _job() -> None:
            try:
                if len(df_snapshot) < min_rows:
                    return
                _, _, out = bpl.fit_predict_live(
                    df_snapshot,
                    window=window,
                    data_dir=data_dir,
                    rf_trees=rf_t,
                    xgb_estimators=xgb_n,
                    dir_C=dir_c,
                    write_json_path=out_path,
                    linear_symbol=self.config.live_predict_symbol,
                )
                self._live_out = out
            except Exception as exc:
                self.log.error(f"Live predict retrain failed: {exc!s}")

        self._live_executor.submit(_job)

    def _maybe_submit_demo_entry(self) -> None:
        if not self.config.enable_exec:
            return
        if self._instrument is None or self._last_mid is None:
            return
        max_o = self.config.max_entry_orders
        if max_o > 0 and self._entry_submits >= max_o:
            return
        bias, sidecar_stake_mult = self._sidecar_bias_and_stake_mult()
        if self.config.sidecar_gate and bias == "short":
            self.log.info("Sidecar gate: bias=short — skip demo BUY")
            return
        if not self._prediction_allows_buy():
            return
        if self.config.exec_adaptive:
            qty = self._adaptive_entry_qty(stake_mult=sidecar_stake_mult)
            if qty is None:
                return
        else:
            qty_s = (self.config.order_qty_str or "").strip()
            if not qty_s:
                return
            try:
                qty = Quantity.from_str(qty_s)
            except ValueError:
                self.log.error(f"Invalid order_qty_str {qty_s!r}")
                return
        mid_f = float(self._last_mid)
        bps = self.config.limit_offset_bps
        raw_px = mid_f * (1.0 - bps / 10_000.0)
        price = Price(raw_px, self._price_precision)
        order = self.order_factory.limit(
            instrument_id=self._instrument.id,
            order_side=OrderSide.BUY,
            quantity=qty,
            price=price,
            time_in_force=TimeInForce.GTC,
            post_only=True,
        )
        self.submit_order(order)
        self._entry_submits += 1
        self.log.info(
            f"Submitted post-only BUY #{self._entry_submits} qty={qty} price={price} "
            f"(mid≈{self._last_mid}, offset {bps} bps)"
        )

    def _sidecar_bias_and_stake_mult(self) -> tuple[str | None, float]:
        """Return (bias, multiplier on adaptive stake). No gate or missing file → (None, 1.0)."""
        if not self.config.sidecar_gate:
            return None, 1.0
        path = Path(self.config.sidecar_signal_path)
        if not path.is_file():
            return None, 1.0
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None, 1.0
        bias = raw.get("bias") if isinstance(raw, dict) else None
        if not isinstance(bias, str):
            return None, 1.0
        bias_l = bias.lower().strip()
        if bias_l == "short":
            return "short", 0.0
        if bias_l == "neutral":
            m = float(self.config.sidecar_neutral_stake_mult)
            return "neutral", max(0.0, min(m, 1.0))
        return bias_l, 1.0

    def _prediction_allows_buy(self) -> bool:
        if self.config.live_predict_train:
            return self._live_prediction_allows_buy()
        if not self.config.prediction_gate:
            return True
        path = Path(self.config.prediction_json_path)
        if not path.is_file():
            self.log.warning(f"Prediction gate: missing {path} — skip BUY")
            return False
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.log.warning(f"Prediction gate: bad JSON ({exc!s}) — skip BUY")
            return False
        if not isinstance(raw, dict):
            return False

        gen = raw.get("generated_utc")
        max_age = int(self.config.prediction_max_age_minutes)
        if max_age > 0 and isinstance(gen, str):
            try:
                ts = datetime.fromisoformat(gen.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_m = (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
                if age_m > float(max_age):
                    self.log.warning(
                        f"Prediction gate: stale output ({age_m:.0f} min > {max_age}) — skip BUY"
                    )
                    return False
            except (TypeError, ValueError):
                self.log.warning("Prediction gate: unparseable generated_utc — skip BUY")
                return False

        preds = raw.get("predictions")
        if not isinstance(preds, dict):
            self.log.warning("Prediction gate: no predictions object — skip BUY")
            return False

        return self._predictions_dict_allows_buy(preds)

    def _live_prediction_allows_buy(self) -> bool:
        raw = self._live_out
        if not isinstance(raw, dict):
            self.log.warning("Live predict: no snapshot yet — skip BUY")
            return False
        preds = raw.get("predictions")
        if not isinstance(preds, dict):
            self.log.warning("Live predict: bad predictions — skip BUY")
            return False
        return self._predictions_dict_allows_buy(preds, prefix="Live predict")

    def _logreg_up_allows_buy(
        self,
        preds: dict,
        *,
        min_c: float,
        prefix: str = "Prediction gate",
    ) -> bool:
        block = preds.get("direction_logistic")
        if not isinstance(block, dict):
            self.log.warning(f"{prefix}: no direction_logistic — skip BUY")
            return False
        label = str(block.get("label", "")).upper()
        conf = float(block.get("confidence") or 0.0)
        ok = label == "UP" and conf >= min_c
        if not ok:
            self.log.info(f"{prefix}: LogReg {label} conf={conf:.1f} (min {min_c:.1f}) — skip BUY")
        return ok

    def _predictions_dict_allows_buy(
        self,
        preds: dict,
        *,
        prefix: str = "Prediction gate",
    ) -> bool:
        sig = (self.config.prediction_signal or "consensus_nautilus_enhanced").strip().lower()
        if sig == "direction_logistic":
            min_c = float(self.config.prediction_min_logreg_confidence)
            return self._logreg_up_allows_buy(preds, min_c=min_c, prefix=prefix)

        key = "consensus_nautilus_enhanced" if sig == "consensus_nautilus_enhanced" else "consensus"
        val = preds.get(key)
        if val is None and key == "consensus_nautilus_enhanced":
            val = preds.get("consensus")
        if not isinstance(val, str):
            self.log.warning(f"{prefix}: missing {key!r} — skip BUY")
            return False
        v = val.upper().strip()
        if v in ("BULLISH", "STRONG_BULLISH"):
            self.log.info(f"{prefix}: {key}={v} — allow BUY")
            return True
        if v == "MIXED":
            # Align with ``btc_analysis_order_signal.decide_forceenter_intent``: MIXED + confident UP.
            floor = float(self.config.prediction_min_logreg_confidence)
            if floor <= 0.0:
                floor = 65.0
            if self._logreg_up_allows_buy(preds, min_c=floor, prefix=f"{prefix} (MIXED→LogReg)"):
                self.log.info(f"{prefix}: {key}=MIXED, LogReg UP ≥ {floor:.1f} — allow BUY")
                return True
            return False
        self.log.info(f"{prefix}: {key}={v} — skip BUY")
        return False

    def _adaptive_entry_qty(self, *, stake_mult: float = 1.0) -> Quantity | None:
        assert self._instrument is not None and self._last_mid is not None
        frac = float(self.config.adaptive_stake_fraction) * max(0.0, float(stake_mult))
        if frac <= 0.0:
            self.log.error("adaptive_stake_fraction must be > 0")
            return None
        acc = self.portfolio.account(BYBIT)
        if acc is None:
            self.log.warning("No portfolio account for BYBIT; skip adaptive qty")
            return None
        quote = self._instrument.quote_currency
        free_m = acc.balance_free(quote)
        if free_m is None:
            self.log.warning(f"No free {quote} balance yet; skip adaptive qty")
            return None
        free_f = float(free_m)
        if free_f <= 0.0:
            self.log.warning(f"Free {quote} is zero; skip adaptive qty")
            return None
        notional = free_f * min(frac, 1.0)
        cap_n = self.config.adaptive_max_notional_usdt
        if cap_n is not None and float(cap_n) > 0.0:
            notional = min(notional, float(cap_n))
        mid = float(self._last_mid)
        if mid <= 0.0:
            return None
        raw_qty = notional / mid
        min_q = 0.0
        if self._instrument.min_quantity is not None:
            min_q = float(self._instrument.min_quantity)
        max_q = raw_qty
        if self._instrument.max_quantity is not None:
            max_q = min(max_q, float(self._instrument.max_quantity))
        cfg_min = (self.config.adaptive_min_qty_str or "").strip()
        if cfg_min:
            try:
                min_q = max(min_q, float(Quantity.from_str(cfg_min)))
            except ValueError:
                self.log.error(f"Invalid adaptive_min_qty_str {cfg_min!r}")
                return None
        cfg_max = (self.config.adaptive_max_qty_str or "").strip()
        if cfg_max:
            try:
                max_q = min(max_q, float(Quantity.from_str(cfg_max)))
            except ValueError:
                self.log.error(f"Invalid adaptive_max_qty_str {cfg_max!r}")
                return None
        raw_qty = max(min_q, min(raw_qty, max_q))
        step = float(self._instrument.size_increment)
        if step > 0.0:
            raw_qty = math.floor(raw_qty / step) * step
        if raw_qty < min_q - 1e-12:
            self.log.warning(
                f"Adaptive qty below min after rounding (raw={raw_qty} min={min_q} step={step})"
            )
            return None
        if self._instrument.min_notional is not None:
            if raw_qty * mid < float(self._instrument.min_notional):
                self.log.warning(
                    f"Order notional below instrument min_notional ({self._instrument.min_notional}); skip"
                )
                return None
        try:
            return Quantity(raw_qty, self._instrument.size_precision)
        except ValueError as e:
            self.log.error(f"Adaptive Quantity build failed: {e!s}")
            return None
