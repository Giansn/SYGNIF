#!/usr/bin/env python3
"""
**Swarm + predict-protocol auto trading (Bybit API demo)** — launcher for ``btc_predict_protocol_loop.py``.
Orders use ``https://api-demo.bybit.com`` + ``BYBIT_DEMO_*`` (mainnet-style BTCUSDT linear, **not** live ``api.bybit.com``).

Sets operator env so each loop iteration:

- runs ``compute_swarm()`` + ``write_fused_sidecar`` + ``swarm_fusion_allows`` (``SYGNIF_SWARM_GATE_LOOP=1``)
- requires **btc_future** branch in Swarm (``SYGNIF_SWARM_BTC_FUTURE=1`` or ``trade``) and fusion **bf** alignment
  (same as ``swarm_gated_predict_protocol_order.py``)
- **btc_future governance:** ``SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE=1`` + raw **bf** vote alignment
  (``SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE``); flat demo allowed if ``SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS=1``.
  Fusion ``vote_btc_future`` uses ``SWARM_ORDER_BTC_FUTURE_FLAT_PASS`` (defaults **1** here too; gate falls back to
  the vote-flat knob if fusion-specific var is unset).
- **Hivemind entries:** ``SWARM_ORDER_REQUIRE_HIVEMIND_VOTE=1`` (default here) — long needs **hm** ``>= 1``, short ``<= -1``;
  ``SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS=1`` allows **hm** ``0`` (quiet liveness). Set ``SWARM_ORDER_REQUIRE_HIVEMIND_VOTE=0`` to skip.
- **Nautilus research + fusion sidecar:** ``SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY=1`` (default) blocks a **long**
  when the Nautilus sidecar vote is **short** (and the converse). ``SYGNIF_PROTOCOL_FUSION_SYNC=1`` (default) keeps
  ``swarm_nautilus_protocol_sidecar.json`` refreshed each loop iteration (dry-run too). Tighter knobs: see
  ``finance_agent/swarm_order_gate.py`` (``SWARM_ORDER_NAUTILUS_MAX_AGE_MIN``, ``SWARM_ORDER_FUSION_REQUIRE_STRONG``,
  ``SWARM_ORDER_ML_LOGREG_MIN_CONF``, …).
- **Swarm mean band (Hivemind bias):** this launcher sets ``SWARM_ORDER_MIN_MEAN_LONG=-0.5`` and
  ``SWARM_ORDER_MAX_MEAN_SHORT=0.5`` when unset — more permissive for mixed TA/channel regimes. Tighten in ``.env``
  (e.g. ``-0.25`` / ``0.25``) for stricter Swarm veto. ``swarm_order_gate`` defaults to ``-0.25`` / ``0.25`` when no env
  is set (bare ``btc_predict_protocol_loop``).
- **~USDT TP/SL fallback** after open (when full Swarm TP/SL apply did not run): ``SYGNIF_SWARM_TP_USDT_TARGET`` /
  ``SYGNIF_SWARM_SL_USDT_TARGET`` default **600** / **360** USDT PnL targets (aligned with 120s entry cadence);
  ``SYGNIF_SWARM_TPSL_PROFILE=reward_risk`` in ``swarm_btc_future_tpsl_apply``.
  **Modeled-profit floor** for flat opens defaults to **9** USDT here (``SYGNIF_PREDICT_MIN_EDGE_PROFIT_USDT=9``);
  set ``0`` for more entries, or ``50``+ for a larger modeled edge. High RF/XGB **move %%** optionally relaxes the
  floor further (``SYGNIF_PREDICT_EDGE_VOL_RELAX`` + ``SYGNIF_PREDICT_EDGE_VOL_*`` in ``btc_asap_predict_core``).
- **Synthetic / 24h flat guard:** ``SYGNIF_PREDICT_BLOCK_SYNTHETIC_HOLD`` defaults **on** here — no **new** opens when
  ``btc_24h_movement_prediction.json`` synthesis is ``NEUTRAL`` and/or ``swarm_btc_synth.json`` is HOLD/FLAT.

**letscrash / BTC-0.1 first:** ``decide_side`` already applies **R01** bearish stack from
``letscrash/btc_strategy_0_1_rule_registry.json`` (via ``r01_registry_bridge``). The loop can **clip** USDT
notional to ``rule_proof_bucket.notional_cap_usdt`` when ``SYGNIF_LETSCRASH_NOTIONAL_CAP=1`` (default in launcher).

**Swarm portfolio / discretion (default here):** ``SWARM_PORTFOLIO_AUTHORITY=0`` — the loop **may** reduce-only
**close** (Bybit) when the model target flips to the opposite side even if Swarm would **block** opening that new leg
(stay **flat** until an eligible window passes ``swarm_fusion_allows``). Set ``SWARM_PORTFOLIO_AUTHORITY=1`` to
**hold** the existing venue leg when the opposite entry is gated (legacy: avoid closing into flat when reopen is blocked).
Opens still require ``swarm_fusion_allows`` when the gate path runs.

**Open as soon as prediction completes:** default ``SYGNIF_PREDICT_OPEN_IMMEDIATE=1`` injects ``--interval-sec 0``
(back-to-back predict → gate → venue). Use ``--paced`` or ``SYGNIF_PREDICT_OPEN_IMMEDIATE=0`` for ``--interval-sec 300``
(5m bar spacing).

**Calmer demo defaults (overridable in .env):** ``SYGNIF_SWARM_LOOP_INTERVAL_SEC`` — pause between loop iterations;
``PREDICT_LOOP_REFRESH_ALIGNED_EVERY_N=0`` disables same-side rip-and-replace; ``SYGNIF_PREDICT_MIN_DISCRETIONARY_CLOSE_SEC``
blocks discretionary closes for a wall period after each open (**0** = off, allow prompt theory reversals);
``SYGNIF_PREDICT_OPPOSITE_SIGNAL_CONFIRM_ITER`` requires that many consecutive opposite-target iterations before flip-close
(**0** = off); ``SWARM_BYBIT_ENTRY_COOLDOWN_SEC`` spaces re-entries.
(back-pressure vs ``--interval-sec 0``). ``PREDICT_LOOP_REFRESH_ALIGNED_EVERY_N`` — aligned ``close+reopen`` only
every **N** iters. ``SYGNIF_PREDICT_MIN_UPNL_TO_CLOSE_USDT`` — min unrealised **USDT** profit before refresh /
hold-until-profit allows closing. ``SYGNIF_PREDICT_MIN_EDGE_PROFIT_USDT`` — modeled edge floor on flat opens.
``SWARM_ORDER_ML_LOGREG_MIN_CONF`` — ML logreg confidence gate on fusion. ``SYGNIF_PREDICT_FAILURE_SWING_PANIC_REVERSE=0``
(default here) avoids Heavy91-driven **flip** when the model target is flat; enable in ``.env`` if you want that behaviour.

**Defaults injected** when you omit flags: ``--interval-sec`` from ``SYGNIF_SWARM_LOOP_INTERVAL_SEC`` or immediate/5m
logic, ``--manual-notional-usdt 100000``,
``--manual-leverage 50`` (50× linear). **Leverage / size** still follow prediction when you omit manual flags (loop uses
``leverage_from_move_pct`` + stake math from ``btc_asap_predict_core``).

**Eligible timeframe:** candle interval is **5m** for ``run_live_fit``. Use ``--eligible-scan`` for a quick
historical eligibility snapshot (subsampling), or ``scripts/predict_protocol_eligible_scan.py`` for a deeper scan.

**Safety:** still requires ``SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES`` and ``--execute`` on the loop.
This script does **not** remove human ACK — it only configures Swarm + sizing defaults.

**Freqtrade-inspired loop mechanics (Bybit only):** optional entry cooldown + consecutive open-fail cap in
``finance_agent/swarm_bybit_ft_mechanics.py``, wired into ``btc_predict_protocol_loop`` (env ``SWARM_BYBIT_*`` —
see ``swarm_operator.env.example``). No Freqtrade containers or APIs.

**Structured dataset (JSONL):** each loop iteration appends one NDJSON row to
``prediction_agent/swarm_predict_protocol_dataset.jsonl`` (ML + Swarm + fusion + venue snapshot; see
``SYGNIF_PREDICT_PROTOCOL_DATASET*`` in ``btc_predict_protocol_loop.py``). This launcher sets
``SYGNIF_PREDICT_PROTOCOL_DATASET=1`` by default; override path with ``SYGNIF_PREDICT_PROTOCOL_DATASET_JSONL`` or
disable with ``SYGNIF_PREDICT_PROTOCOL_DATASET=0``.

**Operator bundle:** ``SYGNIF_SWARM_ACTIVATE_IMPROVEMENTS=1`` (in ``swarm_operator.env``) applies ``setdefault``s for
live **Hivemind fusion** in ``fit_predict_live`` (``SYGNIF_PREDICT_HIVEMIND_FUSION``), **closed PnL** telemetry,
**strategy guideline** gate, and optional **Truthcoin CLI** discovery — see ``finance_agent/swarm_activate_bundle.py``.

**Auto-improvement → demo tuning:** when ``scripts/swarm_auto_improvement_flow.py`` runs with
``SYGNIF_SWARM_IMPROVEMENT_AUTO_DEMO_TUNING=1``, it may write ``prediction_agent/swarm_demo_runtime_hints.json``.
This launcher calls ``apply_demo_runtime_hints_env`` **after** default ``setdefault`` calls when
``SYGNIF_SWARM_RUNTIME_HINTS_APPLY=1`` so whitelist keys (loop interval, entry cooldown, paced opens) can override
calmer-demo defaults — see ``finance_agent/swarm_improvement_runtime.py``.

**Instance / multi-repo:** dotenv + optional ``PYTHONPATH`` for sibling projects under ``$HOME`` are handled by
``finance_agent/swarm_instance_paths.py`` (``SYGNIF_SWARM_LINK_INSTANCE``, ``SYGNIF_INSTANCE_ROOTS``,
``SYGNIF_INSTANCE_ROOTS_SCAN``, ``SYGNIF_SWARM_EXTEND_PYTHONPATH``). See that module for precedence.
This launcher **defaults** ``SYGNIF_INSTANCE_ROOTS_SCAN=1``, ``SYGNIF_SWARM_EXTEND_PYTHONPATH=1``,
``SYGNIF_INSTANCE_ROOTS_EXCLUDE=logs:intel``, ``SYGNIF_SWARM_TRUTHCOIN_DC=1``,
``SYGNIF_SWARM_CORE_ENGINE=hivemind``, ``SYGNIF_SWARM_HIVEMIND_VOTE=1``, ``SYGNIF_SWARM_FULL_ROOT_ACCESS=1``
(Truthcoin Hivemind drives ``swarm_mean`` when the node is up; see ``finance_agent/truthcoin_hivemind_swarm_core.py``);
override in ``.env`` / ``swarm_operator.env``.

Examples::

  SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES \\
    python3 scripts/swarm_auto_predict_protocol_loop.py --execute

  python3 scripts/swarm_auto_predict_protocol_loop.py --eligible-scan

  SYGNIF_SWARM_TP_USDT_TARGET=800 python3 scripts/swarm_auto_predict_protocol_loop.py --execute \\
    --manual-notional-usdt 100000 --manual-leverage 50 --interval-sec 300

  # 5m pacing instead of immediate:
  python3 scripts/swarm_auto_predict_protocol_loop.py --paced --execute

  # Smaller demo risk (notional / leverage / max qty / TP-SL bundle):
  python3 scripts/swarm_auto_predict_protocol_loop.py --risk-profile demo_safe --execute
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "finance_agent"))
from swarm_activate_bundle import (  # noqa: E402
    apply_swarm_activate_improvements_defaults,
)
from swarm_instance_paths import (  # noqa: E402
    apply_swarm_instance_env,
    subprocess_env_with_instance_pythonpath,
)
from swarm_risk_profile import (  # noqa: E402
    apply_swarm_risk_profile,
    resolve_effective_risk_profile,
)


def _repo() -> Path:
    return _REPO


def _predict_open_immediate() -> bool:
    """Default **immediate** (interval 0) unless ``SYGNIF_PREDICT_OPEN_IMMEDIATE=0``."""
    raw = (os.environ.get("SYGNIF_PREDICT_OPEN_IMMEDIATE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _merge_loop_defaults(rest: list[str]) -> list[str]:
    """Prepend pacing + sizing defaults unless already present."""
    joined = " ".join(rest)
    out = list(rest)
    if "--interval-sec" not in joined:
        calm = (os.environ.get("SYGNIF_SWARM_LOOP_INTERVAL_SEC") or "").strip()
        if calm:
            iv = calm
        else:
            iv = "0" if _predict_open_immediate() else "300"
        out = ["--interval-sec", iv] + out
    notional = (os.environ.get("SYGNIF_PREDICT_DEFAULT_NOTIONAL_USDT") or "100000").strip() or "100000"
    leverage = (os.environ.get("SYGNIF_PREDICT_DEFAULT_MANUAL_LEVERAGE") or "50").strip() or "50"
    if "--manual-notional-usdt" not in joined:
        out = ["--manual-notional-usdt", notional] + out
    if "--manual-leverage" not in joined:
        out = ["--manual-leverage", leverage] + out
    return out


def load_swarm_demo_env(*, extra_env_file: Path | None = None) -> None:
    apply_swarm_instance_env(_repo(), extra_env_file=extra_env_file)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Launch predict-protocol loop with Swarm gate + btc_future fusion defaults",
    )
    ap.add_argument(
        "--env-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="Optional .env loaded last (overrides); use for BYBIT_DEMO_* keys",
    )
    ap.add_argument(
        "--no-tp-target",
        action="store_true",
        help="Do not default SYGNIF_SWARM_TP_USDT_TARGET / SL / TPSL_PROFILE (600/360/reward_risk)",
    )
    ap.add_argument(
        "--eligible-scan",
        action="store_true",
        help="Run a fast predict_protocol_eligible_scan (5m / window=5) and exit",
    )
    ap.add_argument(
        "--paced",
        action="store_true",
        help="5m pacing: set SYGNIF_PREDICT_OPEN_IMMEDIATE=0 so loop uses --interval-sec 300",
    )
    ap.add_argument(
        "--risk-profile",
        default=None,
        metavar="NAME",
        help=(
            "Risk bundle after launcher defaults: default | demo_safe. "
            "Also SYGNIF_SWARM_RISK_PROFILE (CLI wins). demo_safe: lower notional/leverage, tighter max qty, calmer cadence."
        ),
    )
    args, rest = ap.parse_known_args()

    load_swarm_demo_env(extra_env_file=args.env_file)
    _impr_keys = apply_swarm_activate_improvements_defaults()
    if _impr_keys:
        print(f"[swarm-auto] activate_improvements keys_set={','.join(_impr_keys)}", flush=True)
    # Venue orders: **Bybit API demo** only (not ``api.bybit.com``), even if .env had hedge mainnet flags.
    os.environ["OVERSEER_BYBIT_HEDGE_MAINNET"] = "0"
    print(
        "[swarm-auto] SYGNIF_ORDER_REST_BASE=https://api-demo.bybit.com "
        "(API demo / mainnet-mirrored USDT linear; not api.bybit.com)",
        flush=True,
    )
    # Multi-repo + Truthcoin context (override in .env / swarm_operator.env if undesired)
    os.environ.setdefault("SYGNIF_INSTANCE_ROOTS_SCAN", "1")
    os.environ.setdefault("SYGNIF_SWARM_EXTEND_PYTHONPATH", "1")
    os.environ.setdefault("SYGNIF_INSTANCE_ROOTS_EXCLUDE", "logs:intel")
    os.environ.setdefault("SYGNIF_SWARM_TRUTHCOIN_DC", "1")
    os.environ.setdefault("SYGNIF_SWARM_CORE_ENGINE", "hivemind")
    os.environ.setdefault("SYGNIF_SWARM_HIVEMIND_VOTE", "1")
    os.environ.setdefault("SYGNIF_SWARM_FULL_ROOT_ACCESS", "1")

    if args.paced:
        os.environ["SYGNIF_PREDICT_OPEN_IMMEDIATE"] = "0"

    os.environ.setdefault("SYGNIF_PREDICT_OPEN_IMMEDIATE", "1")
    # Calmer venue cadence: breathing room between predict→gate→REST passes (override in .env).
    os.environ.setdefault("SYGNIF_SWARM_LOOP_INTERVAL_SEC", "60")
    # Disable same-side close+reopen (major source of churn); use >0 only if you need periodic iface refresh.
    os.environ.setdefault("PREDICT_LOOP_REFRESH_ALIGNED_EVERY_N", "0")
    # Hold / refresh until unrealised profit clears noise + fees (was ~1 USDT via per_trade_fee default).
    os.environ.setdefault("SYGNIF_PREDICT_MIN_UPNL_TO_CLOSE_USDT", "40")
    # Flat opens: modest modeled edge (USDT) before new risk (single assignment — avoid later overwrite to 0).
    os.environ.setdefault("SYGNIF_PREDICT_MIN_EDGE_PROFIT_USDT", "9")
    # Discretionary closes (opposite / refresh / no-edge): 0 = allow immediate reduce-only flip when model reverses.
    os.environ.setdefault("SYGNIF_PREDICT_MIN_DISCRETIONARY_CLOSE_SEC", "0")
    # Consecutive opposite-target iterations before flip-close (0 = off — reverse as soon as target flips).
    os.environ.setdefault("SYGNIF_PREDICT_OPPOSITE_SIGNAL_CONFIRM_ITER", "0")
    # Minimum seconds between successful market opens (flat→open) per symbol.
    os.environ.setdefault("SWARM_BYBIT_ENTRY_COOLDOWN_SEC", "120")
    # Fusion gate: minimum direction_logistic confidence (0–100) on embedded ML JSON.
    os.environ.setdefault("SWARM_ORDER_ML_LOGREG_MIN_CONF", "59")
    os.environ.setdefault("SYGNIF_LETSCRASH_NOTIONAL_CAP", "1")
    os.environ.setdefault("SYGNIF_SWARM_AUTO_TRADING", "1")
    os.environ.setdefault("SYGNIF_SWARM_GATE_LOOP", "1")
    os.environ.setdefault("SWARM_PORTFOLIO_AUTHORITY", "0")
    os.environ.setdefault("SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL", "1")
    os.environ.setdefault("SYGNIF_SWARM_BTC_FUTURE", "1")
    os.environ.setdefault("SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE", "1")
    os.environ.setdefault("SWARM_ORDER_REQUIRE_BTC_FUTURE", "1")
    os.environ.setdefault("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "1")
    os.environ.setdefault("SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE", "1")
    os.environ.setdefault("SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE", "1")
    os.environ.setdefault("SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS", "1")
    os.environ.setdefault("SWARM_ORDER_BTC_FUTURE_FLAT_PASS", "1")
    os.environ.setdefault("SYGNIF_FUSION_BTC_FUTURE_ADAPT_WHEN_FLAT", "1")
    os.environ.setdefault("SWARM_ORDER_REQUIRE_HIVEMIND_VOTE", "1")
    os.environ.setdefault("SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS", "1")
    os.environ.setdefault("SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY", "1")
    os.environ.setdefault("SWARM_ORDER_BLOCK_CONFLICT", "0")
    os.environ.setdefault("SWARM_ORDER_MIN_MEAN_LONG", "-0.5")
    os.environ.setdefault("SWARM_ORDER_MAX_MEAN_SHORT", "0.5")
    os.environ.setdefault("SYGNIF_PREDICT_PER_TRADE_COST_USDT", "1")
    os.environ.setdefault("SYGNIF_PREDICT_EDGE_PLUS_FEE", "1")
    # One-shot trailing after uPnL clears min (USDT notional step / qty → price distance); 0=off.
    os.environ.setdefault("SYGNIF_PREDICT_TRAIL_MOVE_USDT", "55")
    os.environ.setdefault("SYGNIF_PREDICT_HOLD_UNTIL_PROFIT", "0")
    os.environ.setdefault("SYGNIF_PREDICT_ENSURE_HEDGE_MODE", "0")
    os.environ.setdefault("BYBIT_DEMO_ORDER_MAX_QTY", "2.0")
    os.environ.setdefault("SYGNIF_PREDICT_DEFAULT_NOTIONAL_USDT", "100000")
    os.environ.setdefault("SYGNIF_PREDICT_DEFAULT_MANUAL_LEVERAGE", "50")
    os.environ.setdefault("SYGNIF_PREDICT_FAILURE_SWING_HEAVY91_ENTRIES", "1")
    os.environ.setdefault("SYGNIF_PREDICT_FAILURE_SWING_PANIC_REVERSE", "0")
    os.environ.setdefault("SYGNIF_PREDICT_BLOCK_SYNTHETIC_HOLD", "1")
    os.environ.setdefault("SYGNIF_PREDICT_SWING_FAILURE_ENTRIES", "1")
    os.environ.setdefault("SYGNIF_PROTOCOL_FUSION_SYNC", "1")
    os.environ.setdefault("SYGNIF_PREDICT_PROTOCOL_DATASET", "1")
    if not args.no_tp_target:
        os.environ.setdefault("SYGNIF_SWARM_TPSL_PROFILE", "reward_risk")
        os.environ.setdefault("SYGNIF_SWARM_TP_USDT_TARGET", "600")
        os.environ.setdefault("SYGNIF_SWARM_SL_USDT_TARGET", "360")

    try:
        eff_profile = resolve_effective_risk_profile(args.risk_profile)
    except ValueError as exc:
        print(f"[swarm-auto] WARN invalid risk profile: {exc}; using default", flush=True)
        eff_profile = "default"
    applied = apply_swarm_risk_profile(eff_profile)
    print(f"[swarm-auto] risk_profile={eff_profile} (overrides_applied={len(applied)})", flush=True)

    try:
        from swarm_improvement_runtime import apply_demo_runtime_hints_env  # noqa: PLC0415

        aph = apply_demo_runtime_hints_env(_repo())
        if aph.get("applied"):
            print(f"[swarm-auto] runtime_hints applied keys={aph.get('keys')}", flush=True)
        else:
            reason = str(aph.get("reason") or "")
            if reason == "SYGNIF_SWARM_RUNTIME_HINTS_APPLY_off":
                pass
            elif reason == "hints_expired":
                print(
                    "[swarm-auto] WARN runtime_hints skipped: hints_expired "
                    f"expires_utc={aph.get('expires_utc')!r} "
                    "(regenerate: scripts/swarm_auto_improvement_flow.py; or raise SYGNIF_SWARM_RUNTIME_HINTS_TTL_HOURS)",
                    flush=True,
                )
            elif aph.get("ok") is False:
                print(f"[swarm-auto] WARN runtime_hints skipped: {reason}", flush=True)
            elif reason in ("no_hints_file", "no_env_apply"):
                print(f"[swarm-auto] WARN runtime_hints skipped: {reason}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[swarm-auto] WARN runtime_hints exception: {exc}", flush=True)

    if args.eligible_scan:
        scan = _repo() / "scripts" / "predict_protocol_eligible_scan.py"
        notion = (os.environ.get("SYGNIF_PREDICT_DEFAULT_NOTIONAL_USDT") or "100000").strip() or "100000"
        lev = (os.environ.get("SYGNIF_PREDICT_DEFAULT_MANUAL_LEVERAGE") or "50").strip() or "50"
        argv_scan = [
            sys.executable,
            str(scan),
            "--kline-limit",
            "320",
            "--step",
            "20",
            "--window",
            "5",
            "--manual-notional-usdt",
            notion,
            "--manual-leverage",
            lev,
        ]
        print(
            "[swarm-auto] eligible timeframe: protocol uses 5m klines; interval-sec 300 aligns loop with new bar.",
            flush=True,
        )
        print(f"[swarm-auto] exec {' '.join(argv_scan)}", flush=True)
        return int(
            subprocess.run(
                argv_scan,
                cwd=str(_repo()),
                env=subprocess_env_with_instance_pythonpath(_repo()),
            ).returncode
        )

    loop = _repo() / "scripts" / "btc_predict_protocol_loop.py"
    rest_merged = _merge_loop_defaults(rest)
    argv = [sys.executable, str(loop), *rest_merged]
    print(f"[swarm-auto] exec {' '.join(argv)}", flush=True)
    r = subprocess.run(
        argv,
        cwd=str(_repo()),
        env=subprocess_env_with_instance_pythonpath(_repo()),
    )
    return int(r.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
