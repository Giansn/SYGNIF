#!/usr/bin/env python3
"""
CLI for ``prediction_agent/btc_governance`` (package must not share this filename — use ``run_*``).

Usage::
  PYTHONPATH="$HOME/SYGNIF:$HOME/SYGNIF/prediction_agent" python3 scripts/run_btc_governance.py delegate --write
  PYTHONPATH=... python3 scripts/run_btc_governance.py archive --dry-run
  PYTHONPATH=... python3 scripts/run_btc_governance.py embedder-probe
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bootstrap_path() -> None:
    pa = ROOT / "prediction_agent"
    sys.path.insert(0, str(pa))
    sys.path.insert(0, str(ROOT))


def main() -> int:
    _bootstrap_path()
    from btc_governance.archive import run_archive_pass
    from btc_governance.delegate import compute_governance_packet
    from btc_governance.delegate import write_governance_json
    from btc_governance.embedder_cli import probe_embedder_cli

    ap = argparse.ArgumentParser(description="BTC governance (swarm delegate + archive + embedder probe)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("delegate", help="Fuse swarm + R01 + training channel summary")
    d.add_argument("--print-json", action="store_true")
    d.add_argument("--write", action="store_true", help="Write prediction_agent/btc_governance_output.json")
    d.add_argument("--no-training", action="store_true")

    a = sub.add_parser("archive", help="Gzip stale JSONL/log files (see btc_governance/archive.py)")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--days", type=float, default=None)

    sub.add_parser("embedder-probe", help="If SYGNIF_EMBEDDER_CLI=1, run embedder --version/--help")
    args = ap.parse_args()

    if args.cmd == "delegate":
        pkt = compute_governance_packet(include_training_summary=not args.no_training)
        if args.print_json:
            print(json.dumps(pkt.to_dict(), indent=2))
        if args.write:
            p = write_governance_json()
            print(f"[btc_governance] wrote {p}", flush=True)
        if not args.print_json and not args.write:
            print(json.dumps(pkt.to_dict(), indent=2))
        return 0

    if args.cmd == "archive":
        lines = run_archive_pass(repo_root=ROOT, days=args.days, dry_run=args.dry_run)
        for ln in lines:
            print(ln, flush=True)
        if not lines:
            print("[archive] nothing matched (age/glob/dry-run)", flush=True)
        return 0

    if args.cmd == "embedder-probe":
        out = probe_embedder_cli()
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
