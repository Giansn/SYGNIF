"""
Minimal live strategy: subscribe to ``QuoteTick`` and log (no orders).

Used by ``run_bybit_demo_trading_node.py`` to verify Bybit **demo** data routing.
After ``max_ticks``, raises ``KeyboardInterrupt`` so the runner's ``node.run()`` / ``finally: dispose()`` path executes (strategy ``stop()`` alone does not shut down the live node).
"""

from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class BybitDemoQuoteSmokeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    max_ticks: int = 8


class BybitDemoQuoteSmoke(Strategy):
    def __init__(self, config: BybitDemoQuoteSmokeConfig) -> None:
        super().__init__(config)
        self._seen = 0

    def on_start(self) -> None:
        self.subscribe_quote_ticks(self.config.instrument_id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self._seen += 1
        self.log.info(
            "QuoteTick #%s bid=%s ask=%s",
            self._seen,
            tick.bid_price,
            tick.ask_price,
        )
        if self._seen >= self.config.max_ticks:
            self.log.info("max_ticks reached — exiting node (KeyboardInterrupt)")
            raise KeyboardInterrupt
