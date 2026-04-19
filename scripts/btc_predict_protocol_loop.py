#!/usr/bin/env python3
"""
**Circular predict protocol:** repeat **live 5m fit → target side → reconcile venue** on a timer.

Delegates **entries and exits** to the same signal as ``btc_predict_asap_order`` (``decide_side`` from
``btc_asap_predict_core``):

- **Flat** + target **long/short** → **market** open by default (set leverage, then Buy/Sell). Optional
  ``SYGNIF_PREDICT_ENTRY_EXECUTION=limit_postonly`` places a **PostOnly** limit below/above mark (resting order until
  fill); ``SYGNIF_PREDICT_ENTRY_LIMIT_FILL_WAIT_SEC`` polls for a position before TP/SL; duplicate entries skip while
  working non-reduce-only orders exist. ``SYGNIF_PREDICT_ENTRY_LIMIT_FALLBACK_MARKET=1`` (default) falls back to market
  when the limit price is invalid or the venue rejects the limit.
- **Size:** ``--manual-qty`` (BTC) **or** ``--manual-notional-usdt`` (USDT notional ≈ qty×last close from the fit).
- **Execute defaults (high leverage, small-move P/L):** with ``--execute``, if you pass **neither** ``--manual-qty`` nor
  ``--manual-notional-usdt``, the loop applies ``SYGNIF_PREDICT_DEFAULT_NOTIONAL_USDT`` (**100000**) and
  ``SYGNIF_PREDICT_DEFAULT_MANUAL_LEVERAGE`` (**50**) so each entry targets ~**100k USDT notional at 50×** unless you
  override. Disable auto-fill: ``SYGNIF_PREDICT_EXECUTE_AUTO_SIZING_OFF=1`` (then sizing falls back to free-balance logic).
  Large notionals need a high enough ``BYBIT_DEMO_ORDER_MAX_QTY`` (``swarm_auto`` defaults **2.0** BTC cap for ~100k
  USDT at ~50k/BTC spot). ``SYGNIF_LETSCRASH_NOTIONAL_CAP`` clips to ``letscrash/.../notional_cap_usdt`` (**100000**).
- **High leverage:** ``--manual-leverage N`` uses cap ``BYBIT_DEMO_MANUAL_LEVERAGE_MAX`` (default **100**, max 125), not the auto band max (``BYBIT_DEMO_ORDER_MAX_LEVERAGE``).
- **Long** + target **not long** (short or no-edge) → **Sell** ``reduceOnly`` full size.
- **Short** + target **not short** (long or no-edge) → **Buy** ``reduceOnly`` full size.
- **Flip** (e.g. long → short): **close** then **open** opposite in the same iteration (brief pause after
  close for venue consistency).

Uses **Bybit API demo** REST — ``trade_overseer/bybit_linear_hedge.py`` → ``https://api-demo.bybit.com`` with
``BYBIT_DEMO_*`` (mainnet-mirrored USDT linear), **not** ``api.bybit.com``, unless you explicitly set
``OVERSEER_BYBIT_HEDGE_MAINNET=YES`` and ``OVERSEER_HEDGE_LIVE_OK=YES`` (not used by Swarm launchers). Same family as
the ASAP script, optimised for **closed-loop** automation.

**Run**
- Dry-run (predict + planned actions only): ``python3 scripts/btc_predict_protocol_loop.py``
- Live: ``SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES`` and ``--execute``
- Runs **until SIGINT/SIGTERM** when ``PREDICT_LOOP_MAX_ITERATIONS=0`` (default): one continuous circular
  protocol with no mandatory pause between cycles unless you set a positive interval.

**Env**
- ``SYGNIF_SWARM_GATE_LOOP`` — when ``1``/``true`` with ``--execute``, run ``compute_swarm()`` +
  ``write_fused_sidecar`` + ``swarm_fusion_allows`` each iteration before **new** entries; flips/exits still
  follow ``decide_side``. Set ``SYGNIF_SWARM_BTC_FUTURE=1`` (demo **bf**) or ``trade`` (mainnet **bf**); with **ac** on and
  matching symbols, Swarm uses one fused **bf** vote. Default ``1`` when gate is on. See
  ``scripts/swarm_auto_predict_protocol_loop.py``. **Nautilus research / ML sidecar** gates (freshness,
  non-contradiction, strong fusion, logreg floor) live in ``finance_agent/swarm_order_gate.py`` — the
  swarm-auto launcher sets ``SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY`` and ``SYGNIF_PROTOCOL_FUSION_SYNC`` by default.
- **SygnifStrategy-style guidelines (optional):** ``SWARM_ORDER_SYGNIF_STRATEGY_GUIDELINES=1`` requires each live
  ``fit_predict`` payload's ``strategy_guidelines`` (``orb_long`` / ``sygnif_swing`` analogs) before a **new** long/short
  entry passes ``swarm_fusion_allows``. With ``SWARM_ORDER_GUIDELINE_HIVEMIND_FUSION=1``, live Hivemind explore plus an
  aligned ``consensus_nautilus_enhanced`` label can satisfy the gate when strict ORB/swing fails. See
  ``prediction_agent/btc_strategy_guidelines.py`` and ``swarm_order_gate.py``.
- ``SYGNIF_PREDICT_LOGREG_BYPASS_MIN_CONF`` — optional (default **0** = off). When **> 0** with execute + swarm gate,
  if Swarm would **block** a new entry but ``predictions.direction_logistic`` **confidence** (percent) is at least this
  value **and** the LogReg **label** matches the model **target** (``UP``→long, ``DOWN``→short), the entry block is
  cleared and ``SYGNIF_LOOP_LOGREG_BYPASS`` is logged. Research / override only — use with care.
- **USD broad index vs BTC** — embedded in ``write_fused_sidecar`` as ``usd_btc_macro`` (FRED ``DTWEXBGS`` vs
  ``btc_daily_90d.json``). Refresh snapshot: ``pull_btc_context.py`` with ``FRED_API_KEY``; live FRED in-loop:
  ``SYGNIF_PREDICT_USD_BTC_CORR_LIVE=1``, TTL ``SYGNIF_PREDICT_USD_BTC_CORR_TTL_SEC`` (default **3600**), optional
  ``SYGNIF_PREDICT_USD_BTC_CORR_WRITE_SNAPSHOT=1``. Disable block: ``SYGNIF_PREDICT_USD_BTC_MACRO_OFF=1``. Optional
  Swarm **entry** gate: ``SWARM_ORDER_USD_BTC_MACRO_GATE=1`` (see ``finance_agent/swarm_order_gate.py``).
- ``SYGNIF_PROTOCOL_FUSION_SYNC`` — when ``1``/``true``, call ``write_fused_sidecar`` after each successful
  ``run_live_fit`` **without** requiring ``--execute`` or ``SYGNIF_SWARM_GATE_LOOP``. Keeps
  ``prediction_agent/swarm_nautilus_protocol_sidecar.json`` aligned with **Nautilus research** JSON
  (``nautilus_strategy_signal.json``) + ``btc_prediction_output.json`` for dashboards/briefing. Skipped when
  the swarm gate path already wrote fusion this iteration.
- ``SYGNIF_PROTOCOL_FUSION_TICK`` — when ``1``/``true``, embed the loop predict line into that same JSON each
  iteration (see ``prediction_agent/nautilus_protocol_fusion.record_protocol_tick``).
- ``SWARM_PORTFOLIO_AUTHORITY`` — when ``1``/``true`` with gate + ``--execute``, **do not** reduce-only
  **close-for-flip** (opposite model target) if Swarm already **blocked** that target as a new entry
  (hold the leg until Swarm allows the new side). When ``0`` (``swarm_auto`` default), allow **Bybit reduce-only**
  close on opposite signal so the account can go **flat** and wait for an eligible open; reopen only when
  ``swarm_fusion_allows`` passes. ``scripts/swarm_authority_protocol_loop.sh`` keeps authority-style defaults
  (``SWARM_PORTFOLIO_AUTHORITY`` default **1** in that wrapper only).
- **Hivemind gate (optional):** ``SWARM_ORDER_REQUIRE_HIVEMIND_VOTE=1`` requires **hm** vote to align with long/short;
  ``SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS=1`` allows **hm** ``0``. Swarm-auto defaults both **on**; override to ``0`` to disable.
- **btc_future TP/SL (demo):** on ``--execute``, ``SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL`` defaults **on**; this loop sets
  ``SYGNIF_SWARM_TPSL_PROFILE=reward_risk`` (moderate %% TP/SL + trail in ``swarm_btc_future_tpsl_apply``) unless
  already set. After a successful **open**, ``apply_btc_future_tpsl`` POSTs ``takeProfit`` / ``stopLoss`` / optional
  trailing from ``btc_prediction_output.json``. If that succeeds, the USDT-distance fallback is skipped.
  ``SYGNIF_SWARM_TPSL_POST_OPEN_SLEEP_SEC`` (default **1**), ``SYGNIF_SWARM_TPSL_POST_OPEN_RETRIES`` (default **8**).
- **USDT TP/SL fallback** (when Swarm TP/SL did not attach): ``SYGNIF_SWARM_TP_USDT_TARGET`` / ``SYGNIF_SWARM_SL_USDT_TARGET``
  default here to **600** / **360** — USDT **PnL** targets so price distance ≈ ``target / qty`` (linear). If SL env is
  **≤ 0**, fallback uses the TP value. Override in ``.env`` for tighter or wider risk.
- ``SYGNIF_SWARM_AUTO_TRADING`` — marker env set by the auto launcher (for operators / dashboards only).
- ``SYGNIF_LETSCRASH_NOTIONAL_CAP`` (default **on** in ``swarm_auto``) — clip ``--manual-notional-usdt`` to
  ``letscrash/btc_strategy_0_1_rule_registry.json`` → ``rule_proof_bucket.notional_cap_usdt``. Set ``0`` to disable.
- ``PREDICT_LOOP_REFRESH_ALIGNED_EVERY_N`` — see above; ``0`` disables aligned refresh.
- ``SYGNIF_PREDICT_PROTOCOL_TEST_TAG_ORDER`` — ``1``/``true``: same as CLI ``--test-tag-order`` (with ``--execute`` + ACK).
- ``PREDICT_LOOP_INTERVAL_SEC`` (default **0**) — extra sleep after each iteration before the next predict.
  **0** = seamless back-to-back loop (no artificial gap; only predict + venue time). Set e.g. **300** to
  pace on 5m bars or reduce API/CPU load.
- ``PREDICT_LOOP_POST_CLOSE_SLEEP_SEC`` (default **0.75**) — pause after a reduce-only close before open.
- ``PREDICT_LOOP_MAX_ITERATIONS`` — **0** = run until SIGINT/SIGTERM.
- **Dataset JSONL (Swarm / analysis / finetune prep):** one NDJSON line per iteration with ML + Swarm + fusion
  + venue snapshot. **On by default** when ``SYGNIF_SWARM_GATE_LOOP=1`` and ``--execute`` (Swarm-auto path).
  ``SYGNIF_PREDICT_PROTOCOL_DATASET_JSONL=/path/file.jsonl`` overrides output path (``~`` expanded).
  Disable: ``SYGNIF_PREDICT_PROTOCOL_DATASET=0``. Default path when enabled:
  ``prediction_agent/swarm_predict_protocol_dataset.jsonl`` (append-only; add to backups / ignore in git).
- ``PREDICT_LOOP_ERROR_SLEEP_SEC`` (default **2**) — when ``interval`` is **0**, sleep this many seconds
  only after a thrown exception (avoids a tight spin on persistent failures). Set **0** to retry immediately.
- **Swarm demo runtime hints (weak-points → env):** with ``SYGNIF_SWARM_RUNTIME_HINTS_APPLY=1``, hints are normally
  read **once** when ``swarm_auto_predict_protocol_loop`` spawns this process. For **multi-day** tuning while the loop
  stays up, set ``SYGNIF_SWARM_RUNTIME_HINTS_RELOAD_EACH_ITER=1`` to re-apply ``prediction_agent/swarm_demo_runtime_hints.json``
  each iteration (optional ``SYGNIF_SWARM_RUNTIME_HINTS_RELOAD_EVERY_N``, default **1**). Iteration pacing then prefers
  ``PREDICT_LOOP_INTERVAL_SEC``, then ``SYGNIF_SWARM_LOOP_INTERVAL_SEC``, then ``--interval-sec``. Hint file TTL when
  **building** JSON: ``SYGNIF_SWARM_RUNTIME_HINTS_TTL_HOURS`` (default **2**, max **168**; see ``swarm_improvement_runtime``).
- **NeuroLinked Swarm hook (optional):** ``SYGNIF_NEUROLINKED_SWARM_HOOK=1`` pushes Swarm + predict-line meta to
  ``prediction_agent/neurolinked_swarm_channel.json`` and, when ``SYGNIF_NEUROLINKED_HTTP_URL`` is set (default
  ``http://127.0.0.1:8889``), ``POST /api/input/text`` on a running NeuroLinked ``run.py`` server. Tuning:
  ``SYGNIF_NEUROLINKED_SWARM_HOOK_EVERY_N`` (default **1**), ``SYGNIF_NEUROLINKED_HTTP_TIMEOUT_SEC`` (default **3**).
  See ``finance_agent/neurolinked_predict_loop_hook.py``.
- **Resource guard (letscrash):** ``letscrash/btc_strategy_0_1_rule_registry.json`` → ``tuning.predict_loop_resource``
  (``enabled``, ``mem_available_min_mb``, ``loadavg_max``, ``cooldown_sec``). When enabled, skips ``run_live_fit``
  under low **MemAvailable** or high **loadavg** and logs ``SYGNIF_LOOP_RESOURCE_HOLD``. Env overrides:
  ``SYGNIF_PREDICT_RESOURCE_GUARD``, ``SYGNIF_RESOURCE_MEM_MIN_MB``, ``SYGNIF_RESOURCE_LOAD_MAX``,
  ``SYGNIF_RESOURCE_COOLDOWN_SEC``.
- ``PREDICT_LOOP_HOLD_ON_NO_EDGE`` (default **1**) — when truthy, **do not** flatten on a **no-edge**
  signal (``decide_side`` → ``None``); only **exit on an opposite** long/short target (reduces chop).
  Set to ``0`` or use ``--exit-on-no-edge`` to restore flatten-every-cycle on no-edge.
- ``PREDICT_LOOP_REFRESH_ALIGNED_EVERY_N`` — every **N** iterations, if the venue position **already matches**
  the model target, **close + re-open** the same side (demo “rip & replace”). **0** = off (recommended for
  multi-day holds). Default from env is **1** when unset; ``swarm_auto`` injects a calmer default.
- ``SYGNIF_PREDICT_MIN_DISCRETIONARY_CLOSE_SEC`` — after a **successful open in this process**, block
  **opposite-signal**, **aligned refresh**, and **no-edge flatten** closes until this many seconds have
  elapsed (**0** = off). Ignored when the open time is unknown (e.g. position opened before this process).
- ``SYGNIF_PREDICT_OPPOSITE_SIGNAL_CONFIRM_ITER`` — require this many **consecutive** iterations where the
  raw model target is opposite the venue leg before a flip close (**0** = off; **1** = legacy immediate flip).
- **Hivemind + live fit:** when Truthcoin / Swarm hive flags are on (or ``SYGNIF_PREDICT_HIVEMIND_FUSION=1``),
  ``fit_predict_live`` attaches ``predictions.hivemind`` and can promote **BULLISH → STRONG_BULLISH** if
  Hivemind liveness vote is ``+1`` (see ``btc_predict_live`` / ``apply_hivemind_to_enhanced_consensus``).
  Set ``SYGNIF_PREDICT_HIVEMIND_FUSION=0`` to disable that pass while keeping Swarm hive for orders.
- **Modeled-profit gate (flat opens):** skip the **open** when consensus edge × planned qty is below a USDT floor
  (loop keeps running; exits/flips unchanged). Unset ``SYGNIF_PREDICT_MIN_EDGE_PROFIT_USDT`` → use
  ``SYGNIF_SWARM_TP_USDT_TARGET`` when > 0 (e.g. **50** with TP set), else gate **off**. ``swarm_auto`` sets a modest
  floor (override with ``0`` to disable). Edge ≈ ``qty * max(0, consensus_next_mean - close)`` (long) or symmetric
  short. With ``SYGNIF_PREDICT_EDGE_PLUS_FEE`` (default **on** in ``swarm_auto``), the floor adds
  ``SYGNIF_PREDICT_PER_TRADE_COST_USDT`` (default **1**). **Vol relax:** ``effective_open_edge_floor_usdt(move_pct)``
  scales the floor down when RF/XGB mean move %% is high (``SYGNIF_PREDICT_EDGE_VOL_RELAX``, ``SYGNIF_PREDICT_EDGE_VOL_REF_LO_PCT``,
  ``SYGNIF_PREDICT_EDGE_VOL_REF_HI_PCT``, ``SYGNIF_PREDICT_EDGE_VOL_RELAX_MAX``, ``SYGNIF_PREDICT_EDGE_VOL_RELAX_MIN_FACTOR``).
- **Hedge mode:** ``SYGNIF_PREDICT_ENSURE_HEDGE_MODE=1`` (``swarm_auto`` default) calls ``switch_position_mode`` to **hedge**
  once per process (may fail if Bybit refuses while positions exist — see log ``SYGNIF_LOOP_HEDGE_MODE``).
- **Hold until green:** ``SYGNIF_PREDICT_HOLD_UNTIL_PROFIT`` — skip **opposite-signal** closes and **aligned refresh**
  closes while unrealised P/L is below ``SYGNIF_PREDICT_MIN_UPNL_TO_CLOSE_USDT`` (unset → ``per_trade_fee``).
- **Trailing:** ``SYGNIF_PREDICT_TRAIL_MOVE_USDT`` — after uPnL ≥ min, one POST of ``trailingStop`` with price step ≈ USDT/qty
  (Bybit semantics; tune carefully).
- **Swing failure:** live fit JSON gains ``predictions.swing_failure``; ``SYGNIF_PREDICT_SWING_FAILURE_ENTRIES`` lets
  ``decide_side`` emit long/short on **sf_long** / **sf_short** when the vote stack is otherwise flat (after logreg tiebreak).
- **Heavy91 failure swing + panic reverse:** ``predictions.failure_swing_heavy91`` (Pine-style false S/R break) is filled
  each ``run_live_fit``. ``SYGNIF_PREDICT_FAILURE_SWING_HEAVY91_ENTRIES`` lets ``decide_side`` use those entries when
  the ML stack is flat. ``SYGNIF_PREDICT_FAILURE_SWING_PANIC_REVERSE`` (execute path): if ML target is no-edge (would
  flatten when ``hold_on_no_edge`` is off) but Heavy91 signals a counter-trade, **flip** instead of panic-selling to flat.
- **Bybit-only “Freqtrade-style” mechanics** (no Freqtrade runtime): ``finance_agent/swarm_bybit_ft_mechanics``
  — optional **entry cooldown** and **consecutive open-fail** cap with JSON state under ``prediction_agent/``.
  See ``SWARM_BYBIT_FT_MECHANICS``, ``SWARM_BYBIT_ENTRY_COOLDOWN_SEC``, ``SWARM_BYBIT_MAX_CONSEC_OPEN_FAILS``,
  ``SWARM_BYBIT_FT_STATE_JSON``.
- **Next-bar forecast eval (hold-out):** each ``run_live_fit`` can append a **pending** NDJSON row (default **on** via
  ``SYGNIF_PREDICT_EVAL_LOG``). After the **next** 5m bar has printed on Bybit, run
  ``python3 scripts/evaluate_btc_forecast_outcomes.py`` (e.g. cron every few minutes) to resolve pending rows into
  ``btc_eval_outcomes.jsonl`` and print rolling accuracy. Paths and slack: ``SYGNIF_PREDICT_EVAL_FORECAST_JSONL``,
  ``SYGNIF_PREDICT_EVAL_OUTCOMES_JSONL``, ``SYGNIF_PREDICT_EVAL_SLACK_SEC`` — see ``prediction_agent/btc_forecast_eval.py``.
- **Synthetic / 24h guard (no new entries on flat synthesis):** ``SYGNIF_PREDICT_BLOCK_SYNTHETIC_HOLD=1`` blocks
  **opens** when ``btc_24h_movement_prediction.json`` has ``synthesis.bias_24h=NEUTRAL`` and/or
  ``swarm_btc_synth.json`` is ``order_signal=HOLD`` + ``side=FLAT``. Missing files do **not** block (fail-open).
  Closes / flips still follow ``decide_side`` + Swarm portfolio rules; this gate only clears ``open_target``.
- **Live confidence vs closed P/L:** run ``python3 scripts/sygnif_update_predict_calibration_from_ft.py`` (writes
  ``prediction_agent/live_trading_calibration.json``). ``run_live_fit`` then scales ``direction_logistic.confidence``
  before sidecars / JSON (see ``prediction_agent/live_trading_calibration.py``).
- **Time-split audit (leakage review):** ``SYGNIF_PREDICT_AUDIT_TIME_SPLIT=1`` adds ``time_split_audit`` to
  ``btc_prediction_output.json`` from ``fit_predict_live`` (chronological split metadata + note).
- **Min wallet equity (optional):** ``SYGNIF_PREDICT_MIN_WALLET_EQUITY_USDT`` — skip **new** opens when Bybit UNIFIED
  USDT ``equity`` is below this threshold (parsed from the same ``wallet-balance`` call as available balance).

**One prediction fit:** wall time is logged as ``predict_ms`` on each ``SYGNIF_LOOP_PREDICT`` line — typically
**~2–8 s** with default ASAP kline/trees (network + sklearn/xgb on the last row); raise trees/kline for
accuracy at the cost of latency.

Requires **Nautilus venv** (sklearn/xgboost) and ``PYTHONPATH`` to ``prediction_agent``; ``BYBIT_DEMO_*``
for live mode.

**Tag verification (dashboard PREDICT):** one-shot minimal **Buy** open + reduce-only close (account must be
**flat** first): ``SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES`` ``python3 scripts/btc_predict_protocol_loop.py
--execute --test-tag-order`` or set env ``SYGNIF_PREDICT_PROTOCOL_TEST_TAG_ORDER=1`` with ``--execute``.
Optional: ``SYGNIF_TEST_TAG_ORDER_QTY`` (default ``0.001``), ``SYGNIF_TEST_TAG_ORDER_SLEEP_SEC`` (default ``1``),
``SYGNIF_TEST_TAG_ORDER_OPEN_WAIT_SEC`` (default **8**) — poll ``position/list`` after open until size appears.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PA = _REPO / "prediction_agent"
_DATA = _REPO / "finance_agent" / "btc_specialist" / "data"
sys.path.insert(0, str(_PA))
sys.path.insert(0, str(_REPO / "finance_agent"))
sys.path.insert(0, str(_REPO / "trade_overseer"))

import bybit_linear_hedge as blh  # noqa: E402
import btc_iface_trade_tags as iface_tags  # noqa: E402
from btc_asap_predict_core import decide_side  # noqa: E402
from btc_asap_predict_core import env_float  # noqa: E402
from btc_asap_predict_core import env_int  # noqa: E402
from btc_asap_predict_core import leverage_from_move_pct  # noqa: E402
from btc_asap_predict_core import logreg_aligns_target  # noqa: E402
from btc_asap_predict_core import logreg_confidence  # noqa: E402
from btc_asap_predict_core import linear_leg_unrealised_usdt  # noqa: E402
from btc_asap_predict_core import modeled_edge_usdt_per_btc  # noqa: E402
from btc_asap_predict_core import modeled_profit_usdt_at_qty  # noqa: E402
from btc_asap_predict_core import open_modeled_edge_floor_usdt  # noqa: E402
from btc_asap_predict_core import effective_open_edge_floor_usdt  # noqa: E402
from btc_asap_predict_core import per_trade_fee_usdt  # noqa: E402
from btc_asap_predict_core import relative_modeled_edge_pct  # noqa: E402
from btc_asap_predict_core import move_pct_and_close  # noqa: E402
from btc_asap_predict_core import parse_linear_position  # noqa: E402
from btc_asap_predict_core import parse_usdt_available  # noqa: E402
from btc_asap_predict_core import parse_usdt_equity  # noqa: E402
from btc_asap_predict_core import qty_btc  # noqa: E402
from btc_asap_predict_core import run_live_fit  # noqa: E402

try:
    from swarm_bybit_ft_mechanics import entry_allowed as _ft_entry_allowed
    from swarm_bybit_ft_mechanics import record_open_fail as _ft_record_open_fail
    from swarm_bybit_ft_mechanics import record_open_success as _ft_record_open_success
except ImportError:  # pragma: no cover

    def _ft_entry_allowed(_repo: Path, _sym: str, *, iter_count: int) -> tuple[bool, str]:
        return True, ""

    def _ft_record_open_fail(_repo: Path, _sym: str) -> None:
        pass

    def _ft_record_open_success(_repo: Path, _sym: str, *, iter_count: int) -> None:
        pass

try:
    from letscrash_predict_loop_guard import (  # noqa: E402
        load_guard_config,
        resource_snapshot,
        should_skip_iteration,
    )
except ImportError:
    from finance_agent.letscrash_predict_loop_guard import (  # noqa: E402
        load_guard_config,
        resource_snapshot,
        should_skip_iteration,
    )

from swarm_btc_future_tpsl_apply import (  # noqa: E402
    apply_btc_future_tpsl,
    finalize_linear_stop_loss,
    finalize_linear_take_profit,
)

_STOP = False
_HEDGE_SWITCH_TRIED = False
_TRAIL_PLANTED: set[str] = set()
# Wall-clock (monotonic) when this process last opened a position per symbol — for min-hold exits.
_POSITION_OPEN_MONOTONIC: dict[str, float] = {}
# Consecutive iterations with model target opposite venue leg (per symbol).
_OPPOSITE_STREAK: dict[str, int] = {}


def _letscrash_notional_cap_enabled() -> bool:
    raw = (os.environ.get("SYGNIF_LETSCRASH_NOTIONAL_CAP") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _letscrash_notional_cap_usdt() -> float | None:
    try:
        p = _REPO / "letscrash" / "btc_strategy_0_1_rule_registry.json"
        raw = json.loads(p.read_text(encoding="utf-8"))
        cap = (raw.get("rule_proof_bucket") or {}).get("notional_cap_usdt")
        if cap is None:
            return None
        return float(cap)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _clip_manual_notional_letscrash(mn: float) -> float:
    if not _letscrash_notional_cap_enabled():
        return float(mn)
    cap = _letscrash_notional_cap_usdt()
    if cap is not None and cap > 0:
        return min(float(mn), cap)
    return float(mn)


def _apply_swarm_tpsl_after_open(sym_u: str) -> bool:
    """
    After a successful market open, POST demo linear TP/SL (and optional trail) via
    ``apply_btc_future_tpsl`` when ``SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL`` is on.

    Retries while ``apply_btc_future_tpsl`` reports a flat book (position not visible yet).

    Returns True when the trading-stop call succeeded so callers can skip a second TP-only line.
    """
    os.environ.setdefault("SYGNIF_SWARM_TPSL_SYMBOL", sym_u)
    sleep_sec = max(0.0, env_float("SYGNIF_SWARM_TPSL_POST_OPEN_SLEEP_SEC", 1.0))
    retries = env_int("SYGNIF_SWARM_TPSL_POST_OPEN_RETRIES", 8, lo=1, hi=99)
    last: dict = {}
    for attempt in range(retries):
        if sleep_sec > 0:
            time.sleep(sleep_sec)
        try:
            last = apply_btc_future_tpsl(dry_run=False)
        except Exception as exc:  # noqa: BLE001
            print(
                f"SYGNIF_LOOP_WARN tpsl_apply attempt={attempt + 1}/{retries}: {exc}",
                flush=True,
            )
            continue
        if last.get("ok"):
            print(
                f"SYGNIF_LOOP_TPSL ok attempt={attempt + 1} {json.dumps(last, default=str)}",
                flush=True,
            )
            return True
        sk = last.get("skipped")
        if sk != "flat":
            print(f"SYGNIF_LOOP_TPSL skip={sk!r} {json.dumps(last, default=str)}", flush=True)
            break
        print(
            f"SYGNIF_LOOP_TPSL venue flat attempt={attempt + 1}/{retries} — retry",
            flush=True,
        )
    if last:
        print(f"SYGNIF_LOOP_TPSL give_up {json.dumps(last, default=str)}", flush=True)
    return False


def _on_signal(_sig, _frame) -> None:
    global _STOP
    _STOP = True


def _hold_on_no_edge_from_env() -> bool:
    v = os.environ.get("PREDICT_LOOP_HOLD_ON_NO_EDGE", "1").strip().lower()
    return v not in ("0", "false", "no", "off", "")


def _default_refresh_aligned_every() -> int:
    raw = (os.environ.get("PREDICT_LOOP_REFRESH_ALIGNED_EVERY_N", "1") or "1").strip().lower()
    if raw in ("0", "off", "false", "no", ""):
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 1


def need_close_position(
    pos_side: str | None,
    target: str | None,
    *,
    hold_on_no_edge: bool,
) -> bool:
    """
    Whether to reduce-only close an open position before (optionally) opening the target side.

    - Opposite signal always closes (long+short or short+long).
    - If ``hold_on_no_edge``: **no-edge** (``target is None``) does **not** close — hold through chop.
    - If not ``hold_on_no_edge``: no-edge closes (legacy aggressive flatten).
    """
    if pos_side is None:
        return False
    if target is None:
        return not hold_on_no_edge
    if pos_side == "long" and target == "short":
        return True
    if pos_side == "short" and target == "long":
        return True
    return False


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        o = json.loads(path.read_text(encoding="utf-8"))
        return o if isinstance(o, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _close_qty_str(raw_api: str, abs_float: float) -> str:
    s = (raw_api or "").strip()
    if s:
        return s
    step = 0.001
    q = math.floor(abs_float / step) * step
    return f"{q:.6f}".rstrip("0").rstrip(".") or str(step)


def _env_flag(name: str) -> bool:
    """Truthy env: 1/true/yes/on (used for SYGNIF_* swarm gate, fusion sync, SWARM_* toggles)."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _min_upnl_to_close_usdt() -> float:
    """Minimum unrealised USDT P/L before we allow an **opposite-signal** close (hold-until-green)."""
    raw = (os.environ.get("SYGNIF_PREDICT_MIN_UPNL_TO_CLOSE_USDT") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            pass
    return max(1e-9, per_trade_fee_usdt())


def _raw_opposite_signal(pos_side: str | None, target: str | None) -> bool:
    return (
        pos_side in ("long", "short")
        and target in ("long", "short")
        and pos_side != target
    )


def _min_discretionary_close_sec() -> float:
    raw = (os.environ.get("SYGNIF_PREDICT_MIN_DISCRETIONARY_CLOSE_SEC") or "").strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _opposite_confirm_iters() -> int:
    """Iterations with a sustained opposite target before flip-close (0 = off)."""
    return env_int("SYGNIF_PREDICT_OPPOSITE_SIGNAL_CONFIRM_ITER", 1, lo=0, hi=500)


def _min_hold_blocks_close(sym_u: str, *, exit_kind: str) -> bool:
    sec = _min_discretionary_close_sec()
    if sec <= 1e-9:
        return False
    if exit_kind not in ("opposite_signal", "aligned_refresh", "no_edge_flat"):
        return False
    t0 = _POSITION_OPEN_MONOTONIC.get(sym_u)
    if t0 is None:
        return False
    return (time.monotonic() - t0) < sec


def _maybe_switch_hedge_mode(symbol: str) -> None:
    """POST ``switch-mode`` to hedge once per process when ``SYGNIF_PREDICT_ENSURE_HEDGE_MODE`` is on."""
    global _HEDGE_SWITCH_TRIED
    if _HEDGE_SWITCH_TRIED or not _env_flag("SYGNIF_PREDICT_ENSURE_HEDGE_MODE"):
        return
    _HEDGE_SWITCH_TRIED = True
    try:
        m = int(getattr(blh, "MODE_HEDGE", 3))
        r = blh.switch_position_mode(symbol, m)
        print(f"SYGNIF_LOOP_HEDGE_MODE {json.dumps(r, default=str)}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"SYGNIF_LOOP_WARN hedge_mode: {exc}", flush=True)


_DATASET_SCHEMA = "sygnif.swarm_predict_protocol_dataset/v1"


def _protocol_dataset_path(repo: Path, execute: bool) -> Path | None:
    """NDJSON sink for structured loop rows; None = disabled."""
    raw_off = os.environ.get("SYGNIF_PREDICT_PROTOCOL_DATASET", "").strip().lower()
    if raw_off in ("0", "false", "no", "off"):
        return None
    raw_path = (os.environ.get("SYGNIF_PREDICT_PROTOCOL_DATASET_JSONL") or "").strip()
    if raw_path:
        return Path(raw_path).expanduser()
    if _env_flag("SYGNIF_PREDICT_PROTOCOL_DATASET"):
        return repo / "prediction_agent" / "swarm_predict_protocol_dataset.jsonl"
    if execute and _env_flag("SYGNIF_SWARM_GATE_LOOP"):
        return repo / "prediction_agent" / "swarm_predict_protocol_dataset.jsonl"
    return None


def _compact_ml_for_dataset(out: dict | None) -> dict | None:
    if not out or not isinstance(out, dict):
        return None
    preds = out.get("predictions")
    pred_obj: dict = preds if isinstance(preds, dict) else {}
    slim_pred: dict[str, object] = {}
    rf = pred_obj.get("random_forest")
    if isinstance(rf, dict):
        slim_pred["random_forest"] = {"next_mean": rf.get("next_mean"), "delta": rf.get("delta")}
    xg = pred_obj.get("xgboost")
    if isinstance(xg, dict):
        slim_pred["xgboost"] = {"next_mean": xg.get("next_mean"), "delta": xg.get("delta")}
    lr = pred_obj.get("direction_logistic")
    if isinstance(lr, dict):
        slim_pred["direction_logistic"] = {"label": lr.get("label"), "confidence": lr.get("confidence")}
    for k in ("consensus", "consensus_nautilus_enhanced"):
        if pred_obj.get(k) is not None:
            slim_pred[k] = pred_obj.get(k)
    hm = pred_obj.get("hivemind")
    if isinstance(hm, dict):
        ex = hm.get("explore")
        slim_pred["hivemind"] = {
            "fusion_enabled": hm.get("fusion_enabled"),
            "vote": hm.get("vote"),
            "vote_detail": hm.get("vote_detail"),
            "explore_ok": (ex or {}).get("ok") if isinstance(ex, dict) else None,
            "explore_detail": (ex or {}).get("detail") if isinstance(ex, dict) else None,
        }
    nr = out.get("nautilus_research")
    slim_nr = None
    if isinstance(nr, dict):
        sc = nr.get("sidecar_signal")
        if isinstance(sc, dict):
            slim_nr = {
                "sidecar_signal": {
                    "generated_utc": sc.get("generated_utc"),
                    "bias": sc.get("bias"),
                    "close": sc.get("close"),
                    "rsi14": sc.get("rsi14"),
                }
            }
    return {
        "generated_utc": out.get("generated_utc"),
        "timeframe": out.get("timeframe"),
        "window_size": out.get("window_size"),
        "last_candle_utc": out.get("last_candle_utc"),
        "current_close": out.get("current_close"),
        "predictions": slim_pred or None,
        "nautilus_consensus_meta": out.get("nautilus_consensus_meta"),
        "nautilus_research": slim_nr,
        "backtest_metrics": out.get("backtest_metrics"),
        "model_options": out.get("model_options"),
    }


def _compact_swarm_for_dataset(swarm: dict | None) -> dict | None:
    if not swarm or not isinstance(swarm, dict):
        return None
    row: dict[str, object] = {
        "swarm_mean": swarm.get("swarm_mean"),
        "swarm_label": swarm.get("swarm_label"),
        "swarm_conflict": swarm.get("swarm_conflict"),
        "swarm_core_engine": swarm.get("swarm_core_engine"),
    }
    src = swarm.get("sources") if isinstance(swarm.get("sources"), dict) else {}
    bf = src.get("bf") if isinstance(src.get("bf"), dict) else {}
    hm = src.get("hm") if isinstance(src.get("hm"), dict) else {}
    if bf:
        row["bf"] = {"vote": bf.get("vote"), "detail": bf.get("detail")}
    if hm:
        row["hm"] = {"vote": hm.get("vote"), "detail": hm.get("detail")}
    btf = swarm.get("btc_future")
    if isinstance(btf, dict):
        row["btc_future"] = {
            "enabled": btf.get("enabled"),
            "ok": btf.get("ok"),
            "profile": btf.get("profile"),
            "detail": btf.get("detail"),
        }
    return {k: v for k, v in row.items() if v is not None}


def _compact_fusion_for_dataset(doc: dict | None) -> dict | None:
    if not doc or not isinstance(doc, dict):
        return None
    fus = doc.get("fusion") if isinstance(doc.get("fusion"), dict) else {}
    row: dict[str, object] = {
        "fusion_label": fus.get("label"),
        "fusion_sum": fus.get("sum"),
        "fusion_nautilus_detail": fus.get("nautilus_detail"),
        "fusion_ml_detail": fus.get("ml_detail"),
        "vote_btc_future": fus.get("vote_btc_future"),
        "btc_future_direction": fus.get("btc_future_direction"),
    }
    lt = doc.get("liquidation_tape")
    if isinstance(lt, dict) and lt.get("enabled"):
        row["liq_tape_vote"] = lt.get("tape_pressure_vote")
        row["liq_tape_label"] = lt.get("tape_label")
        nest = lt.get("notional_usdt_est")
        if isinstance(nest, dict):
            row["liq_long_usdt_est"] = nest.get("long_side")
            row["liq_short_usdt_est"] = nest.get("short_side")
        iw = lt.get("in_window")
        if isinstance(iw, dict):
            row["liq_n_long"] = iw.get("n_long_events")
            row["liq_n_short"] = iw.get("n_short_events")
    return {k: v for k, v in row.items() if v is not None}


def _append_protocol_dataset_jsonl(
    path: Path,
    *,
    repo: Path,
    args: argparse.Namespace,
    iter_count: int,
    execute: bool,
    line: dict[str, object],
    out: dict | None,
    swarm_snapshot: dict | None,
    fusion_doc_last: dict | None,
) -> None:
    venue: dict[str, object] = {"side": None, "qty": None}
    try:
        pr = blh.position_list(args.symbol)
        ps, psz, _ = parse_linear_position(pr, args.symbol, getattr(args, "position_idx", 0))
        venue["side"] = ps
        venue["qty"] = psz
    except Exception as exc:  # noqa: BLE001
        venue["query_error"] = str(exc)[:500]
    row = {
        "schema": _DATASET_SCHEMA,
        "repo": str(repo),
        "symbol": (args.symbol or "").upper(),
        "execute": execute,
        "iter": iter_count,
        "predict_protocol_line": line,
        "ml": _compact_ml_for_dataset(out),
        "swarm": _compact_swarm_for_dataset(swarm_snapshot),
        "fusion": _compact_fusion_for_dataset(fusion_doc_last),
        "venue_linear": venue,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def _avg_entry_from_position_list(pr: dict, symbol: str) -> float | None:
    """Dominant linear leg ``avgPrice`` for TP math (USDT linear)."""
    avg, _, _ = _avg_liq_mark_from_position_list(pr, symbol)
    return avg


def _avg_liq_mark_from_position_list(pr: dict, symbol: str) -> tuple[float | None, float | None, float | None]:
    """Dominant linear leg: ``(avgPrice, liqPrice, markPrice)`` for TP/SL vs liquidation."""
    sym = (symbol or "").replace("/", "").upper().strip() or "BTCUSDT"
    if pr.get("retCode") != 0:
        return None, None, None
    best_avg: float | None = None
    best_liq: float | None = None
    best_mark: float | None = None
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
                best_avg = float(str(row.get("avgPrice") or 0).strip() or 0)
            except (TypeError, ValueError):
                best_avg = None
            try:
                lq = float(str(row.get("liqPrice") or 0).strip() or 0)
            except (TypeError, ValueError):
                lq = 0.0
            best_liq = lq if lq > 0 else None
            try:
                mk = float(str(row.get("markPrice") or 0).strip() or 0)
            except (TypeError, ValueError):
                mk = 0.0
            best_mark = mk if mk > 0 else None
    if not best_avg or best_avg <= 0:
        return None, best_liq, best_mark
    return best_avg, best_liq, best_mark


def _predict_entry_execution_mode() -> str:
    raw = (os.environ.get("SYGNIF_PREDICT_ENTRY_EXECUTION") or "market").strip().lower()
    if raw in ("limit_postonly", "limit", "postonly", "maker"):
        return "limit_postonly"
    return "market"


def _entry_limit_fallback_market_enabled() -> bool:
    raw = (os.environ.get("SYGNIF_PREDICT_ENTRY_LIMIT_FALLBACK_MARKET") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _working_non_reduce_linear_orders(symbol: str, position_idx: int) -> list[dict]:
    try:
        r = blh.get_open_orders_realtime_linear(symbol)
    except Exception:
        return []
    if r.get("retCode") != 0:
        return []
    want = int(position_idx)
    active = frozenset({"New", "PartiallyFilled", "Untriggered", "Active", "Created"})
    out: list[dict] = []
    for row in (r.get("result") or {}).get("list") or []:
        if not isinstance(row, dict):
            continue
        ro = str(row.get("reduceOnly", "") or "").lower()
        if ro in ("true", "1", "yes"):
            continue
        try:
            pidx = int(row.get("positionIdx") or 0)
        except (TypeError, ValueError):
            pidx = 0
        if pidx != want:
            continue
        st = str(row.get("orderStatus", "") or "")
        if st and st not in active:
            continue
        out.append(row)
    return out


def _quantize_limit_price(price: float, tick: float, *, side_is_buy: bool) -> float:
    if tick <= 1e-12:
        return price
    if side_is_buy:
        return math.floor(price / tick + 1e-12) * tick
    return math.ceil(price / tick - 1e-12) * tick


def _limit_entry_open_price(*, side_buy: bool, mark: float, offset_bps: float, tick: float) -> float | None:
    if mark <= 0.0 or offset_bps < 0.0:
        return None
    frac = offset_bps / 10000.0
    raw = mark * (1.0 - frac) if side_buy else mark * (1.0 + frac)
    q = _quantize_limit_price(raw, tick, side_is_buy=side_buy)
    if side_buy and q >= mark - 1e-9:
        return None
    if not side_buy and q <= mark + 1e-9:
        return None
    return q


def _iteration(
    *,
    args: argparse.Namespace,
    training: dict | None,
    execute: bool,
    iter_count: int,
    hold_on_no_edge: bool,
    refresh_aligned_every: int,
) -> int:
    """Return 0 ok, nonzero severe error (caller may continue loop)."""
    _rg = load_guard_config(_REPO)
    _skip, _rg_reason, _rg_sleep = should_skip_iteration(_rg)
    if _skip:
        _snap = resource_snapshot()
        print(
            "SYGNIF_LOOP_RESOURCE_HOLD "
            + json.dumps(
                {"iter": iter_count, "reason": _rg_reason, **_snap},
                separators=(",", ":"),
            ),
            flush=True,
        )
        time.sleep(_rg_sleep)
        return 0

    wj = str(args.write_json).strip() if not args.no_write_json else ""
    wpath = wj or None
    allow_buy, enhanced, out, pred_ms = run_live_fit(
        symbol=args.symbol,
        kline_limit=args.kline_limit,
        window=args.window,
        data_dir=str(args.data_dir),
        rf_trees=args.rf_trees,
        xgb_estimators=args.xgb_estimators,
        write_json_path=wpath,
    )
    target, why = decide_side(out, training)
    entry_blocked = False
    swarm_gate_ok: bool | None = None
    swarm_reason = ""
    swarm_snapshot: dict | None = None
    fusion_doc_last: dict | None = None
    if execute and _env_flag("SYGNIF_SWARM_GATE_LOOP"):
        os.environ.setdefault("SYGNIF_SWARM_BTC_FUTURE", "1")
        try:
            from swarm_knowledge import compute_swarm  # noqa: PLC0415
        except ImportError:
            from finance_agent.swarm_knowledge import compute_swarm  # noqa: PLC0415
        from nautilus_protocol_fusion import write_fused_sidecar  # noqa: PLC0415
        try:
            from swarm_order_gate import swarm_fusion_allows  # noqa: PLC0415
        except ImportError:
            from finance_agent.swarm_order_gate import swarm_fusion_allows  # noqa: PLC0415
        swarm = compute_swarm()
        swarm_snapshot = swarm
        fusion_doc = write_fused_sidecar(_REPO, btc_prediction_override=out)
        fusion_doc_last = fusion_doc
        swarm_gate_ok, swarm_reason = swarm_fusion_allows(
            target=target,
            swarm=swarm,
            fusion_doc=fusion_doc,
            predict_out=out,
        )
        if not swarm_gate_ok and target in ("long", "short"):
            entry_blocked = True
        _sources = swarm.get("sources") if isinstance(swarm.get("sources"), dict) else {}
        _bf = _sources.get("bf") if isinstance(_sources.get("bf"), dict) else {}
        _btc_fut = swarm.get("btc_future") if isinstance(swarm.get("btc_future"), dict) else {}
        _bf_line: dict[str, object] = {"iter": iter_count, "swarm_gate_ok": swarm_gate_ok}
        if _bf:
            _bf_line["bf_vote"] = _bf.get("vote")
            _bf_line["bf_detail"] = _bf.get("detail")
        if _btc_fut:
            _bf_line["btc_future_enabled"] = _btc_fut.get("enabled")
            _bf_line["btc_future_ok"] = _btc_fut.get("ok")
            if _btc_fut.get("profile") is not None:
                _bf_line["btc_future_profile"] = _btc_fut.get("profile")
        print(
            f"SYGNIF_LOOP_BTC_FUTURE {json.dumps(_bf_line, separators=(',', ':'), default=str)}",
            flush=True,
        )
    elif _env_flag("SYGNIF_PROTOCOL_FUSION_SYNC"):
        try:
            from nautilus_protocol_fusion import write_fused_sidecar  # noqa: PLC0415

            fusion_sync = write_fused_sidecar(_REPO, btc_prediction_override=out)
            fusion_doc_last = fusion_sync
            fus = fusion_sync.get("fusion") if isinstance(fusion_sync, dict) else {}
            print(
                "SYGNIF_LOOP_FUSION_SYNC "
                + json.dumps(
                    {
                        "iter": iter_count,
                        "label": fus.get("label"),
                        "sum": fus.get("sum"),
                        "nautilus_detail": fus.get("nautilus_detail"),
                        "ml_detail": fus.get("ml_detail"),
                    },
                    separators=(",", ":"),
                    default=str,
                ),
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"SYGNIF_LOOP_WARN fusion_sync: {exc}", flush=True)

    mn_usdt = getattr(args, "manual_notional_usdt", None)
    if mn_usdt is not None:
        try:
            mn0 = float(mn_usdt)
            if mn0 > 0:
                mn1 = _clip_manual_notional_letscrash(mn0)
                if mn1 != mn0:
                    print(
                        f"SYGNIF_LOOP_LETSCRASH notional_usdt {mn0} -> {mn1} "
                        f"(rule_proof_bucket.notional_cap_usdt)",
                        flush=True,
                    )
                    args.manual_notional_usdt = mn1
        except (TypeError, ValueError):
            pass

    # Optional: treat high LogReg confidence as sufficient to pass Swarm **entry** gate (demo / research).
    _bypass_min = env_float("SYGNIF_PREDICT_LOGREG_BYPASS_MIN_CONF", 0.0)
    if (
        entry_blocked
        and _bypass_min > 0
        and execute
        and _env_flag("SYGNIF_SWARM_GATE_LOOP")
        and target in ("long", "short")
    ):
        _lcb = logreg_confidence(out)
        if _lcb + 1e-9 >= _bypass_min and logreg_aligns_target(out, target):
            entry_blocked = False
            swarm_gate_ok = True
            swarm_reason = f"logreg_bypass_min_conf:{_bypass_min:.0f}_actual:{_lcb:.1f}"
            print(
                "SYGNIF_LOOP_LOGREG_BYPASS "
                + json.dumps(
                    {"target": target, "logreg_conf": round(_lcb, 2), "min_conf": _bypass_min},
                    separators=(",", ":"),
                ),
                flush=True,
            )

    synthetic_entry_blocked = False
    synthetic_block_reason = ""
    if _env_flag("SYGNIF_PREDICT_BLOCK_SYNTHETIC_HOLD"):
        try:
            from predict_synthetic_guard import evaluate_synthetic_entry_block  # noqa: E402
        except ImportError:
            evaluate_synthetic_entry_block = None  # type: ignore[misc,assignment]
        if evaluate_synthetic_entry_block is not None:
            _sblk, _sreas = evaluate_synthetic_entry_block(_REPO)
            if _sblk:
                synthetic_entry_blocked = True
                synthetic_block_reason = _sreas
                if target in ("long", "short"):
                    print(
                        "SYGNIF_LOOP_SYNTH_GATE "
                        + json.dumps({"target": target, "reason": _sreas}, separators=(",", ":")),
                        flush=True,
                    )

    open_target = None if (entry_blocked or synthetic_entry_blocked) else target
    move_pct, close = move_pct_and_close(out)
    lev, t_move = leverage_from_move_pct(move_pct)
    if args.manual_leverage is not None:
        # Manual override: cap by BYBIT_DEMO_MANUAL_LEVERAGE_MAX (default 100), not auto band (often 5–25).
        lo = env_int("BYBIT_DEMO_ORDER_MIN_LEVERAGE", 1, lo=1, hi=125)
        hi_manual = env_int("BYBIT_DEMO_MANUAL_LEVERAGE_MAX", 100, lo=1, hi=125)
        lev = max(lo, min(hi_manual, int(round(float(args.manual_leverage)))))
    lconf = logreg_confidence(out)

    mn_arg = getattr(args, "manual_notional_usdt", None)
    planned_qty_from_notional: float | None = None
    if mn_arg is not None and float(mn_arg) > 0 and close > 0:
        step = 0.001
        planned_qty_from_notional = math.floor((float(mn_arg) / close) / step) * step

    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = {
        "ts_utc": ts,
        "iter": iter_count,
        "predict_ms": round(pred_ms, 1),
        "allow_buy": allow_buy,
        "enhanced": enhanced,
        "target_side": target,
        "target_reason": why,
        "leverage_would": lev,
        "move_pct": round(move_pct, 6),
        "hold_on_no_edge": hold_on_no_edge,
    }
    if swarm_gate_ok is not None:
        line["swarm_gate_ok"] = swarm_gate_ok
        line["swarm_reason"] = swarm_reason
        line["entry_blocked"] = entry_blocked
        line["open_target_side"] = open_target
        if swarm_snapshot:
            _src = swarm_snapshot.get("sources") if isinstance(swarm_snapshot.get("sources"), dict) else {}
            _hm = _src.get("hm") if isinstance(_src.get("hm"), dict) else {}
            if _hm:
                line["hm_vote"] = _hm.get("vote")
                line["hm_detail"] = _hm.get("detail")
            sce = swarm_snapshot.get("swarm_core_engine")
            if sce is not None:
                line["swarm_core_engine"] = sce
    line["synthetic_entry_blocked"] = synthetic_entry_blocked
    if synthetic_block_reason:
        line["synthetic_block_reason"] = synthetic_block_reason
    if mn_arg is not None:
        line["manual_notional_usdt"] = float(mn_arg)
    if planned_qty_from_notional is not None:
        line["planned_qty_from_notional"] = planned_qty_from_notional
    _hmp = (out.get("predictions") or {}).get("hivemind")
    if isinstance(_hmp, dict) and _hmp.get("fusion_enabled"):
        line["predict_hivemind_vote"] = _hmp.get("vote")
        _hex = _hmp.get("explore")
        if isinstance(_hex, dict) and "ok" in _hex:
            line["predict_hivemind_explore_ok"] = _hex.get("ok")
    _nmeta = out.get("nautilus_consensus_meta")
    if isinstance(_nmeta, dict) and _nmeta.get("hivemind_prediction_note"):
        line["predict_hivemind_note"] = _nmeta.get("hivemind_prediction_note")
    if isinstance(fusion_doc_last, dict):
        umb = fusion_doc_last.get("usd_btc_macro")
        if isinstance(umb, dict):
            _ms = umb.get("macro_source")
            if _ms is not None:
                line["usd_btc_macro_source"] = _ms
            _pc = umb.get("pearson_correlation_daily_returns")
            if isinstance(_pc, dict):
                if _pc.get("pearson_last_20d") is not None:
                    line["usd_idx_corr_20d"] = _pc.get("pearson_last_20d")
                if _pc.get("pearson_last_60d") is not None:
                    line["usd_idx_corr_60d"] = _pc.get("pearson_last_60d")
            if umb.get("last_usd_index_return") is not None:
                line["usd_idx_last_ret"] = umb.get("last_usd_index_return")
            if umb.get("last_common_date") is not None:
                line["usd_idx_last_date"] = umb.get("last_common_date")
    if target in ("long", "short"):
        line["relative_modeled_edge_pct"] = round(relative_modeled_edge_pct(out, target), 6)
        line["per_trade_fee_usdt"] = round(per_trade_fee_usdt(), 4)
    _floor_base = open_modeled_edge_floor_usdt()
    _floor_e = effective_open_edge_floor_usdt(move_pct)
    if _floor_base > 0 and target in ("long", "short"):
        _per = modeled_edge_usdt_per_btc(out, target)
        line["min_edge_profit_usdt"] = round(_floor_e, 4)
        if abs(_floor_e - _floor_base) > 1e-6:
            line["min_edge_profit_usdt_base"] = round(_floor_base, 4)
        line["modeled_edge_usdt_per_btc"] = round(_per, 4)
        if planned_qty_from_notional is not None:
            line["modeled_profit_usdt_est"] = round(
                modeled_profit_usdt_at_qty(out, target, planned_qty_from_notional), 4
            )
    print(f"SYGNIF_LOOP_PREDICT {json.dumps(line, separators=(',', ':'))}", flush=True)
    if entry_blocked:
        print(f"SYGNIF_LOOP_SWARM_BLOCK model_target={target!r} reason={swarm_reason!r}", flush=True)

    try:
        from neurolinked_predict_loop_hook import push_neurolinked_network  # noqa: PLC0415

        _nl_out = push_neurolinked_network(_REPO, iter_count, swarm_snapshot, predict_meta=line)
        if not _nl_out.get("skipped"):
            _nl_log = {
                "iter": iter_count,
                "channel_json": _nl_out.get("channel_json"),
                "text_chars": _nl_out.get("text_chars"),
                "http": _nl_out.get("http") or _nl_out.get("http_status"),
            }
            if _nl_out.get("http_error"):
                _nl_log["http_error"] = _nl_out.get("http_error")
            print(f"SYGNIF_LOOP_NEUROLINKED {json.dumps(_nl_log, separators=(',', ':'), default=str)}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"SYGNIF_LOOP_NEUROLINKED_WARN {exc!r}", flush=True)

    if (
        os.environ.get("SYGNIF_PROTOCOL_FUSION_TICK", "").strip().lower()
        in ("1", "true", "yes", "on")
    ):
        try:
            from nautilus_protocol_fusion import record_protocol_tick  # noqa: PLC0415

            record_protocol_tick(_REPO, {**line, "execute": execute})
        except Exception as exc:  # noqa: BLE001
            print(f"SYGNIF_LOOP_WARN fusion_tick: {exc}", flush=True)

    _ds_path = _protocol_dataset_path(_REPO, execute)
    if _ds_path is not None:
        try:
            _append_protocol_dataset_jsonl(
                _ds_path,
                repo=_REPO,
                args=args,
                iter_count=iter_count,
                execute=execute,
                line=line,
                out=out,
                swarm_snapshot=swarm_snapshot,
                fusion_doc_last=fusion_doc_last,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"SYGNIF_LOOP_WARN dataset_jsonl: {exc}", flush=True)

    if not execute:
        try:
            pr = blh.position_list(args.symbol)
        except RuntimeError:
            pos_side, pos_sz, pos_raw = None, 0.0, ""
        else:
            pos_side, pos_sz, pos_raw = parse_linear_position(
                pr, args.symbol, getattr(args, "position_idx", 0)
            )
        nc = need_close_position(pos_side, target, hold_on_no_edge=hold_on_no_edge)
        extra_sz = ""
        if planned_qty_from_notional is not None:
            extra_sz = f" planned_qty≈{planned_qty_from_notional} (from notional)"
        print(
            f"SYGNIF_LOOP_DRY position={pos_side!r} sz={pos_sz} target={target!r} "
            f"would_close={nc} hold_on_no_edge={hold_on_no_edge}{extra_sz} "
            f"(set SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES --execute for venue)",
            flush=True,
        )
        return 0

    # --- live reconcile ---
    os.environ.setdefault("SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL", "1")
    os.environ.setdefault("SYGNIF_SWARM_TPSL_PROFILE", "reward_risk")
    os.environ.setdefault("SYGNIF_SWARM_TP_USDT_TARGET", "600")
    os.environ.setdefault("SYGNIF_SWARM_SL_USDT_TARGET", "360")

    try:
        pr = blh.position_list(args.symbol)
    except RuntimeError as exc:
        print(f"SYGNIF_LOOP_ERR position_list: {exc}", flush=True)
        return 3

    _maybe_switch_hedge_mode(args.symbol)
    close_sleep = max(0.0, env_float("PREDICT_LOOP_POST_CLOSE_SLEEP_SEC", 0.75))
    pidx = args.position_idx
    sym_u = (args.symbol or "").replace("/", "").upper().strip() or "BTCUSDT"
    pos_side, pos_sz, pos_raw = parse_linear_position(pr, args.symbol, pidx)
    upnl = linear_leg_unrealised_usdt(pr, sym_u, pidx)
    # Reverse trade vs panic flatten: when ML target is flat/no-edge but Heavy91 failure-swing
    # signals a counter-trade, flip target so we close+reopen instead of reduce-only flatten to cash.
    if (
        _env_flag("SYGNIF_PREDICT_FAILURE_SWING_PANIC_REVERSE")
        and pos_side in ("long", "short")
        and not hold_on_no_edge
        and target is None
    ):
        fs91 = (out.get("predictions") or {}).get("failure_swing_heavy91")
        if isinstance(fs91, dict) and fs91.get("ok"):
            _pr = False
            if pos_side == "long" and fs91.get("entry_short"):
                target, why = "short", "panic_reverse:failure_swing_heavy91_short"
                _pr = True
            elif pos_side == "short" and fs91.get("entry_long"):
                target, why = "long", "panic_reverse:failure_swing_heavy91_long"
                _pr = True
            if _pr:
                print(
                    "SYGNIF_LOOP_PANIC_REVERSE "
                    + json.dumps(
                        {"leg": pos_side, "new_target": target, "reason": why},
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
                if (
                    _env_flag("SYGNIF_SWARM_GATE_LOOP")
                    and swarm_snapshot is not None
                    and fusion_doc_last is not None
                    and target in ("long", "short")
                ):
                    try:
                        from swarm_order_gate import swarm_fusion_allows  # noqa: PLC0415
                    except ImportError:
                        from finance_agent.swarm_order_gate import swarm_fusion_allows  # noqa: PLC0415
                    swarm_gate_ok, swarm_reason = swarm_fusion_allows(
                        target=target,
                        swarm=swarm_snapshot,
                        fusion_doc=fusion_doc_last,
                        predict_out=out,
                    )
                    entry_blocked = not swarm_gate_ok and target in ("long", "short")
                else:
                    entry_blocked = False
                open_target = None if entry_blocked else target
    if _raw_opposite_signal(pos_side, target):
        _OPPOSITE_STREAK[sym_u] = _OPPOSITE_STREAK.get(sym_u, 0) + 1
    else:
        _OPPOSITE_STREAK[sym_u] = 0

    def _exit_kind_for_close() -> str:
        if target is None:
            return "no_edge_flat"
        if pos_side == "long" and target == "short":
            return "opposite_signal"
        if pos_side == "short" and target == "long":
            return "opposite_signal"
        return "reconcile"

    def _do_close(exit_kind: str, pos_meta: dict) -> bool:
        nonlocal pos_side, pos_sz, pos_raw
        global _TRAIL_PLANTED
        if pos_side is None or pos_sz <= 0:
            return True
        qclose = _close_qty_str(pos_raw, pos_sz)
        if pos_side == "long":
            side_venue = "Sell"
        else:
            side_venue = "Buy"
        link_c = iface_tags.order_link_close(iter_count)
        mo = blh.create_market_order(
            args.symbol,
            side_venue,
            qclose,
            pidx,
            reduce_only=True,
            order_link_id=link_c,
        )
        print(f"SYGNIF_LOOP_CLOSE {json.dumps(mo, default=str)}", flush=True)
        if mo.get("retCode") != 0:
            return False
        c_oid = iface_tags.order_id_from_create_response(mo)
        if c_oid:
            iface_tags.append_journal(
                {
                    "symbol": sym_u,
                    "action": "close",
                    "order_id": c_oid,
                    "order_link_id": link_c,
                    "open_tag": (pos_meta.get("open_tag") or "predict_loop"),
                    "close_tag": "predict_loop_close",
                    "exit_kind": exit_kind,
                    "open_order_id": pos_meta.get("open_order_id") or "",
                    "open_detail": (pos_meta.get("open_detail") or "")[:2000],
                }
            )
        iface_tags.clear_position_symbol(sym_u)
        _TRAIL_PLANTED.discard(sym_u)
        _POSITION_OPEN_MONOTONIC.pop(sym_u, None)
        time.sleep(close_sleep)
        pr2 = blh.position_list(args.symbol)
        pos_side, pos_sz, pos_raw = parse_linear_position(pr2, args.symbol, pidx)
        return True

    # Exit / flip: opposite target, or no-edge when not holding through chop
    need_flat = need_close_position(pos_side, target, hold_on_no_edge=hold_on_no_edge)
    if (
        _env_flag("SYGNIF_PREDICT_HOLD_UNTIL_PROFIT")
        and pos_side in ("long", "short")
        and need_flat
        and target in ("long", "short")
        and pos_side != target
        and upnl is not None
        and upnl < _min_upnl_to_close_usdt()
    ):
        need_flat = False
        print(
            "SYGNIF_LOOP_HOLD_UNTIL_PROFIT "
            + json.dumps(
                {
                    "upnl": round(upnl, 6),
                    "min_upnl": round(_min_upnl_to_close_usdt(), 6),
                    "leg": pos_side,
                    "target": target,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
    raw_opp = _raw_opposite_signal(pos_side, target)
    if need_flat and raw_opp:
        cfm = _opposite_confirm_iters()
        if cfm > 0 and _OPPOSITE_STREAK.get(sym_u, 0) < cfm:
            need_flat = False
            print(
                "SYGNIF_LOOP_OPPOSITE_DEBOUNCE "
                + json.dumps(
                    {
                        "streak": _OPPOSITE_STREAK.get(sym_u, 0),
                        "need_iters": cfm,
                        "leg": pos_side,
                        "target": target,
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
    if need_flat and pos_side is not None:
        ek_hold = _exit_kind_for_close()
        if _min_hold_blocks_close(sym_u, exit_kind=ek_hold):
            need_flat = False
            age = time.monotonic() - (_POSITION_OPEN_MONOTONIC.get(sym_u) or 0.0)
            print(
                "SYGNIF_LOOP_MIN_HOLD "
                + json.dumps(
                    {
                        "exit_kind": ek_hold,
                        "min_sec": round(_min_discretionary_close_sec(), 3),
                        "age_sec": round(age, 3),
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
    hold_flip_swarm = (
        execute
        and _env_flag("SWARM_PORTFOLIO_AUTHORITY")
        and _env_flag("SYGNIF_SWARM_GATE_LOOP")
        and entry_blocked
        and pos_side in ("long", "short")
        and target in ("long", "short")
        and pos_side != target
    )
    if hold_flip_swarm:
        print(
            f"SYGNIF_LOOP_SWARM_PORTFOLIO hold leg={pos_side!r} model_target={target!r} "
            f"(Swarm blocked entry — no flip-close) reason={swarm_reason!r}",
            flush=True,
        )
    if pos_side is not None and need_flat and not hold_flip_swarm:
        pos_meta = iface_tags.load_position_meta().get(sym_u, {})
        if not _do_close(_exit_kind_for_close(), pos_meta):
            return 4
        # re-fetch after close
        pr = blh.position_list(args.symbol)
        pos_side, pos_sz, pos_raw = parse_linear_position(pr, args.symbol, pidx)
        upnl = linear_leg_unrealised_usdt(pr, sym_u, pidx)

    # When model and venue already agree, optionally close+reopen so orders still run (demo / iface tags).
    ren = max(0, int(refresh_aligned_every))
    if (
        ren > 0
        and pos_side is not None
        and open_target == pos_side
        and open_target in ("long", "short")
        and pos_sz > 1e-9
        and iter_count % ren == 0
    ):
        if _min_hold_blocks_close(sym_u, exit_kind="aligned_refresh"):
            print(
                "SYGNIF_LOOP_SKIP_REFRESH min_hold "
                + json.dumps(
                    {
                        "min_sec": round(_min_discretionary_close_sec(), 3),
                        "age_sec": round(
                            time.monotonic() - (_POSITION_OPEN_MONOTONIC.get(sym_u) or 0.0),
                            3,
                        ),
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
        else:
            skip_rf = (
                _env_flag("SYGNIF_PREDICT_HOLD_UNTIL_PROFIT")
                and upnl is not None
                and upnl < _min_upnl_to_close_usdt()
            )
            if skip_rf:
                print(
                    "SYGNIF_LOOP_SKIP_REFRESH hold_until_profit "
                    + json.dumps(
                        {"upnl": round(upnl, 6), "min_upnl": round(_min_upnl_to_close_usdt(), 6)},
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
            else:
                pos_meta = iface_tags.load_position_meta().get(sym_u, {})
                print(
                    f"SYGNIF_LOOP_REFRESH aligned iter={iter_count} every={ren} side={pos_side!r} "
                    "— close then re-enter",
                    flush=True,
                )
                if not _do_close("aligned_refresh", pos_meta):
                    return 4
                pr = blh.position_list(args.symbol)
                pos_side, pos_sz, pos_raw = parse_linear_position(pr, args.symbol, pidx)
                upnl = linear_leg_unrealised_usdt(pr, sym_u, pidx)

    # Entry: flat and have target (Swarm may block **entry**; ``SWARM_PORTFOLIO_AUTHORITY`` can also block flip-closes)
    if open_target in ("long", "short") and (pos_side is None or pos_sz < 1e-9):
        ok_ft, ft_reason = _ft_entry_allowed(_REPO, sym_u, iter_count=iter_count)
        if not ok_ft:
            print(
                "SYGNIF_LOOP_FT_PROT "
                + json.dumps({"block": True, "reason": ft_reason}, separators=(",", ":")),
                flush=True,
            )
            return 0
        try:
            w = blh.wallet_balance_unified_coin("USDT")
            free = parse_usdt_available(w)
        except RuntimeError as exc:
            print(f"SYGNIF_LOOP_ERR wallet: {exc}", flush=True)
            return 5
        if free is None or free <= 0:
            print("SYGNIF_LOOP_ERR no free USDT", flush=True)
            return 5
        min_eq = env_float("SYGNIF_PREDICT_MIN_WALLET_EQUITY_USDT", 0.0)
        if min_eq > 0:
            eq = parse_usdt_equity(w)
            if eq is not None and eq + 1e-9 < min_eq:
                print(
                    "SYGNIF_LOOP_MIN_EQUITY_BLOCK "
                    + json.dumps(
                        {"equity_usdt": round(eq, 4), "min_required": min_eq},
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
                return 0
        mn = getattr(args, "manual_notional_usdt", None)
        if mn is not None and float(mn) > 0:
            if close <= 0:
                print(f"SYGNIF_LOOP_ERR manual-notional needs positive close, got {close}", flush=True)
                return 6
            raw_qty = float(mn) / close
            step = 0.001
            q = math.floor(raw_qty / step) * step
            min_q = max(1e-9, env_float("BYBIT_DEMO_ORDER_MIN_QTY", 0.001))
            if q + 1e-12 < min_q:
                print(
                    f"SYGNIF_LOOP_ERR notional→qty below min_qty {min_q} (got {q})",
                    flush=True,
                )
                return 6
            qty_s = f"{q:.6f}".rstrip("0").rstrip(".") or str(min_q)
        elif args.manual_qty is not None and str(args.manual_qty).strip():
            qty_s = str(args.manual_qty).strip()
        else:
            qty_s, _eff = qty_btc(free_usdt=free, close=close, t_move=t_move, logreg_conf=lconf)
        if not qty_s:
            print("SYGNIF_LOOP_ERR qty below min", flush=True)
            return 6
        min_edge = effective_open_edge_floor_usdt(move_pct)
        floor_base = open_modeled_edge_floor_usdt()
        if min_edge > 0:
            try:
                q_gate = float(qty_s)
            except (TypeError, ValueError):
                q_gate = 0.0
            per_btc = modeled_edge_usdt_per_btc(out, open_target or "")
            exp_usdt = modeled_profit_usdt_at_qty(out, open_target or "", q_gate)
            if exp_usdt + 1e-12 < min_edge:
                print(
                    f"SYGNIF_LOOP_EDGE_GATE modeled_profit_usdt≈{exp_usdt:.2f} "
                    f"(edge_per_btc={per_btc:.2f} qty={qty_s}) min_floor={min_edge:.2f} "
                    f"base_floor={floor_base:.2f} move_pct={move_pct:.6f} — skip open",
                    flush=True,
                )
                return 0
        lr = blh.set_linear_leverage(args.symbol, str(lev))
        print(f"SYGNIF_LOOP_LEV {json.dumps(lr, default=str)}", flush=True)
        lrc = lr.get("retCode")
        # Bybit: 110043 = leverage already at requested value — safe to continue
        if lrc not in (0, 110043):
            return 7
        order_side = "Buy" if open_target == "long" else "Sell"
        link_o = iface_tags.order_link_open(iter_count, open_target == "long")
        exec_m = _predict_entry_execution_mode()
        mo: dict = {}
        used_limit = False

        if exec_m == "limit_postonly":
            pend = _working_non_reduce_linear_orders(args.symbol, pidx)
            if pend:
                print(
                    "SYGNIF_LOOP_ENTRY_PENDING "
                    + json.dumps({"symbol": sym_u, "n": len(pend), "positionIdx": pidx}, separators=(",", ":")),
                    flush=True,
                )
                return 0
            offset_bps = max(0.0, env_float("SYGNIF_PREDICT_ENTRY_LIMIT_OFFSET_BPS", 5.0))
            tick = max(1e-6, env_float("SYGNIF_PREDICT_ENTRY_PRICE_TICK", 0.1))
            mark, _lst = blh.linear_mark_and_last_price(args.symbol)
            side_buy = open_target == "long"
            lim_px: float | None = None
            if mark and float(mark) > 0:
                lim_px = _limit_entry_open_price(
                    side_buy=side_buy,
                    mark=float(mark),
                    offset_bps=offset_bps,
                    tick=tick,
                )
            if lim_px is None or lim_px <= 0:
                if _entry_limit_fallback_market_enabled():
                    exec_m = "market"
                else:
                    print(
                        "SYGNIF_LOOP_ERR limit_entry: no valid PostOnly price vs mark "
                        "(widen SYGNIF_PREDICT_ENTRY_LIMIT_OFFSET_BPS or enable "
                        "SYGNIF_PREDICT_ENTRY_LIMIT_FALLBACK_MARKET=1)",
                        flush=True,
                    )
                    return 8
            else:
                dec = max(0, int(float(os.environ.get("SYGNIF_PREDICT_ENTRY_PRICE_DECIMALS", "2") or 2)))
                lim_s = f"{lim_px:.{dec}f}".rstrip("0").rstrip(".") or f"{lim_px}"
                mo = blh.create_limit_order(
                    args.symbol,
                    order_side,
                    qty_s,
                    pidx,
                    lim_s,
                    time_in_force="PostOnly",
                    reduce_only=False,
                    order_link_id=link_o,
                )
                used_limit = True
                print(
                    "SYGNIF_LOOP_OPEN_LIMIT "
                    + json.dumps({"resp": mo, "price": lim_s, "mark": mark}, default=str, separators=(",", ":")),
                    flush=True,
                )

        if not used_limit:
            mo = blh.create_market_order(
                args.symbol,
                order_side,
                qty_s,
                pidx,
                reduce_only=False,
                order_link_id=link_o,
            )
            print(f"SYGNIF_LOOP_OPEN {json.dumps(mo, default=str)}", flush=True)
        if mo.get("retCode") != 0 and used_limit and _entry_limit_fallback_market_enabled():
            mo = blh.create_market_order(
                args.symbol,
                order_side,
                qty_s,
                pidx,
                reduce_only=False,
                order_link_id=link_o,
            )
            used_limit = False
            print(f"SYGNIF_LOOP_OPEN limit_fail_fallback_market {json.dumps(mo, default=str)}", flush=True)
        if mo.get("retCode") != 0:
            _ft_record_open_fail(_REPO, sym_u)
            return 8
        if used_limit:
            wait_sec = max(0.0, env_float("SYGNIF_PREDICT_ENTRY_LIMIT_FILL_WAIT_SEC", 4.0))
            t_dead = time.time() + wait_sec
            got_pos = False
            while time.time() < t_dead:
                try:
                    prp = blh.position_list(args.symbol)
                except RuntimeError:
                    time.sleep(0.35)
                    continue
                ps2, sz2, raw2 = parse_linear_position(prp, args.symbol, pidx)
                if ps2 and sz2 > 1e-9:
                    pos_side, pos_sz, pos_raw = ps2, sz2, raw2
                    got_pos = True
                    break
                time.sleep(0.35)
            if not got_pos:
                print(
                    "SYGNIF_LOOP_OPEN_LIMIT_RESTING "
                    + json.dumps(
                        {"orderLinkId": link_o, "wait_sec": wait_sec},
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
                return 0
        _ft_record_open_success(_REPO, sym_u, iter_count=iter_count)
        _POSITION_OPEN_MONOTONIC[sym_u] = time.monotonic()
        o_oid = iface_tags.order_id_from_create_response(mo)
        if o_oid:
            detail = (why or "")[:2000]
            iface_tags.append_journal(
                {
                    "symbol": sym_u,
                    "action": "open",
                    "order_id": o_oid,
                    "order_link_id": link_o,
                    "open_tag": "predict_loop",
                    "side": order_side,
                    "open_detail": detail,
                }
            )
            iface_tags.set_position_open(
                sym_u,
                open_tag="predict_loop",
                open_detail=detail,
                open_order_id=o_oid,
                open_order_link_id=link_o,
                pos_side=open_target or "",
            )
            _TRAIL_PLANTED.discard(sym_u)
            tpsl_full_ok = _apply_swarm_tpsl_after_open(sym_u)
            tp_tgt = env_float("SYGNIF_SWARM_TP_USDT_TARGET", 0.0)
            if tp_tgt > 0 and not tpsl_full_ok:
                try:
                    avg_px: float | None = None
                    liq_px: float | None = None
                    mark_px: float | None = None
                    pr_tp: dict = {}
                    for _ in range(10):
                        pr_tp = blh.position_list(args.symbol)
                        avg_px, liq_px, mark_px = _avg_liq_mark_from_position_list(pr_tp, args.symbol)
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
                        if open_target == "long":
                            tp_px = avg_px + delta_tp
                            sl_px = avg_px - delta_sl
                        else:
                            tp_px = avg_px - delta_tp
                            sl_px = avg_px + delta_sl
                        sym_side = "Buy" if open_target == "long" else "Sell"
                        mk = float(mark_px or 0.0)
                        if mk <= 0.0:
                            mk = float(avg_px)
                        buf = env_float("SYGNIF_SWARM_SL_LIQ_BUFFER_BPS", 8.0)
                        liq_on = (os.environ.get("SYGNIF_SWARM_SL_LIQ_ANCHOR", "1") or "").strip().lower() not in (
                            "0",
                            "false",
                            "no",
                            "off",
                        )
                        sl_px, sl_meta = finalize_linear_stop_loss(
                            side=sym_side,
                            sl=float(sl_px),
                            mark=mk,
                            liq_price=float(liq_px) if liq_px and liq_px > 0 else None,
                            liq_buffer_bps=buf,
                            liq_anchor_enabled=bool(liq_on and liq_px and liq_px > 0),
                        )
                        tp_px, tp_meta = finalize_linear_take_profit(
                            side=sym_side,
                            tp=float(tp_px),
                            mark=mk,
                        )
                        if sl_meta or tp_meta:
                            print(
                                "SYGNIF_LOOP_TP_SL_ANCHOR "
                                + json.dumps({**sl_meta, **tp_meta}, default=str),
                                flush=True,
                            )
                        tp_s = f"{tp_px:.2f}"
                        sl_s = f"{sl_px:.2f}"
                        tsr = blh.set_trading_stop_linear(
                            args.symbol,
                            position_idx=pidx,
                            take_profit=tp_s,
                            stop_loss=sl_s,
                            tp_trigger_by="MarkPrice",
                            sl_trigger_by="MarkPrice",
                        )
                        print(
                            f"SYGNIF_LOOP_TP_SL {json.dumps(tsr, default=str)} "
                            f"target_tp_usdt={tp_tgt} target_sl_usdt={sl_tgt}",
                            flush=True,
                        )
                    else:
                        print(
                            "SYGNIF_LOOP_WARN tp_sl_target: no avg entry after open "
                            f"(retCode={pr_tp.get('retCode')})",
                            flush=True,
                        )
                except Exception as exc:  # noqa: BLE001
                    print(f"SYGNIF_LOOP_WARN tp_sl_target: {exc}", flush=True)

    elif pos_side == target and target is not None:
        print(f"SYGNIF_LOOP_HOLD {pos_side!r} aligned with target", flush=True)
        trail_step = env_float("SYGNIF_PREDICT_TRAIL_MOVE_USDT", 0.0)
        if (
            trail_step > 0.0
            and sym_u not in _TRAIL_PLANTED
            and upnl is not None
            and upnl >= _min_upnl_to_close_usdt()
            and pos_sz > 1e-12
        ):
            try:
                dist = trail_step / float(pos_sz)
                if dist > 1e-12:
                    tsr = blh.set_trading_stop_linear(
                        args.symbol,
                        position_idx=pidx,
                        trailing_stop=f"{dist:.2f}",
                        take_profit="0",
                        stop_loss="0",
                    )
                    print(
                        f"SYGNIF_LOOP_TRAIL {json.dumps(tsr, default=str)} "
                        f"trailing_price_step≈{dist:.2f} from_usdt={trail_step}",
                        flush=True,
                    )
                    if int(tsr.get("retCode") or -1) == 0:
                        _TRAIL_PLANTED.add(sym_u)
            except Exception as exc:  # noqa: BLE001
                print(f"SYGNIF_LOOP_WARN trail: {exc}", flush=True)
    elif pos_side is not None and target is None and hold_on_no_edge:
        print(
            f"SYGNIF_LOOP_HOLD {pos_side!r} no-edge signal — keeping position (hold_on_no_edge)",
            flush=True,
        )

    return 0


def run_test_tag_order(args: argparse.Namespace) -> int:
    """
    Place minimal linear **Buy** (open / net-long), then **reduce-only** close on the **actual** venue leg
    (Sell if long, Buy if short) with ``predict_loop`` journal rows for BTC Interface **PREDICT** tags.
    """
    sym_u = (args.symbol or "").replace("/", "").upper().strip() or "BTCUSDT"
    pidx = args.position_idx
    qty_s = (os.environ.get("SYGNIF_TEST_TAG_ORDER_QTY", "0.001") or "0.001").strip()
    detail = (
        os.environ.get("SYGNIF_TEST_TAG_ORDER_DETAIL", "").strip()
        or "SYGNIF_TEST_TAG_ORDER verify predict_loop → dashboard PREDICT badge"
    )[:2000]
    sleep_mid = max(0.25, float(os.environ.get("SYGNIF_TEST_TAG_ORDER_SLEEP_SEC", "1") or 1))

    try:
        pr = blh.position_list(args.symbol)
    except RuntimeError as exc:
        print(f"SYGNIF_TEST_TAG_ERR position_list {exc}", flush=True)
        return 3

    pos_side, pos_sz, pos_raw = parse_linear_position(pr, args.symbol, pidx)
    if pos_sz and pos_sz > 1e-9:
        print(
            f"SYGNIF_TEST_TAG_ERR not flat (side={pos_side!r} sz={pos_sz}); flatten first",
            flush=True,
        )
        return 10

    lev = env_int("BYBIT_DEMO_ORDER_MIN_LEVERAGE", 5, lo=1, hi=125)
    lr = blh.set_linear_leverage(args.symbol, str(lev))
    print(f"SYGNIF_TEST_TAG_LEV {json.dumps(lr, default=str)}", flush=True)
    if lr.get("retCode") not in (0, 110043):
        return 7

    link_o = iface_tags.order_link_verify_open()
    mo = blh.create_market_order(
        args.symbol,
        "Buy",
        qty_s,
        pidx,
        reduce_only=False,
        order_link_id=link_o,
    )
    print(f"SYGNIF_TEST_TAG_OPEN {json.dumps(mo, default=str)}", flush=True)
    if mo.get("retCode") != 0:
        return 8
    o_oid = iface_tags.order_id_from_create_response(mo)
    if not o_oid:
        print("SYGNIF_TEST_TAG_ERR missing open orderId", flush=True)
        return 12
    iface_tags.append_journal(
        {
            "symbol": sym_u,
            "action": "open",
            "order_id": o_oid,
            "order_link_id": link_o,
            "open_tag": "predict_loop",
            "side": "Buy",
            "open_detail": detail,
        }
    )
    iface_tags.set_position_open(
        sym_u,
        open_tag="predict_loop",
        open_detail=detail,
        open_order_id=o_oid,
        open_order_link_id=link_o,
        pos_side="long",
    )

    open_wait = max(sleep_mid, float(os.environ.get("SYGNIF_TEST_TAG_ORDER_OPEN_WAIT_SEC", "8") or 8))
    deadline = time.time() + open_wait
    pos_side, pos_sz, pos_raw = None, 0.0, ""
    while time.time() < deadline:
        time.sleep(0.25)
        try:
            pr = blh.position_list(args.symbol)
        except RuntimeError as exc:
            print(f"SYGNIF_TEST_TAG_WARN position_list after open {exc}", flush=True)
            continue
        pos_side, pos_sz, pos_raw = parse_linear_position(pr, args.symbol, pidx)
        if pos_side is not None and pos_sz > 1e-9:
            break
    if pos_side is None or pos_sz < 1e-9:
        print("SYGNIF_TEST_TAG_ERR no position after open (increase SYGNIF_TEST_TAG_ORDER_OPEN_WAIT_SEC?)", flush=True)
        iface_tags.clear_position_symbol(sym_u)
        return 11

    if pos_side != "long":
        print(
            f"SYGNIF_TEST_TAG_WARN after Buy open, venue reports side={pos_side!r} (one-way reduce of prior short?); "
            "closing with matching reduce-only side",
            flush=True,
        )

    pos_meta = iface_tags.load_position_meta().get(sym_u, {})
    mo2: dict = {}
    close_link_used = ""
    for attempt in range(5):
        try:
            prc = blh.position_list(args.symbol)
        except RuntimeError as exc:
            print(f"SYGNIF_TEST_TAG_WARN pre-close position_list {exc}", flush=True)
            time.sleep(0.2)
            continue
        pos_side, pos_sz, pos_raw = parse_linear_position(prc, args.symbol, pidx)
        if pos_side is None or pos_sz < 1e-9:
            if attempt == 0:
                print(
                    "SYGNIF_TEST_TAG_WARN flat before close (external flatten?); open journal only",
                    flush=True,
                )
                iface_tags.clear_position_symbol(sym_u)
                return 0
            print(
                "SYGNIF_TEST_TAG_WARN flat during close retries; assuming external close",
                flush=True,
            )
            iface_tags.clear_position_symbol(sym_u)
            return 0
        qclose = _close_qty_str(pos_raw, pos_sz)
        close_venue = "Sell" if pos_side == "long" else "Buy"
        close_link_used = iface_tags.order_link_verify_close()
        mo2 = blh.create_market_order(
            args.symbol,
            close_venue,
            qclose,
            pidx,
            reduce_only=True,
            order_link_id=close_link_used,
        )
        print(f"SYGNIF_TEST_TAG_CLOSE {json.dumps(mo2, default=str)}", flush=True)
        if mo2.get("retCode") == 0:
            break
        if mo2.get("retCode") == 110017:
            time.sleep(0.25)
            continue
        return 4
    else:
        return 4
    c_oid = iface_tags.order_id_from_create_response(mo2)
    if c_oid:
        iface_tags.append_journal(
            {
                "symbol": sym_u,
                "action": "close",
                "order_id": c_oid,
                "order_link_id": close_link_used,
                "open_tag": (pos_meta.get("open_tag") or "predict_loop"),
                "close_tag": "predict_loop_close",
                "exit_kind": "test_tag_verify",
                "open_order_id": pos_meta.get("open_order_id") or "",
                "open_detail": (pos_meta.get("open_detail") or detail)[:2000],
            }
        )
    iface_tags.clear_position_symbol(sym_u)

    close_sleep = max(0.0, env_float("PREDICT_LOOP_POST_CLOSE_SLEEP_SEC", 0.75))
    time.sleep(close_sleep)

    print(
        "SYGNIF_TEST_TAG_OK open+close journal written; check BTC Interface "
        f"(PREDICT tags; order_ids open={o_oid} close={c_oid or '?'})",
        flush=True,
    )
    return 0


def _env_truthy_predict(name: str, *, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _maybe_reload_runtime_hints(repo: Path, iter_count: int) -> None:
    if not _env_truthy_predict("SYGNIF_SWARM_RUNTIME_HINTS_RELOAD_EACH_ITER"):
        return
    try:
        every = max(1, int(env_int("SYGNIF_SWARM_RUNTIME_HINTS_RELOAD_EVERY_N", 1)))
    except (TypeError, ValueError):
        every = 1
    if iter_count % every != 0:
        return
    try:
        from swarm_improvement_runtime import apply_demo_runtime_hints_env
    except ImportError:
        from finance_agent.swarm_improvement_runtime import apply_demo_runtime_hints_env

    out = apply_demo_runtime_hints_env(repo)
    if out.get("applied"):
        keys = out.get("keys") or {}
        print(f"SYGNIF_LOOP_RUNTIME_HINTS_APPLIED {json.dumps(keys, sort_keys=True)}", flush=True)
    elif out.get("reason") not in (None, "SYGNIF_SWARM_RUNTIME_HINTS_APPLY_off", "no_hints_file"):
        print(f"SYGNIF_LOOP_RUNTIME_HINTS_SKIP {out.get('reason')}", flush=True)


def _inter_iteration_sleep_sec(*, fallback: float) -> float:
    raw = (os.environ.get("PREDICT_LOOP_INTERVAL_SEC") or "").strip()
    if raw != "":
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    raw2 = (os.environ.get("SYGNIF_SWARM_LOOP_INTERVAL_SEC") or "").strip()
    if raw2 != "":
        try:
            return max(0.0, float(raw2))
        except ValueError:
            pass
    return max(0.0, float(fallback))


def main() -> int:
    global _STOP
    try:
        from swarm_instance_paths import apply_swarm_instance_env  # noqa: PLC0415
    except ImportError:
        from finance_agent.swarm_instance_paths import apply_swarm_instance_env  # noqa: PLC0415

    apply_swarm_instance_env(_REPO)
    ap = argparse.ArgumentParser(description="Predict protocol loop: auto entries and exits (Bybit demo)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument(
        "--kline-limit",
        type=int,
        default=max(120, min(1000, int(os.environ.get("ASAP_KLINE_LIMIT", "320") or 320))),
    )
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--rf-trees", type=int, default=max(10, int(os.environ.get("ASAP_RF_TREES", "32") or 32)))
    ap.add_argument(
        "--xgb-estimators",
        type=int,
        default=max(20, int(os.environ.get("ASAP_XGB_N_ESTIMATORS", "60") or 60)),
    )
    ap.add_argument("--data-dir", type=Path, default=_DATA)
    ap.add_argument("--training-json", type=Path, default=_PA / "training_channel_output.json")
    ap.add_argument(
        "--write-json",
        default=str(_PA / "btc_prediction_output.json"),
        metavar="PATH",
    )
    ap.add_argument("--no-write-json", action="store_true")
    ap.add_argument(
        "--manual-qty",
        default=None,
        help="Fixed linear order qty in base coin (e.g. BTC for BTCUSDT). Mutually exclusive with --manual-notional-usdt.",
    )
    ap.add_argument(
        "--manual-notional-usdt",
        type=float,
        default=None,
        metavar="USDT",
        help="Target approximate USDT notional: qty ≈ USDT / last close from the live fit. Mutually exclusive with --manual-qty.",
    )
    ap.add_argument(
        "--manual-leverage",
        type=float,
        default=None,
        help="Override leverage; clamped 1..BYBIT_DEMO_MANUAL_LEVERAGE_MAX (default 100).",
    )
    ap.add_argument(
        "--position-idx",
        type=int,
        default=int(os.environ.get("BYBIT_DEMO_POSITION_IDX", "0") or 0),
    )
    ap.add_argument(
        "--interval-sec",
        type=float,
        default=float(os.environ.get("PREDICT_LOOP_INTERVAL_SEC", "0") or 0),
        help="Extra sleep after each iteration (default 0=seamless; env PREDICT_LOOP_INTERVAL_SEC)",
    )
    ap.add_argument(
        "--max-iterations",
        type=int,
        default=int(os.environ.get("PREDICT_LOOP_MAX_ITERATIONS", "0") or 0),
        help="0 = unlimited until signal",
    )
    ap.add_argument("--execute", action="store_true")
    ap.add_argument(
        "--exit-on-no-edge",
        action="store_true",
        help="Flatten when decide_side returns no-edge (disables hold-on-no-edge for this run)",
    )
    ap.add_argument(
        "--test-tag-order",
        action="store_true",
        help=(
            "One-shot: minimal Buy open + reduce-only Sell close on demo to verify iface tags "
            "(journal predict_loop; requires --execute + SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES; account flat)"
        ),
    )
    ap.add_argument(
        "--refresh-aligned-every",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Every N iterations, if position matches target, close+reopen same side (0=off). "
            "Default: env PREDICT_LOOP_REFRESH_ALIGNED_EVERY_N or 1."
        ),
    )
    args = ap.parse_args()
    if bool(getattr(args, "execute", False)):
        _siz_off = (os.environ.get("SYGNIF_PREDICT_EXECUTE_AUTO_SIZING_OFF") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if not _siz_off:
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
    if args.refresh_aligned_every is None:
        args.refresh_aligned_every = _default_refresh_aligned_every()
    else:
        args.refresh_aligned_every = max(0, int(args.refresh_aligned_every))

    if os.environ.get("SYGNIF_PREDICT_PROTOCOL_TEST_TAG_ORDER", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        args.test_tag_order = True

    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    training = _load_json(args.training_json)
    execute = bool(args.execute)
    hold_on_no_edge = _hold_on_no_edge_from_env() and not bool(args.exit_on_no_edge)

    if args.test_tag_order:
        if not execute:
            print("Refusing --test-tag-order without --execute", file=sys.stderr)
            return 2
        if os.environ.get("SYGNIF_PREDICT_PROTOCOL_LOOP_ACK", "").strip().upper() != "YES":
            print(
                "Refusing --test-tag-order: set SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES",
                file=sys.stderr,
            )
            return 2
        return run_test_tag_order(args)

    if execute and os.environ.get("SYGNIF_PREDICT_PROTOCOL_LOOP_ACK", "").strip().upper() != "YES":
        print(
            "Refusing --execute: set SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES",
            file=sys.stderr,
        )
        return 2

    interval = max(0.0, float(args.interval_sec))
    interval_fallback = interval
    max_iter = max(0, int(args.max_iterations))
    err_sleep = max(0.0, float(os.environ.get("PREDICT_LOOP_ERROR_SLEEP_SEC", "2") or 2))

    n = 0
    while not _STOP:
        n += 1
        _maybe_reload_runtime_hints(_REPO, n)
        interval = _inter_iteration_sleep_sec(fallback=interval_fallback)
        had_exc = False
        try:
            rc = _iteration(
                args=args,
                training=training,
                execute=execute,
                iter_count=n,
                hold_on_no_edge=hold_on_no_edge,
                refresh_aligned_every=args.refresh_aligned_every,
            )
        except Exception as exc:
            print(f"SYGNIF_LOOP_EXC {exc!r}", flush=True)
            rc = 9
            had_exc = True
        if rc >= 7:
            print(f"SYGNIF_LOOP_WARN iteration rc={rc}", flush=True)

        if _STOP:
            break
        if max_iter > 0 and n >= max_iter:
            break

        if interval > 0 and not _STOP:
            t0 = time.monotonic()
            while not _STOP:
                elapsed = time.monotonic() - t0
                if elapsed >= interval:
                    break
                rem = interval - elapsed
                time.sleep(min(1.0, rem))
        elif had_exc and err_sleep > 0 and not _STOP:
            time.sleep(err_sleep)

    print("SYGNIF_LOOP_STOP", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
