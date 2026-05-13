# sfp_trader — separate SFP-priority perp opener

Architectural separation of the SFP signal from fast-reactor, per the
2026-05-13 decision: "SFP gets priority when it fires, fast-reactor handles
casual market movement."

> **Status (2026-05-13)**: architecture validated, **signal failed** the
> 30-day backtest gates across 38 variant configs. See
> [`variants/BACKTEST_RESULTS.md`](variants/BACKTEST_RESULTS.md). The
> daemon stays scaffolded but disabled (kill-switch armed). The
> `fast_reactor_coordination.patch` (mutex + fib-zone veto) is the
> independently useful piece — apply that even if SFP-trader never
> enables.

## What's here

```
experiments/sfp_trader/
├── sygnif_sfp_trader.py             new daemon — SFP-only, sygSFP prefix
├── fib_sfp_trigger.py                shared SFP+Fib math (copied from PR #15)
├── sygnif-sfp-trader.service         systemd unit (disabled by default)
├── fast_reactor_coordination.patch   small patch to live fast-reactor:
│                                     (1) respect SFP mutex, (2) fib-zone veto
├── variants/                         backtest harness + 5 variant configs
│   └── BACKTEST_RESULTS.md           final 38-config sweep result (all FAIL)
└── README.md                         this file
```

## Architecture

```
                  ┌─────────────────────────┐
                  │   intel_summary.json    │ ◄── shared by all
                  │   strategy_claim.json   │ ◄── shared mutex
                  └────────────┬────────────┘
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
     ┌────────────────┐                 ┌────────────────┐
     │  SFP trader    │                 │ Fast reactor   │
     │ (priority 1)   │                 │ (priority 2)   │
     ├────────────────┤                 ├────────────────┤
     │ • SFP at fib   │                 │ • momentum     │
     │   confluence   │                 │ • whale flow   │
     │ • intel gate   │                 │ • intel gate   │
     │ • sygSFP       │                 │ • sygFAST      │
     │ • rare fires   │                 │ • casual moves │
     │ • 30min cool   │                 │ • fib-zone veto│
     └────────┬───────┘                 └────────┬───────┘
              │                                  │
              └───────────────┬──────────────────┘
                              ▼
                  ┌──────────────────────┐
                  │   strategy_claim     │
                  │   • SFP open → fast  │
                  │     reactor stands   │
                  │     down on perps    │
                  │   • Fast reactor's   │
                  │     own positions    │
                  │     don't block SFP  │
                  │     (SFP preemption  │
                  │     not implemented; │
                  │     SFP just waits)  │
                  └──────────┬───────────┘
                             ▼
                  ┌──────────────────────┐
                  │   bybit-mcp vault    │
                  └──────────────────────┘
```

## Mutex semantics

| Scenario | Outcome |
|---|---|
| No open positions, SFP fires | SFP opens, claims slot, fast-reactor blocked on perps until SFP closes |
| No open positions, fast-reactor fires | Fast-reactor opens, claims slot. **SFP does NOT preempt** — it waits for fast-reactor to close before it can fire. |
| Fast-reactor open + SFP signal arrives | SFP skips this fire (logged), waits for next. (Future enhancement: SFP preemption if signal quality > threshold.) |
| SFP open + new SFP signal | SFP skips (cooldown 30min anyway, claim mutex enforces single position) |
| SFP open + fast-reactor signal | Fast-reactor blocked with `sfp_priority:sfp_open:<olid>` reason in log |

## Hard kill-switch

The SFP trader ships **disabled by default**. Three guards:

1. `SYGNIF_SFP_TRADER_ENABLED=0` (env, default 0) — daemon refuses to
   place orders. It still observes signals and logs them.
2. `SYGNIF_SFP_DRY_RUN=1` (env, default 1) — even with ENABLED=1, won't
   actually call Bybit V5 unless DRY_RUN=0.
3. The systemd unit `sygnif-sfp-trader.service` is **disabled** by
   default. `systemctl enable --now` won't run it.

This is intentional: per PR #15's 30-day backtest, the SFP signal has
negative net-EV on BTC 1m. Don't enable until one of these is shipped:

- Regime filter (only fire in range-bound conditions)
- Inverted directionality (SFP as breakout confirmation, not mean-rev)
- Higher-timeframe Fib (1h / 4h instead of 1m × 240)
- Trailing-stop exit (mirror trailing-daemon)
- Intel-confluence ≥ 2 boosts before firing

## What fast-reactor changes (small patch)

`fast_reactor_coordination.patch` adds two minor gates:

1. **SFP-priority mutex check** — before any fire (momentum, whale, or
   the now-dead bounce), check `strategy_claim.json` for an open SFP
   position. If found → reject with `sfp_priority:sfp_open:<olid>`.
2. **Fib-zone context veto** — read the rolling 240-bar fib levels.
   Reject `momentum` and `whale` longs above fib_0.786 (overextended),
   shorts below fib_0.236 (capitulation). This is fib-as-CONTEXT, not
   fib-as-signal. SFP-the-signal is exclusively the SFP trader's domain.

Fast-reactor's existing whale + momentum triggers are unchanged
otherwise. The dead `bounce` trigger stays preserved (function intact,
not called from on_message — see SYGNIF.md §4.6).

## Deploy steps (when ready, NOT YET)

```bash
# 1. Stage the new daemon
sudo cp experiments/sfp_trader/sygnif_sfp_trader.py \
        /opt/sygnif-services/sygnif_sfp_trader.py
sudo cp experiments/sfp_trader/fib_sfp_trigger.py \
        /opt/sygnif-services/fib_sfp_trigger.py

# 2. Install systemd unit (still disabled)
sudo cp experiments/sfp_trader/sygnif-sfp-trader.service \
        /etc/systemd/system/sygnif-sfp-trader.service
sudo systemctl daemon-reload
sudo systemctl enable sygnif-sfp-trader.service   # enables at boot
sudo systemctl start  sygnif-sfp-trader.service   # starts in scaffold mode

# 3. Apply the fast-reactor coordination patch
sudo cp /opt/sygnif-services/sygnif_fast_reactor.py \
        /opt/sygnif-services/sygnif_fast_reactor.py.pre-sfp-coord-$(date +%Y%m%d)
sudo patch /opt/sygnif-services/sygnif_fast_reactor.py \
        < experiments/sfp_trader/fast_reactor_coordination.patch
sudo systemctl restart sygnif-fast-reactor.service

# 4. Verify
journalctl -u sygnif-sfp-trader.service -n 50 --no-pager
journalctl -u sygnif-fast-reactor.service -n 50 --no-pager
ls -la /var/lib/sygnif/strategy_claim.json   # mutex file
```

Watch `[SFP] FIRE BLOCKED: disabled` in the SFP log — that's the daemon
operating correctly in scaffold mode. To actually start trading SFP
(once a viable variant is found):

```bash
# Set in /etc/sygnif/sfp-trader.env (root:ubuntu mode 640):
#   SYGNIF_SFP_TRADER_ENABLED=1
#   SYGNIF_SFP_DRY_RUN=0
sudo systemctl restart sygnif-sfp-trader.service
```

## Rollback

Stop the SFP trader, revert the fast-reactor patch:

```bash
sudo systemctl stop sygnif-sfp-trader.service
sudo systemctl disable sygnif-sfp-trader.service
sudo cp /opt/sygnif-services/sygnif_fast_reactor.py.pre-sfp-coord-<ts> \
        /opt/sygnif-services/sygnif_fast_reactor.py
sudo systemctl restart sygnif-fast-reactor.service
```

The SFP module itself is read-only context; nothing else imports it,
so leaving the files in `/opt/sygnif-services/` is harmless.

## Why ship this if the signal doesn't work yet

The user's framing: *"separate the SFP and the fast-reactor fib-res. if
SFP trader gets signal let him trade and have only open trade. if market
is just casually moving let the fast reactor trade."*

What ships here:
- ✅ The architectural separation (two distinct daemons, mutex-coordinated)
- ✅ The prefix discipline (`sygSFP` vs `sygFAST` — clean attribution)
- ✅ The hard kill-switch (disabled by default, env-gated)
- ✅ The fib-context veto on fast-reactor (uses fib as a guard, not as signal)
- ❌ NOT a profitable SFP signal — that's a separate concern (see PR #15
     for the failure analysis + 5 follow-up directions)

When someone develops a viable SFP variant (regime-filtered, HTF, intel-
confluent, or inverted), the wiring is in place. Just swap in the new
`evaluate()` logic inside `FibSfpState` and flip the kill-switch.
