# fast_reactor_v2 — experimental fib_sfp trigger

Replacement for fast-reactor's dead `bounce` trigger (whose feeder
`sygnif-bounce-watcher.service` is disabled). Fuses Jules' Fibonacci /
Support-Resistance / Swing-Failure-Pattern math (merged into
`SygnifStrategy.py` via PR #10) into the live WebSocket reactor.

## ⚠ Backtest failed — do not deploy

See `BACKTEST_RESULTS.md` for the full negative result and proposed
follow-up directions. Summary:

```
30 days of BTCUSDT 1m, 25 parameter combos tested
Best observed: 26.4% win rate, EV −$2.80/trade
Gates required: ≥40% WR + ≥5 fires/wk + EV > $0
```

The module + tests + backtest harness are reusable scaffolding for the
next iteration. Don't delete.

## Files

| File | Lines | Purpose |
|---|---|---|
| `fib_sfp_trigger.py` | 228 | `FibSfpState` class — rolling 240-bar buffer + SFP detection + fib-proximity check |
| `tests/test_fib_sfp_trigger.py` | 184 | 8 unit tests — cold start, SFP detection, fib proximity, edge-trigger, .shift(1) bias guard, performance (p99 < 1 ms) |
| `backtest_fib_sfp.py` | 218 | Replay harness — pulls Bybit V5 1m klines, simulates entries, checks acceptance gates |
| `BACKTEST_RESULTS.md` | this | Failure report with parameter sweeps |
| `data/btc_1m_klines.jsonl` | cached | 30 days of BTC klines (~10 MB) — gitignored |
| `data/backtest_result.json` | output | Per-trade outcomes for the baseline config |

## Run the tests

```bash
cd experiments/fast_reactor_v2
python tests/test_fib_sfp_trigger.py
```

Expected: `ALL 8 TESTS PASSED` in < 1 second.

## Run the backtest

```bash
cd experiments/fast_reactor_v2
python backtest_fib_sfp.py --days 30
```

First run fetches ~43k klines (~10 sec on a good connection). Subsequent
runs use the cached `data/btc_1m_klines.jsonl`. Replay finishes in ~1
second.

## Why we're keeping this branch

Per `AGENTS.md` §6: failed backtests document themselves as PRs.
Closing without merge is the right action; rebuilding from scratch
later would lose the negative-result evidence + the (working) scaffold.

## How to iterate

`BACKTEST_RESULTS.md` lists 5 design changes worth trying. Each one is
a separate experiment / separate PR / separate backtest run on this
same data. The module shape (`FibSfpState.evaluate(bar) → payload or
None`) is stable; just modify the trigger conditions inside.
