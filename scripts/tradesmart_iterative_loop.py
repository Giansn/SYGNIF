#!/usr/bin/env python3
"""
**TradeSmart (Noren) iterative loop** — OAuth session + fast open/flatten cadence + on-disk strategy state.

Uses the same OAuth flow as https://github.com/trade-smart/TradesmartApioAuth-py via ``NorenRestApiOAuth``
(see ``finance_agent/tradesmart_noren_client.py``).

Examples::

  cd ~/SYGNIF && . .venv/bin/activate
  pip install -r finance_agent/requirements-tradesmart.txt

  # Dry-run (no orders): log decisions only
  TRADESMART_ACCESS_TOKEN=... TRADESMART_UID=... TRADESMART_ACCOUNT_ID=... \\
    python3 scripts/tradesmart_iterative_loop.py --dry-run --max-iterations 5

  # Live (intraday market) — requires ACK
  TRADESMART_PREDICT_ORDER_ACK=YES \\
    python3 scripts/tradesmart_iterative_loop.py --execute --interval 5 --symbol INFY-EQ

Environment (REST):
- ``TRADESMART_NOREN_HOST`` / ``TRADESMART_NOREN_WEBSOCKET`` — broker endpoints (defaults match upstream ``api_helper``).
- ``TRADESMART_ACCESS_TOKEN``, ``TRADESMART_UID``, ``TRADESMART_ACCOUNT_ID`` **or** ``TRADESMART_CRED_YAML``.

Safety:
- Without ``--execute`` + ``TRADESMART_PREDICT_ORDER_ACK=YES``, the script refuses to send orders.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "finance_agent") not in sys.path:
    sys.path.insert(0, str(_REPO / "finance_agent"))


def _load_dotenv() -> None:
    for p in (_REPO / ".env", Path.home() / "SYGNIF" / ".env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            k = k.strip()
            if k and k not in os.environ:
                v = v.strip().strip('"').strip("'")
                os.environ[k] = v


def main() -> int:
    _load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    ap = argparse.ArgumentParser(description="TradeSmart Noren iterative trading loop")
    ap.add_argument("--exchange", default=os.environ.get("TRADESMART_EXCHANGE", "NSE"))
    ap.add_argument("--symbol", default=os.environ.get("TRADESMART_SYMBOL", "INFY-EQ"))
    ap.add_argument("--qty", type=int, default=int(os.environ.get("TRADESMART_QTY", "1")))
    ap.add_argument("--product", default=os.environ.get("TRADESMART_PRODUCT", "I"), help="Noren prd: I intraday, C delivery, …")
    ap.add_argument("--interval", type=float, default=float(os.environ.get("TRADESMART_INTERVAL", "5")), help="Base sleep (seconds)")
    ap.add_argument("--min-interval", type=float, default=2.0)
    ap.add_argument("--max-interval", type=float, default=60.0)
    ap.add_argument(
        "--state",
        default=os.environ.get("TRADESMART_STATE_JSON", str(_REPO / "prediction_agent/tradesmart_iter_state.json")),
    )
    ap.add_argument("--strategy", choices=("pulse", "alternate"), default="pulse")
    ap.add_argument("--dry-run", action="store_true", help="Never POST orders; log only")
    ap.add_argument(
        "--stub-positions",
        action="store_true",
        help="Use a flat stub for positions (offline dry-run; no OAuth / no REST)",
    )
    ap.add_argument("--execute", action="store_true", help="Actually place orders (needs ACK env)")
    ap.add_argument("--max-iterations", type=int, default=0, help="0 = run forever")
    args = ap.parse_args()

    from tradesmart_iterative_runner import (  # noqa: E402
        RunnerConfig,
        StubFlatPositionsApi,
        run_loop,
        strategy_from_name,
    )
    from tradesmart_noren_client import build_noren_api  # noqa: E402

    if args.dry_run:
        dry = True
    elif args.execute:
        ack = os.environ.get("TRADESMART_PREDICT_ORDER_ACK", "").strip().upper()
        if ack != "YES":
            print(
                "Refusing --execute: set TRADESMART_PREDICT_ORDER_ACK=YES to acknowledge live orders.",
                file=sys.stderr,
            )
            return 2
        dry = False
    else:
        dry = True

    cfg = RunnerConfig(
        exchange=str(args.exchange).strip(),
        tradingsymbol=str(args.symbol).strip().upper(),
        quantity=max(1, int(args.qty)),
        product_type=str(args.product).strip(),
        interval_sec=float(args.interval),
        min_interval_sec=float(args.min_interval),
        max_interval_sec=float(args.max_interval),
        state_path=Path(args.state).expanduser(),
        dry_run=dry,
    )

    strat = strategy_from_name(args.strategy)

    if args.stub_positions and not dry:
        print("--stub-positions is only valid with --dry-run (refusing).", file=sys.stderr)
        return 2

    def _factory():
        if args.stub_positions:
            return StubFlatPositionsApi()
        return build_noren_api()

    try:
        run_loop(
            api_factory=_factory,
            cfg=cfg,
            strategy=strat,
            max_iterations=int(args.max_iterations),
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logging.exception("tradesmart loop failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
