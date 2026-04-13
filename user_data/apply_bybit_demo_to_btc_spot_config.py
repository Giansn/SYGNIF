#!/usr/bin/env python3
"""
Build runtime Freqtrade config for **freqtrade-btc-spot** → Bybit **demo** (spot).

- Merges ``api-demo.bybit.com`` + ``enableDemoTrading`` into ``ccxt_config`` / ``ccxt_async_config``.
- If ``BYBIT_DEMO_API_KEY`` + ``BYBIT_DEMO_API_SECRET`` are set (Docker ``env_file``), injects them
  and sets ``dry_run: false``.
- If demo mode is on but keys are missing in both env and base JSON, forces ``dry_run: true``.
- If ``SYGNIF_BTC_SPOT_SERVE_ONLY`` is truthy, forces ``dry_run: true`` (paper / serving only).

Output path: ``SYGNIF_BTC_SPOT_RUNTIME_CONFIG`` (default ``/tmp/config_btc_spot_runtime.json``).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEMO_API = {
    "public": "https://api-demo.bybit.com",
    "private": "https://api-demo.bybit.com",
}


def main() -> int:
    use_demo = os.environ.get("SYGNIF_BTC_SPOT_USE_BYBIT_DEMO", "1").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    base = Path(
        os.environ.get(
            "SYGNIF_BTC_SPOT_CONFIG",
            "/freqtrade/user_data/config_btc_spot_dedicated.json",
        )
    )
    out = Path(
        os.environ.get(
            "SYGNIF_BTC_SPOT_RUNTIME_CONFIG",
            "/tmp/config_btc_spot_runtime.json",
        )
    )
    cfg = json.loads(base.read_text(encoding="utf-8"))
    ex = cfg.setdefault("exchange", {})
    if ex.get("name", "").lower() != "bybit":
        print("apply_bybit_demo: exchange is not bybit, skipping demo merge", file=sys.stderr)
    elif use_demo:
        for ck in ("ccxt_config", "ccxt_async_config"):
            c = ex.setdefault(ck, {})
            opts = c.setdefault("options", {})
            opts["defaultType"] = "spot"
            opts["enableDemoTrading"] = True
            c["urls"] = {"api": dict(DEMO_API)}

    env_k = (os.environ.get("BYBIT_DEMO_API_KEY") or "").strip()
    env_s = (os.environ.get("BYBIT_DEMO_API_SECRET") or "").strip()
    if env_k and env_s:
        ex["key"] = env_k
        ex["secret"] = env_s
        cfg["dry_run"] = False
    elif use_demo:
        fk = str(ex.get("key") or "").strip()
        fs = str(ex.get("secret") or "").strip()
        if not fk or not fs:
            cfg["dry_run"] = True

    serve_only = os.environ.get("SYGNIF_BTC_SPOT_SERVE_ONLY", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if serve_only:
        cfg["dry_run"] = True

    out.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(
        f"apply_bybit_demo: wrote {out} use_demo={use_demo} dry_run={cfg.get('dry_run')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
