#!/usr/bin/env python3
"""
**Swarm + open linear P/L (read-only)** — one-shot snapshot.

Calls ``finance_agent.swarm_knowledge.compute_swarm()`` and prints **unrealised** USDT P/L from
``bybit_open_pnl`` (same path as ``SYGNIF_SWARM_BYBIT_OPEN_PNL`` / ``btc_future.position`` — needs
``SYGNIF_SWARM_BTC_FUTURE`` demo or trade for the **bf** venue row).

Loads env: ``<repo>/.env``, ``~/xrp_claude_bot/.env`` (override), ``SYGNIF_SECRETS_ENV_FILE``.

Examples::

  cd ~/SYGNIF && PYTHONPATH=finance_agent:prediction_agent python3 scripts/swarm_monitor_open_pnl.py
  python3 scripts/swarm_monitor_open_pnl.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def load_env(path: Path, *, override: bool = False) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        if "=" not in s:
            continue
        k, _, rest = s.partition("=")
        k = k.strip()
        if not k:
            continue
        v = rest.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if override or k not in os.environ:
            os.environ[k] = v


def _load_standard_env() -> None:
    repo = _repo()
    load_env(repo / ".env", override=False)
    load_env(Path.home() / "xrp_claude_bot" / ".env", override=True)
    extra = (os.environ.get("SYGNIF_SECRETS_ENV_FILE") or "").strip()
    if extra:
        load_env(Path(extra).expanduser(), override=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Swarm snapshot: unrealised P/L on open Bybit linear legs")
    ap.add_argument("--json", action="store_true", help="Print full compute_swarm() JSON")
    args = ap.parse_args()

    _load_standard_env()
    repo = _repo()
    pa = repo / "prediction_agent"
    for p in (str(repo / "finance_agent"), str(pa), str(repo / "trade_overseer")):
        if p not in sys.path:
            sys.path.insert(0, p)

    try:
        from swarm_knowledge import briefing_line_swarm  # noqa: PLC0415
        from swarm_knowledge import compute_swarm  # noqa: PLC0415
    except ImportError:
        from finance_agent.swarm_knowledge import briefing_line_swarm  # noqa: PLC0415
        from finance_agent.swarm_knowledge import compute_swarm  # noqa: PLC0415

    sk = compute_swarm()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.json:
        print(json.dumps(sk, indent=2, default=str))
        return 0

    mean = sk.get("swarm_mean")
    lab = sk.get("swarm_label")
    cf = sk.get("swarm_conflict")
    print(f"SYGNIF_SWARM_MONITOR ts={ts}", flush=True)
    print(f"  swarm_mean={mean!r} label={lab!r} conflict={cf!r}", flush=True)

    op = sk.get("bybit_open_pnl") if isinstance(sk.get("bybit_open_pnl"), dict) else {}
    if not op.get("enabled"):
        print(f"  bybit_open_pnl: disabled or missing ({op})", flush=True)
    else:
        s = op.get("sum_unrealised_pnl_usdt")
        print(f"  unrealised_pnl_sum_usdt={s}", flush=True)
        for vname, row in (op.get("venues") or {}).items():
            if not isinstance(row, dict):
                continue
            parts = [f"venue={vname}"]
            for k in ("symbol", "flat", "unrealised_pnl_usdt", "ok", "skipped", "reason", "detail"):
                if k in row and row[k] is not None:
                    parts.append(f"{k}={row[k]!r}")
            print("  " + " ".join(parts), flush=True)

    bf = sk.get("btc_future") if isinstance(sk.get("btc_future"), dict) else {}
    pos = bf.get("position") if isinstance(bf.get("position"), dict) else {}
    if pos and not pos.get("flat"):
        print(
            f"  btc_future position: side={pos.get('side')!r} size={pos.get('size')!r} "
            f"unrealisedPnl={pos.get('unrealisedPnl')!r} avgPrice={pos.get('avgPrice')!r}",
            flush=True,
        )
    elif bf.get("enabled"):
        print(f"  btc_future: flat or no position snapshot ok={bf.get('ok')!r}", flush=True)

    os.environ.setdefault("SYGNIF_BRIEFING_INCLUDE_SWARM", "1")
    bl = briefing_line_swarm(max_chars=400)
    if bl:
        print(f"  {bl}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
