#!/usr/bin/env python3
"""
Freqtrade + Bybit: ccxt accepts ``options.enableDemoTrading`` but does not call
``enable_demo_trading()`` automatically. Without that call, private requests use
production hosts and **demo-only API keys** fail with retCode 10003 during
``additional_exchange_init`` (e.g. ``set_position_mode``).

Idempotent patch to ``/freqtrade/freqtrade/exchange/exchange.py`` (runtime).
Marker: ``BYBIT_CCXT_DEMO_PATCH``.
"""
from __future__ import annotations

import sys
from pathlib import Path

TARGET = Path("/freqtrade/freqtrade/exchange/exchange.py")
MARKER = "BYBIT_CCXT_DEMO_PATCH"
NEEDLE = "            api = getattr(ccxt_module, name.lower())(ex_config)"
REPLACEMENT = """            api = getattr(ccxt_module, name.lower())(ex_config)
            if (
                name.lower() == "bybit"
                and ex_config.get("options", {}).get("enableDemoTrading")
                and hasattr(api, "enable_demo_trading")
            ):  # BYBIT_CCXT_DEMO_PATCH
                api.enable_demo_trading(True)  # BYBIT_CCXT_DEMO_PATCH
"""


def main() -> int:
    if not TARGET.is_file():
        print(f"bybit_ccxt_demo_patch: skip (no {TARGET})", file=sys.stderr)
        return 0
    text = TARGET.read_text(encoding="utf-8")
    if MARKER in text:
        print("bybit_ccxt_demo_patch: already applied")
        return 0
    if NEEDLE not in text:
        print("bybit_ccxt_demo_patch: needle not found; freqtrade layout changed", file=sys.stderr)
        return 1
    text = text.replace(NEEDLE, REPLACEMENT, 1)
    TARGET.write_text(text, encoding="utf-8")
    print("bybit_ccxt_demo_patch: applied to", TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
