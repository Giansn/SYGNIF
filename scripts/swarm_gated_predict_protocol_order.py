#!/usr/bin/env python3
"""
**One-shot (or gated dry-run):** predict-protocol **5m live fit** + **Swarm** (incl. **btc_future**)
+ **Nautilus/ML fusion** sidecar, then optional **Bybit demo trading** market order (REST).

**Bybit API demo (mainnet-style USDT linear, not ``api.bybit.com``):** set ``BYBIT_DEMO_API_KEY`` and
``BYBIT_DEMO_API_SECRET`` in the
environment — typically from ``~/SYGNIF/.env``, ``~/SYGNIF/swarm_operator.env``, ``SYGNIF_SECRETS_ENV_FILE``,
and optional sibling ``.env`` files under ``$HOME`` (see ``finance_agent/swarm_instance_paths.py``:
``SYGNIF_SWARM_LINK_INSTANCE``, ``SYGNIF_INSTANCE_ROOTS``, ``SYGNIF_INSTANCE_ROOTS_SCAN``). Use ``--env-file PATH``
to load a file **last** (overrides). Swarm **bf** vote and venue orders both use these keys; host is
``api-demo.bybit.com`` via ``trade_overseer/bybit_linear_hedge.py`` unless
``OVERSEER_BYBIT_HEDGE_MAINNET=YES`` + ``OVERSEER_HEDGE_LIVE_OK=YES`` (not recommended for this script).

Flow
1. ``run_live_fit`` → ``btc_prediction_output.json`` (same stack as ``btc_predict_protocol_loop``).
2. ``compute_swarm()`` → ``swarm_knowledge_output.json`` (ML/ch/sc/ta + optional **bf**).
3. ``write_fused_sidecar`` → ``swarm_nautilus_protocol_sidecar.json`` (Nautilus + ML + **btc_future** vote).
4. **Gates:** ``finance_agent/swarm_order_gate.py`` — Swarm mean / conflict / ``btc_future.ok``; fusion label;
   **Nautilus research** non-contradiction (``SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY``); optional **Hivemind** **hm**
   vote alignment (``SWARM_ORDER_REQUIRE_HIVEMIND_VOTE=1``). Further sidecar / ML confidence knobs are documented in
   ``swarm_order_gate.py``.
5. If ``--execute`` + ``SYGNIF_SWARM_PREDICT_ORDER_ACK=YES`` (or ``SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES``):
   one ``btc_predict_protocol_loop._iteration`` (opens **Swarm-gated** position on **Bybit demo**).

**Eligible timeframe:** **5m** klines via ``run_live_fit``; historical scan: ``scripts/predict_protocol_eligible_scan.py``.

**Defaults:** ``--manual-notional-usdt 100000`` ``--manual-leverage 50``.

**Continuous auto trading (loop):** ``scripts/swarm_auto_predict_protocol_loop.py`` sets ``SYGNIF_SWARM_GATE_LOOP=1``,
``SYGNIF_SWARM_BTC_FUTURE=1`` (demo **bf**) or ``SYGNIF_SWARM_BTC_FUTURE=trade`` (mainnet **bf**), fusion alignment, and optional ``SYGNIF_SWARM_TP_USDT_TARGET=50`` — then runs
``btc_predict_protocol_loop.py`` (still requires ACK + ``--execute``).

**Defaults in this script** (after loading dotenv): ``SYGNIF_INSTANCE_ROOTS_SCAN=1``,
``SYGNIF_SWARM_EXTEND_PYTHONPATH=1``, ``SYGNIF_INSTANCE_ROOTS_EXCLUDE=logs:intel``, ``SYGNIF_SWARM_TRUTHCOIN_DC=1``,
``SYGNIF_SWARM_CORE_ENGINE=hivemind``, ``SYGNIF_SWARM_HIVEMIND_VOTE=1``, ``SYGNIF_SWARM_FULL_ROOT_ACCESS=1``.
Unset in ``swarm_operator.env`` if you do not want them.

**Safety:** Demo API by default. No ACK → dry-run only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PA = _REPO / "prediction_agent"
_DATA = _REPO / "finance_agent" / "btc_specialist" / "data"

sys.path.insert(0, str(_PA))
sys.path.insert(0, str(_REPO / "trade_overseer"))
sys.path.insert(0, str(_REPO / "finance_agent"))

from swarm_instance_paths import apply_swarm_instance_env, append_instance_roots_to_syspath  # noqa: E402
from swarm_order_gate import swarm_fusion_allows  # noqa: E402


def _force_api_demo_trading_host() -> None:
    """Swarm gated stack: venue orders always **api-demo** (``OVERSEER_BYBIT_HEDGE_MAINNET=0``)."""
    os.environ["OVERSEER_BYBIT_HEDGE_MAINNET"] = "0"


def load_swarm_demo_env(*, extra_env_file: Path | None = None) -> None:
    """
    Load secrets for demo workflow (see ``finance_agent/swarm_instance_paths.apply_swarm_instance_env``),
    then optionally append sibling roots to ``sys.path`` when ``SYGNIF_SWARM_EXTEND_PYTHONPATH=1``.
    """
    apply_swarm_instance_env(_REPO, extra_env_file=extra_env_file)
    append_instance_roots_to_syspath(_REPO)


def _mask_key_id(s: str) -> str:
    """Do not print any key material — length only."""
    s = (s or "").strip()
    if not s:
        return "unset"
    return f"set({len(s)} chars)"


def _demo_venue_line() -> str:
    try:
        import bybit_linear_hedge as blh  # noqa: PLC0415

        base = blh.signed_trading_rest_base()
    except Exception:
        base = "https://api-demo.bybit.com"
    return f"orders_rest={base} creds=BYBIT_DEMO_API_KEY (API demo / mainnet-mirrored linear)"


def _print_demo_credentials_status() -> None:
    """Log masked key id only; never print secrets."""
    k = os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
    sec = os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
    print(
        f"SYGNIF_SWARM_DEMO_KEYS key_id={_mask_key_id(k)} secret_len={len(sec)} {_demo_venue_line()}",
        flush=True,
    )


def _require_demo_keys_for_execute() -> bool:
    k = os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
    sec = os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
    if len(k) >= 8 and len(sec) >= 8:
        return True
    print(
        "SYGNIF_SWARM_ERR missing BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET — "
        "add them to .env or pass --env-file (see docker-compose env_file order).",
        file=sys.stderr,
    )
    return False


def _load_loop_module():
    path = _REPO / "scripts" / "btc_predict_protocol_loop.py"
    spec = importlib.util.spec_from_file_location("btc_predict_protocol_loop", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load btc_predict_protocol_loop")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_swarm_json(doc: dict) -> Path:
    dest = _PA / "swarm_knowledge_output.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Swarm+btc_future+Nautilus fusion gate → one predict-protocol demo order",
    )
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
    ap.add_argument("--write-json", default=str(_PA / "btc_prediction_output.json"), metavar="PATH")
    ap.add_argument("--manual-notional-usdt", type=float, default=100_000.0, metavar="USDT")
    ap.add_argument("--manual-leverage", type=float, default=50.0, help="Clamped to BYBIT_DEMO_MANUAL_LEVERAGE_MAX")
    ap.add_argument("--position-idx", type=int, default=int(os.environ.get("BYBIT_DEMO_POSITION_IDX", "0") or 0))
    ap.add_argument("--no-fusion-sync", action="store_true", help="Skip write_fused_sidecar after predict")
    ap.add_argument(
        "--no-fusion-align",
        action="store_true",
        help="Disable fusion gates (SWARM_ORDER_REQUIRE_FUSION_ALIGN=0)",
    )
    ap.add_argument(
        "--env-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="Optional extra .env loaded last (overrides); use for demo keys if not in repo .env",
    )
    ap.add_argument("--execute", action="store_true")
    ap.add_argument(
        "--hivemind-off",
        action="store_true",
        help="Disable SWARM_ORDER_REQUIRE_HIVEMIND_VOTE (gate entries on Swarm mean + fusion + btc_future only)",
    )
    ap.add_argument(
        "--auto-trading",
        action="store_true",
        help="Set SYGNIF_SWARM_AUTO_TRADING=1 (marker) and print swarm-auto loop hint",
    )
    args = ap.parse_args()

    load_swarm_demo_env(extra_env_file=args.env_file)
    _force_api_demo_trading_host()
    print(
        "SYGNIF_ORDER_REST_BASE=https://api-demo.bybit.com "
        "(API demo / mainnet-mirrored USDT linear; OVERSEER_BYBIT_HEDGE_MAINNET=0)",
        flush=True,
    )
    os.environ.setdefault("SYGNIF_INSTANCE_ROOTS_SCAN", "1")
    os.environ.setdefault("SYGNIF_SWARM_EXTEND_PYTHONPATH", "1")
    os.environ.setdefault("SYGNIF_INSTANCE_ROOTS_EXCLUDE", "logs:intel")
    os.environ.setdefault("SYGNIF_SWARM_TRUTHCOIN_DC", "1")
    os.environ.setdefault("SYGNIF_SWARM_CORE_ENGINE", "hivemind")
    os.environ.setdefault("SYGNIF_SWARM_HIVEMIND_VOTE", "1")
    os.environ.setdefault("SYGNIF_SWARM_FULL_ROOT_ACCESS", "1")

    if args.auto_trading:
        os.environ["SYGNIF_SWARM_AUTO_TRADING"] = "1"
        print(
            "SYGNIF_SWARM_AUTO_TRADING=1 — for continuous venue loop with Swarm+bf gates use:\n"
            "  SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES python3 scripts/swarm_auto_predict_protocol_loop.py "
            "--execute --manual-notional-usdt 100000 --manual-leverage 50",
            flush=True,
        )

    os.environ.setdefault("SYGNIF_SWARM_BTC_FUTURE", "1")
    # Fusion aligner: sum-label (Nautilus+ML+btc_future) + **btc_future** demo vote
    os.environ.setdefault("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "1")
    os.environ.setdefault("SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE", "1")
    os.environ.setdefault("SWARM_ORDER_BTC_FUTURE_FLAT_PASS", "1")
    os.environ.setdefault("SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS", "1")
    os.environ.setdefault("SWARM_ORDER_MIN_MEAN_LONG", "-0.25")
    os.environ.setdefault("SWARM_ORDER_MAX_MEAN_SHORT", "0.25")
    os.environ.setdefault("SWARM_ORDER_REQUIRE_HIVEMIND_VOTE", "1")
    os.environ.setdefault("SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS", "1")
    # Nautilus research sidecar: veto entries against explicit sidecar bias (fusion ``vote_nautilus``).
    os.environ.setdefault("SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY", "1")
    if getattr(args, "hivemind_off", False):
        os.environ["SWARM_ORDER_REQUIRE_HIVEMIND_VOTE"] = "0"
    if getattr(args, "no_fusion_align", False):
        os.environ["SWARM_ORDER_REQUIRE_FUSION_ALIGN"] = "0"

    _print_demo_credentials_status()
    if args.execute and not _require_demo_keys_for_execute():
        return 2

    loop = _load_loop_module()
    training = loop._load_json(args.training_json)

    wpath = str(args.write_json).strip()
    _allow_buy, _enhanced, out, pred_ms = loop.run_live_fit(
        symbol=args.symbol,
        kline_limit=args.kline_limit,
        window=args.window,
        data_dir=str(args.data_dir),
        rf_trees=args.rf_trees,
        xgb_estimators=args.xgb_estimators,
        write_json_path=wpath,
    )
    target, why = loop.decide_side(out, training)
    print(
        f"SYGNIF_SWARM_GATE_PREDICT target={target!r} why={why!r} predict_ms={pred_ms:.1f}",
        flush=True,
    )

    try:
        from swarm_knowledge import compute_swarm  # noqa: PLC0415
    except ImportError:
        from finance_agent.swarm_knowledge import compute_swarm  # noqa: PLC0415

    swarm = compute_swarm()
    sp = _write_swarm_json(swarm)
    print(f"SYGNIF_SWARM_GATE wrote {sp}", flush=True)
    _bf = swarm.get("btc_future") if isinstance(swarm.get("btc_future"), dict) else {}
    _src = swarm.get("sources") if isinstance(swarm.get("sources"), dict) else {}
    _bfs = _src.get("bf") if isinstance(_src.get("bf"), dict) else {}
    print(
        "SYGNIF_SWARM_BF "
        + json.dumps(
            {
                "bf_vote": _bfs.get("vote"),
                "bf_detail": _bfs.get("detail"),
                "btc_future_enabled": _bf.get("enabled"),
                "btc_future_ok": _bf.get("ok"),
                "btc_future_profile": _bf.get("profile"),
            },
            default=str,
        ),
        flush=True,
    )

    fusion_doc = None
    if not args.no_fusion_sync:
        try:
            from nautilus_protocol_fusion import write_fused_sidecar  # noqa: PLC0415
        except ImportError:
            sys.path.insert(0, str(_PA))
            from nautilus_protocol_fusion import write_fused_sidecar  # noqa: PLC0415

        fusion_doc = write_fused_sidecar(_REPO, btc_prediction_override=out)
        print(
            "SYGNIF_SWARM_GATE fusion "
            + json.dumps((fusion_doc.get("fusion") or {}), default=str),
            flush=True,
        )

    ok, reason = swarm_fusion_allows(target=target, swarm=swarm, fusion_doc=fusion_doc, predict_out=out)
    print(f"SYGNIF_SWARM_GATE decision ok={ok} reason={reason}", flush=True)
    if not ok:
        return 3

    ack = os.environ.get("SYGNIF_SWARM_PREDICT_ORDER_ACK", "").strip().upper() == "YES" or os.environ.get(
        "SYGNIF_PREDICT_PROTOCOL_LOOP_ACK", ""
    ).strip().upper() == "YES"
    if args.execute and not ack:
        print(
            "Refusing --execute: set SYGNIF_SWARM_PREDICT_ORDER_ACK=YES "
            "or SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES",
            file=sys.stderr,
        )
        return 2

    ns = argparse.Namespace(
        symbol=args.symbol,
        kline_limit=args.kline_limit,
        window=args.window,
        data_dir=args.data_dir,
        rf_trees=args.rf_trees,
        xgb_estimators=args.xgb_estimators,
        write_json=args.write_json,
        no_write_json=False,
        manual_qty=None,
        manual_notional_usdt=float(args.manual_notional_usdt),
        manual_leverage=float(args.manual_leverage),
        position_idx=args.position_idx,
    )

    if not args.execute:
        print(
            "SYGNIF_SWARM_GATE dry-run only (add --execute + ACK to send venue order)",
            flush=True,
        )
        return 0

    rc = loop._iteration(
        args=ns,
        training=training,
        execute=True,
        iter_count=1,
        hold_on_no_edge=loop._hold_on_no_edge_from_env(),
        refresh_aligned_every=0,
    )
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
