# Actor Boundaries — NautilusTrader Actor Model Applied to SYGNIF

> CUR-16 v2: One concern per process, mapped to NT's Actor/MessageBus architecture.

## NT Actor Architecture Reference

NautilusTrader implements a strict actor model (sources: `crates/common/src/actor/mod.rs`, `crates/common/src/msgbus/mod.rs`):

| NT Concept | Description | SYGNIF Equivalent |
|---|---|---|
| **Actor** | Base trait — owns state, handles messages via `on_*` | Docker container / systemd service |
| **Strategy** (extends Actor) | Generates signals, submits orders via RiskEngine | `SygnifStrategy.py` inside freqtrade |
| **DataActor** (extends Actor) | Receives market data, publishes processed data | In-strategy (populate_indicators) |
| **ExecAlgorithm** (extends Actor) | Receives orders, spawns child orders | `execution_policies.py` |
| **MessageBus** | Pub/sub message routing between actors | HTTP webhooks + API polling |
| **NautilusKernel** | Orchestrates all actors, clock, cache, msgbus | `docker-compose.yml` |
| **Trader** | Registers strategies + exec algorithms | Freqtrade bot instance |

## SYGNIF Actor Map (NT Alignment)

| Actor | Container / Process | NT Equivalent | Port | Single Concern |
|---|---|---|---|---|
| **freqtrade** | `freqtrade` | Trader + Strategy + DataActor | 8080 | Spot strategy execution |
| **freqtrade-futures** | `freqtrade-futures` | Trader + Strategy + DataActor | 8081 | Futures strategy execution |
| **notification-handler** | `notification-handler` | Actor (on_event subscriber) | 8089 | Event → Telegram formatting |
| **trade-overseer** | `trade-overseer` | Actor (portfolio analyst) | 8090 | Trade monitoring + LLM commentary |
| **dashboard-spot** | systemd service | N/A (external UI) | 5000 | Spot web dashboard |
| **dashboard-futures** | systemd service | N/A (external UI) | 5001 | Futures web dashboard |
| **notify** | systemd oneshot | N/A (lifecycle hook) | — | System up/down alerts |

## MessageBus Mapping

NT's MessageBus routes messages by topic. SYGNIF uses HTTP as the transport:

```
NT MessageBus:                          SYGNIF Equivalent:
┌──────────────────────────┐           ┌──────────────────────────┐
│ Strategy.submit_order()  │           │ SygnifStrategy →         │
│   → msgbus.publish(      │           │   webhook POST to        │
│     "SubmitOrder", cmd)  │           │   notification-handler   │
│   → RiskEngine.execute() │           │   (entry/exit events)    │
│   → ExecutionEngine      │           │                          │
│   → ExecClient.submit()  │           │ Freqtrade engine →       │
│                          │           │   Bybit API (orders)     │
└──────────────────────────┘           └──────────────────────────┘

NT topic routing:                       SYGNIF routing:
  events.order.*  → Logger, Cache         POST /webhook → notification-handler
  events.position.* → Portfolio           GET /api/v1/status → trade-overseer
  data.bars.* → Strategy.on_bar()         Strategy internal → populate_*
```

## Input/Output Map (NT-style data flow)

### freqtrade / freqtrade-futures (NT: Trader + Strategy)

| Direction | NT Equivalent | What | Target |
|---|---|---|---|
| **IN** | DataClient.subscribe_bars() | Market data | Bybit API (OHLCV) |
| **IN** | BacktestDataConfig | Config | `config.json` / `config_futures.json` |
| **OUT** | Cache.add_position() | Trade DB | `tradesv3.sqlite` / `tradesv3-futures.sqlite` |
| **OUT** | MessageBus.publish(OrderFilled) | Webhooks | `notification-handler:8089/webhook` |
| **OUT** | Trader API | REST API | `:8080` / `:8081` (dashboards, overseer) |
| **OUT** | LoggerAdapter | Logs | `freqtrade.log` / `freqtrade-futures.log` |

### notification-handler (NT: Actor subscribed to order/position events)

| Direction | NT Equivalent | What | Target |
|---|---|---|---|
| **IN** | Actor.on_event(OrderFilled) | POST /webhook | From freqtrade containers |
| **OUT** | (external) | Telegram messages | Telegram Bot API |
| **OUT** | (external) | Claude review | Anthropic API |

### trade-overseer (NT: Actor with portfolio analysis role)

| Direction | NT Equivalent | What | Target |
|---|---|---|---|
| **IN** | Portfolio.positions() | GET /api/v1/status | Freqtrade APIs |
| **IN** | (external) | GET /briefing | finance-agent |
| **OUT** | MessageBus.publish() | Telegram | Overseer bot token |
| **OUT** | EventLog (NT Cache) | JSONL events | `trade_overseer/data/events.jsonl` |
| **OUT** | (internal) | State | `trade_overseer/data/state.json` |
| **OUT** | Actor API | REST | `:8090` — `/overview`, `/trades`, `/health` |

## Event Flow (NT OrderEvent lifecycle mapped to SYGNIF)

```
NT Event Chain:                         SYGNIF Event Chain:
━━━━━━━━━━━━━━━━━━━                     ━━━━━━━━━━━━━━━━━━━
OrderInitialized                        populate_entry_trend (signal)
  ↓                                       ↓
OrderSubmitted                          confirm_trade_entry (risk check)
  ↓                                       ↓
OrderAccepted                           Freqtrade → Bybit (order placed)
  ↓                                       ↓
OrderFilled                             Webhook → notification-handler
  ↓                                     "Order Filled" Telegram
PositionOpened                            ↓
  ↓                                     trade-overseer polls API
PositionChanged (unrealized PnL)        LLM eval → "HOLD/TRAIL/CUT"
  ↓                                       ↓
OrderUpdated (SL modify)                custom_stoploss (ratcheting trail)
  ↓                                       ↓
OrderFilled (exit)                      custom_exit / trailing_stop_loss
  ↓                                       ↓
PositionClosed                          Webhook → notification-handler
                                        Claude review → Telegram "Closed"
                                          ↓
                                        SQLite trade record
```

## Periodic Tasks (NT Clock.set_timer equivalent)

NT uses `Clock.set_timer(name, interval, callback)` for periodic actor work.
SYGNIF uses in-process loops and sleep intervals:

| Task | Actor | Interval | NT Equivalent | Mechanism |
|---|---|---|---|---|
| Movers refresh | freqtrade | 4h | Clock.set_timer("refresh_movers") | `_refresh_movers()` in `bot_loop_start` |
| New pairs refresh | freqtrade | 30m | Clock.set_timer("refresh_pairs") | `_refresh_new_pairs()` in `bot_loop_start` |
| Overseer poll | trade-overseer | 30m | Clock.set_timer("poll_trades") | `time.sleep(POLL_INTERVAL_SEC)` |
| State save | trade-overseer | Every poll | Actor.on_save() | `save_state()` after each eval |
| Doom cooldown persist | freqtrade | On SL exit | Actor.on_event(PositionClosed) | `_save_doom_cooldown()` |

## Data Freshness (NT Cache staleness checks)

NT Cache tracks `ts_last_event` per instrument. SYGNIF equivalent:

| Data | Stale After | NT Equivalent | Check |
|---|---|---|---|
| Movers list | 4h | Cache.instruments (periodic refresh) | `_movers_last_update` vs `time.time()` |
| New pairs | 30m | DataEngine.subscribed_bars() | `_new_pairs_last_update` |
| Overseer state | 1h | Cache.positions() ts | `last_eval_time` in `/overview` |
| Finance agent briefing | — | External data feed | Fetched on-demand |
| Doom cooldown | 4h per pair | RiskEngine timer | Auto-expires |

## Known Gaps vs NT Architecture

| Gap | NT Has | SYGNIF Status | Priority |
|---|---|---|---|
| **No MessageBus** | Native pub/sub, topic routing | HTTP webhooks (fire-and-forget) | LOW — adequate at scale |
| **No event replay** | Full event sourcing, replay from log | JSONL log exists but no replay engine | MEDIUM — build when needed |
| **No actor lifecycle** | start/stop/resume/dispose per actor | Docker start/stop only | LOW — Docker sufficient |
| **Coupled Strategy+Data** | Separate Strategy and DataActor | populate_indicators inside Strategy | LOW — Freqtrade design |
| **No ExecClient abstraction** | ExecClient per venue | Freqtrade handles via ccxt | LOW — single venue |
| **No Portfolio object** | PortfolioManager tracks all positions | SQLite + API queries | MEDIUM — event_log partially covers |

## Known Issues

1. **Overseer HTTP bind**: Fixed — now uses `config.HTTP_HOST` (was hardcoded `127.0.0.1`).
2. **Strategy name mismatch**: Futures compose uses `--strategy MarketStrategy` but config says `SygnifStrategy` — CLI wins, but confusing. NT would use `Trader.add_strategy(strategy_id)`.
3. **Shared volume**: Both containers mount `user_data/` — intentional but means shared state. NT isolates per-Trader Cache.
4. **No dead letter queue**: NT MessageBus has error handlers for undeliverable messages. Webhook failures are silently dropped.
