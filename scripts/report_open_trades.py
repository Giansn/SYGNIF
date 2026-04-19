#!/usr/bin/env python3
"""
Print open **Bybit** USDT-linear positions: same logic as Swarm ``open_trades`` (``position/list``).

Symbols: ``SYGNIF_SWARM_OPEN_TRADES_BYBIT_SYMBOLS`` (comma-separated), else ``SYGNIF_SWARM_BTC_FUTURE_SYMBOL`` /
``SYGNIF_SWARM_BYBIT_SYMBOL``, else ``BTCUSDT``. Demo vs mainnet matches ``bybit_linear_hedge`` (see repo ``.env``).

Legacy Freqtrade (overseer + SQLite): ``python3 -c "from finance_agent.swarm_open_trades_freqtrade_archive import build_open_trades_report_freqtrade_legacy as r; import json; print(json.dumps(r(), indent=2))"``
(from repo root with ``finance_agent`` on ``PYTHONPATH``).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    root = _repo()
    if str(root / "finance_agent") not in sys.path:
        sys.path.insert(0, str(root / "finance_agent"))
    import swarm_knowledge as sk  # noqa: PLC0415

    print(json.dumps(sk.build_open_trades_report(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
