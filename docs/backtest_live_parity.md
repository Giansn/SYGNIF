# Backtest / Live Parity Audit — NautilusTrader Equivalence Model

> CUR-13 v2: Parity audit informed by NT's BacktestEngine / SimulatedExchange architecture.

## NT Parity Architecture Reference

NautilusTrader achieves backtest/live parity through (sources: `crates/backtest/src/engine/mod.rs`, `crates/backtest/src/exchange/mod.rs`):

| NT Component | Purpose | How It Ensures Parity |
|---|---|---|
| **BacktestEngine** | Orchestrates simulated trading | Uses same NautilusKernel as live |
| **SimulatedExchange** | Emulates venue behavior | Same OrderMatchingEngine as live fill model |
| **TestClock** | Deterministic time progression | Replaces LiveClock; strategies see identical API |
| **FillModel** | Simulates slippage, partial fills | Configurable latency_model, fill_model per venue |
| **FeeModel** | Exact fee calculation | MakerTakerFeeModel with venue-specific rates |
| **BacktestDataConfig** | Data window specification | instrument_id, start_time, end_time, catalog_path |

**NT key insight**: Strategies run identically in backtest and live because the Kernel abstracts clock, data, and execution behind the same Actor interface. Only the clock and exchange are swapped.

## Parity Checklist (NT-informed)

### 1. Look-Ahead Bias (NT: TestClock advances only on data event)

| Item | Status | NT Equivalent | Notes |
|---|---|---|---|
| `populate_indicators` uses `.iloc[-1]` / `.iloc[-2]` only | OK | Strategy.on_bar(bar) | No future data accessed |
| `populate_entry_trend` has `is_last_candle` guard | OK | Strategy.on_bar() only fires on latest | Claude calls gated to last candle |
| Failure swing uses `.shift(1)` | OK | Rolling window with lag | 48-bar max of `high.shift(1)` |
| TA score is fully vectorized | OK | DataActor processes complete dataset | No future dependency |
| Global protections use current/lagged indicators | OK | Strategy checks current Cache state | RSI cascade uses current values |
| `startup_candle_count = 400` | OK | BacktestDataConfig.start_time (warmup) | Sufficient for EMA_200 |

### 2. Warmup (NT: BacktestEngine pre-rolls data before strategy starts)

| Indicator | Min Bars Required | Covered by 400? | NT Equivalent |
|---|---|---|---|
| EMA_200 | ~200 | Yes | DataEngine pre-warms bars |
| EMA_120 | ~120 | Yes | " |
| RSI_14 | ~14 | Yes | " |
| Bollinger_20 | ~20 | Yes | " |
| volume_sma_25 | ~25 | Yes | " |
| sf_resistance (48-bar) | ~48 | Yes | " |
| ATR_14 | ~14 | Yes | " |
| Informative TFs (1h, 4h, 1d) | varies | **WARNING** | NT: separate DataConfig per TF |

**NT comparison**: NT's BacktestDataConfig specifies exact `start_time` and pre-rolls all indicators before the strategy's `on_start()` fires. Freqtrade's `startup_candle_count=400` on 5m = ~33h, which under-warms daily EMAs. NT would specify `start_time = run_start - 200 days` for daily data.

**Recommendation**: Increase `startup_candle_count` to 600, or ensure backtests use `--timerange` with sufficient historical data for daily informative TFs.

### 3. Fee / Slippage Model (NT: BacktestVenueConfig.fee_model + fill_model)

| Parameter | Freqtrade BT | Bybit Live | NT BacktestVenueConfig | Aligned? |
|---|---|---|---|---|
| Maker fee | 0.1% default | 0.02% (VIP0) | `MakerTakerFeeModel(maker=0.0002)` | **NO** — BT pessimistic |
| Taker fee | 0.1% default | 0.055% (VIP0) | `MakerTakerFeeModel(taker=0.00055)` | **NO** — BT pessimistic |
| Slippage | Not modeled | Variable | `FillModel(latency_ms=50)` | **NO** — not modeled |
| Funding rate | Not modeled | ±0.01% / 8h | NT: `ExchangeRateCalculator` | **NO** — significant > 12h |
| Partial fills | Not modeled | Possible | `FillModel(prob_fill=0.95)` | **NO** — FT assumes full fill |

**NT advantage**: NT's `SimulatedExchange` uses configurable `FillModel` and `FeeModel` per venue, so backtest fills match live behavior. Freqtrade backtests assume instant full fills at candle OHLC prices.

**Recommendations**:
1. Set backtest `fee` to Bybit actuals: `"fee": 0.00055` (taker, conservative)
2. For futures backtests, estimate funding cost in `custom_exit` for trades > 8h
3. Use `order_types.entry = "limit"` (already set) — reduces slippage impact
4. NT-style improvement: add a `FillModel` wrapper that adjusts backtest entry prices by estimated spread

### 4. Config Drift (NT: BacktestRunConfig captures ALL parameters)

NT's `BacktestRunConfig` is a single serializable object containing engine config, data config, and venue config. Any drift between backtest and live is immediately visible by diffing configs.

| Setting | EC2 Live | Local/Backtest | NT Would Capture In | Match? |
|---|---|---|---|---|
| `max_open_trades` (futures) | 12 | Check config | EngineConfig.max_positions | Verify |
| `dry_run_wallet` (futures) | $240 | $240 | VenueConfig.starting_balances | OK |
| `tradable_balance_ratio` | 0.80 | 0.80 | VenueConfig | OK |
| `stoploss_on_exchange` | true | N/A for BT | SimulatedExchange handles natively | N/A |
| `CooldownPeriod` | 2 candles | 2 candles | Strategy config | OK |
| Pairlist | VolumePairList (dynamic) | Static in BT | DataConfig.instrument_ids | **DRIFT** |
| Strategy class | `MarketStrategy` (futures CLI) | `SygnifStrategy` | `Trader.add_strategy(id)` | **CHECK** |

**NT approach to solve drift**: Use `research_metadata.py` (CUR-15) to freeze all config at run time. Compare `config_hash` between backtest and live runs.

### 5. Execution Model Differences

| Behavior | Freqtrade Live | Freqtrade Backtest | NT BacktestEngine |
|---|---|---|---|
| Order matching | Bybit matching engine | OHLC candle price check | `OrderMatchingEngine` (simulated book) |
| Stoploss trigger | Exchange-side trigger | Price crosses SL on next candle | `SimulatedExchange.process_bar()` |
| Trailing stop | `custom_stoploss` called per candle | Same function called | Same logic, deterministic clock |
| Multiple exits same candle | First trigger wins | First trigger wins | Priority queue by ts_event |
| Ratcheting trail update | `stoploss_on_exchange_update_freq` | N/A (checked each candle) | `OrderUpdated` event per tick |

**Key gap**: Freqtrade backtests check SL/TP at candle OHLC granularity. A candle that touches both TP and SL is resolved by OHLC order (open→high→low→close or open→low→high→close). NT's tick-level replay resolves this deterministically.

### 6. Strategy Code Parity

| Component | Live (SygnifStrategy) | Finance Agent SKILL.md | Match? |
|---|---|---|---|
| RSI-14 thresholds | <35 / >70 | <35 / >70 | OK |
| RSI-3 | <20 / >80 | <20 / >80 | OK |
| EMA crossover | EMA9 > EMA21 | EMA9 > EMA21 | OK |
| Bollinger | Close < lower / > upper | Price < lower / > upper | OK |
| Aroon | Up > 70 & Down < 30 | Up > 70 & Down < 30 | OK |
| StochRSI | K < 20 & D < 20 | K < 20 & D < 20 | OK |
| CMF | > 0.05 / < -0.05 | > 0.05 / < -0.05 | OK |
| Volume gate | > 1.2x SMA25 | > 1.5x avg (SKILL.md) | **MISMATCH** |
| Swing window | 48 bars | 84-period (SKILL.md) | **MISMATCH** |
| Swing vol filter | > 0.03 (3%) | > 5% (SKILL.md) | **MISMATCH** |

**Root cause**: Code was updated but documentation wasn't synced. Code is source of truth.

## Action Items (NT-informed priority)

| Priority | Fix | NT Pattern |
|---|---|---|
| **HIGH** | Align finance-agent SKILL.md parameters with live code | BacktestRunConfig parity |
| **HIGH** | Set backtest fees to Bybit actuals (0.02%/0.055%) | BacktestVenueConfig.fee_model |
| **MEDIUM** | Use `research_metadata.py` to freeze + compare configs | BacktestRunConfig serialization |
| **MEDIUM** | Model funding rate for futures backtests > 8h | VenueConfig.funding_model |
| **MEDIUM** | Add spread/slippage estimate to backtest entry prices | FillModel.latency_model |
| **LOW** | Increase `startup_candle_count` to 600 | BacktestDataConfig.start_time |
| **LOW** | Document static pairlist vs live VolumePairList | DataConfig.instrument_ids |
| **FUTURE** | Tick-level backtest engine (if scale justifies) | BacktestEngine.run() |
