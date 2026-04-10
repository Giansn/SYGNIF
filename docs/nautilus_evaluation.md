# NautilusTrader Migration / Hybrid Evaluation v2

> CUR-17 v2: Spike eval grounded in actual NautilusTrader repository analysis via GitNexus.

## Executive Summary

**Recommendation: STAY on Freqtrade, adopt Nautilus patterns incrementally.**

After indexing and querying the NautilusTrader codebase (589 symbols, 47 execution flows analyzed), the most valuable NT patterns have already been extracted into SYGNIF modules (CUR-11 through CUR-16). A full migration is unjustified at current scale.

## NT Codebase Analysis (from actual repository)

### Architecture Examined

| NT Source | What We Found | SYGNIF Adoption |
|---|---|---|
| `crates/model/src/events/order/any.rs` | 16 OrderEventAny variants with UUID4 + nanosecond timestamps | → `event_log.py` EVENT_TYPES |
| `crates/model/src/events/position/mod.rs` | 4 PositionEvent variants (opened/changed/closed/adjusted) | → `event_log.py` POSITION_EVENT_TYPES |
| `crates/risk/src/engine/mod.rs` | RiskEngine: pre-trade validation, throttler, max_notional, TradingState | → `risk_manager.py` RiskManager |
| `crates/risk/src/sizing.rs` | `calculate_fixed_risk_position_size()` formula | → `risk_manager.py` calculate_position_size() |
| `crates/trading/src/algorithm/mod.rs` | ExecAlgorithm trait: on_order(), spawn_market/limit | → `execution_policies.py` PolicyEngine |
| `crates/trading/src/algorithm/twap.rs` | TwapAlgorithm: time-sliced child orders | → `execution_policies.py` twap_scale_in() |
| `crates/execution/src/trailing.rs` | TrailingOffsetType (Price/BasisPoints/Ticks), TriggerType | → `execution_policies.py` trailing_stop_* |
| `crates/common/src/actor/mod.rs` | Actor trait: on_start, on_stop, on_event, on_data | → `docs/actor_boundaries.md` |
| `crates/common/src/msgbus/mod.rs` | MessageBus: topic-based pub/sub, correlation IDs | → webhook HTTP (simplified) |
| `crates/backtest/src/engine/mod.rs` | BacktestEngine: TestClock + SimulatedExchange | → `docs/backtest_live_parity.md` |
| `crates/backtest/src/config.rs` | BacktestRunConfig: data + venue + engine configs | → `research_metadata.py` |
| `crates/backtest/src/exchange/mod.rs` | SimulatedExchange: OrderMatchingEngine with FillModel | Documented, not implemented |
| `nautilus_trader/risk/config.py` | RiskEngineConfig: bypass, max_order_submit_rate | → `risk_manager.py` RiskEngineConfig |
| `nautilus_trader/analysis/statistics.py` | PortfolioStatisticCalculator: sharpe, sortino, etc. | → `perf_analysis.py` compute_portfolio_stats() |

### NT Integration Points for Bybit

From `docs/integrations/bybit.md` in the NT repository:

| Feature | NT Bybit Adapter | SYGNIF (Freqtrade + ccxt) |
|---|---|---|
| Spot trading | BybitSpotHttpClient + BybitSpotWebSocket | ccxt bybit (REST + WS via FT) |
| Futures (linear) | BybitLinearHttpClient + BybitLinearWebSocket | ccxt bybit futures mode |
| Testnet support | `is_testnet=True` config flag | `dry_run: true` in FT config |
| Order types | Market, Limit, StopMarket, StopLimit, TrailingStop | Limit, Market, StopLoss (via FT) |
| Data types | OrderBook (L1, L2, L3), Trades, Bars, BybitTicker | OHLCV bars only (via FT) |
| Account types | CASH, MARGIN | Isolated margin (via FT config) |
| Multi-venue | Native (add multiple BybitClients) | Single venue only |

## Evaluation Criteria (Code-Grounded)

| Criterion | Freqtrade (current) | NautilusTrader (actual code) | Verdict |
|---|---|---|---|
| **Event model** | Webhooks + SQLite | 20 event types, UUID4 + ns timestamps, full replay | NT superior; we've adopted schema in event_log.py |
| **Risk engine** | In-strategy, now extracted (risk_manager.py) | Dedicated RiskEngine crate with throttler, max_notional, TradingState | Comparable after CUR-12 |
| **Execution algos** | custom_stoploss/custom_exit | ExecAlgorithm trait, TWAP built-in, trailing stop calculator | NT better; CUR-14 bridges the gap |
| **Backtesting** | Candle-based, OHLC fill simulation | Tick-level, SimulatedExchange with FillModel/FeeModel | NT much better for realistic fills |
| **Multi-venue** | Bybit only (via ccxt) | Native multi-venue, dedicated adapters per exchange | FT sufficient for single-exchange |
| **Actor isolation** | Docker containers (coarse-grained) | Fine-grained Actor trait with MessageBus | NT better; Docker adequate at scale |
| **Performance** | Python GIL-bound, adequate for 20 pairs | Rust core, Cython hot path, nanosecond clock | FT adequate at current scale |
| **Portfolio analytics** | SQLite queries + perf_analysis.py | PortfolioStatisticCalculator, ReportProvider | Comparable after CUR-6 |
| **Reproducibility** | research_metadata.py (config hash + env) | BacktestRunConfig (full serializable config) | Comparable after CUR-15 |
| **Documentation** | Extensive FT docs, large community | Sparser docs, small community, improving | FT wins |
| **Learning curve** | Well-known, Python-only | Rust + Python, complex domain model | FT much easier |
| **Team capacity** | Solo developer | Solo developer | Can't maintain two systems |

## What We've Already Adopted

| NT Pattern | SYGNIF Module | Status |
|---|---|---|
| Event sourcing (OrderEventAny, PositionEvent) | `trade_overseer/event_log.py` | Done (CUR-11 v2) |
| RiskEngine (pre-trade checks, throttler, TradingState) | `trade_overseer/risk_manager.py` | Done (CUR-12 v2) |
| ExecAlgorithm (named policies, trailing stop types) | `trade_overseer/execution_policies.py` | Done (CUR-14 v2) |
| PortfolioStatisticCalculator | `trade_overseer/perf_analysis.py` | Done (CUR-6 v2) |
| BacktestRunConfig | `trade_overseer/research_metadata.py` | Done (CUR-15 v2) |
| Actor/MessageBus model | `docs/actor_boundaries.md` | Documented (CUR-16 v2) |
| BacktestEngine parity checks | `docs/backtest_live_parity.md` | Documented (CUR-13 v2) |

## What We Haven't Adopted (and Why)

| NT Feature | Reason to Skip | When to Reconsider |
|---|---|---|
| **Tick-level backtest** | Candle-level sufficient for 5m TF strategy | Sub-candle fill precision matters |
| **MessageBus pub/sub** | HTTP webhooks work for 3-4 actors | 10+ actors or latency-critical routing |
| **Rust core** | Python adequate at 20 pairs × 5m | > 100 pairs or sub-second processing |
| **Multi-venue adapters** | Single Bybit venue | Trading on 2+ exchanges |
| **OrderMatchingEngine** | Freqtrade's candle-based matching acceptable | Strategy depends on limit order fills |
| **Full event replay** | JSONL log exists but no replay engine | Need to replay and re-analyze historical events |

## Cost Analysis (Updated with Code Knowledge)

### Full Migration to NautilusTrader

| Cost | Estimate | NT-specific Complexity |
|---|---|---|
| Rewrite SygnifStrategy as NT Strategy | 2-3 weeks | Must map populate_entry_trend → on_bar, custom_stoploss → trailing stop config |
| Rewrite notification → NT Actor | 1 week | Subscribe to OrderFilled/PositionClosed events on MessageBus |
| Rewrite overseer → NT Actor | 1 week | Port LLM eval loop as periodic timer callback |
| Setup Bybit adapter config | 2-3 days | BybitLinearHttpClient + WebSocket + testnet config |
| Rewrite Docker/deploy | 3-5 days | NT uses NautilusKernel, single process per Trader |
| Learning curve | 2-4 weeks | Rust/Cython internals for debugging |
| **Total** | **6-10 weeks** | |
| **Risk** | HIGH | No parallel running during transition |

### Hybrid (NT Backtesting, FT Live)

| Cost | Estimate | Notes |
|---|---|---|
| NT backtest adapter for SygnifStrategy signals | 1-2 weeks | Port TA score to NT Strategy, compare signals |
| BacktestRunConfig for SYGNIF | 3 days | Already done via research_metadata.py |
| Signal parity verification | 1 week | Run both, compare entry/exit signals |
| Ongoing maintenance | ~2h/week | Keep two codebases in sync |
| **Total** | **2-3 weeks + ongoing** | |
| **Risk** | MEDIUM | Signal drift between BT and live |

### Stay on Freqtrade + NT Patterns (current approach)

| Cost | Estimate | Notes |
|---|---|---|
| All CUR-11 through CUR-16 modules | **Already done** | event_log, risk_manager, execution_policies, perf_analysis, research_metadata |
| Documentation (actor boundaries, parity audit) | **Already done** | actor_boundaries.md, backtest_live_parity.md |
| **Total** | **Zero additional** | |
| **Risk** | LOW | Incremental, no migration |

## Decision Matrix

| Option | Cost | Risk | Value | Recommendation |
|---|---|---|---|---|
| Full migrate | HIGH (6-10 wk) | HIGH | Access to tick-level BT, multi-venue | **NO** — premature |
| Hybrid | MEDIUM (3 wk) | MEDIUM | Better backtest fidelity | **MAYBE** — if BT accuracy becomes bottleneck |
| Stay + patterns | ZERO | LOW | 80% of NT value, 0% migration cost | **YES** — best ROI |

## When to Reconsider

Migrate when **any two** become true:

1. **Multi-exchange**: Trading on 2+ exchanges simultaneously
2. **Scale**: > 50 concurrent positions needing portfolio-level risk
3. **Execution edge**: Trade sizes > $10k where TWAP/iceberg matter
4. **Tick-level needs**: Strategy profitability depends on sub-candle fills
5. **Team grows**: Second developer who can maintain parallel systems
6. **Event replay**: Need to replay historical events for strategy optimization

**Decision: Stay on Freqtrade. Ship the NT-pattern epic. Revisit Q3 2026 if scale changes.**
