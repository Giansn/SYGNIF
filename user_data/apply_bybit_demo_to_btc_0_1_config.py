#!/usr/bin/env python3
"""
Runtime Freqtrade config for **freqtrade-btc-0-1** → Bybit **USDT linear demo** (perpetual).

Merges CCXT options ``enableDemoTrading`` + ``hostname`` (see Bybit demo docs / NFI bridge).
If ``BYBIT_DEMO_API_KEY`` and ``BYBIT_DEMO_API_SECRET`` are set, injects them and ``dry_run: false``.
Otherwise forces ``dry_run: true``.

Requires ``bybit_ccxt_demo_patch.py`` applied in the image (or run before freqtrade).

Output: ``SYGNIF_BTC_0_1_RUNTIME_CONFIG`` (default ``/tmp/config_btc_0_1_runtime.json``).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_DEMO_OPTIONS = {
    "defaultType": "swap",
    "defaultSettle": "USDT",
    "enableDemoTrading": True,
    "hostname": "bybit.com",
}


def _merge_demo_exchange(exchange: dict) -> None:
    for ck in ("ccxt_config", "ccxt_async_config"):
        c = exchange.setdefault(ck, {})
        c.setdefault("enableRateLimit", True)
        opts = c.setdefault("options", {})
        for k, v in _DEMO_OPTIONS.items():
            opts[k] = v


def main() -> int:
    base = Path(
        os.environ.get(
            "SYGNIF_BTC_0_1_CONFIG",
            "/freqtrade/user_data/config_btc_strategy_0_1_paper_market.json",
        )
    )
    out = Path(
        os.environ.get(
            "SYGNIF_BTC_0_1_RUNTIME_CONFIG",
            "/tmp/config_btc_0_1_runtime.json",
        )
    )
    cfg = json.loads(base.read_text(encoding="utf-8"))
    ex = cfg.setdefault("exchange", {})
    if ex.get("name", "").lower() != "bybit":
        print("apply_bybit_demo_btc_0_1: exchange is not bybit, skipping demo merge", file=sys.stderr)
    else:
        _merge_demo_exchange(ex)

    env_k = (os.environ.get("BYBIT_DEMO_API_KEY") or "").strip()
    env_s = (os.environ.get("BYBIT_DEMO_API_SECRET") or "").strip()
    if env_k and env_s:
        ex["key"] = env_k
        ex["secret"] = env_s
        cfg["dry_run"] = False
        cfg["force_entry_enable"] = True
    else:
        cfg["dry_run"] = True
        cfg["force_entry_enable"] = False
        print(
            "apply_bybit_demo_btc_0_1: BYBIT_DEMO_API_KEY/SECRET missing → dry_run=True",
            file=sys.stderr,
        )

    cfg["bot_name"] = "btc-0-1-bybit-demo"
    out.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(
        f"apply_bybit_demo_btc_0_1: wrote {out} dry_run={cfg.get('dry_run')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
