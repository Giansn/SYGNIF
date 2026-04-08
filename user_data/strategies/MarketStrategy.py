"""
MarketStrategy — futures Docker entrypoint (market-strategy v2).

Extends the frozen v1 snapshot in MarketStrategy1.py. Override methods here
for new market logic; keep MarketStrategy1.py unchanged as rollback reference.
"""

from MarketStrategy1 import MarketStrategy1


class MarketStrategy(MarketStrategy1):
    """New market strategy for `freqtrade-futures` only (see docker-compose)."""

    pass
