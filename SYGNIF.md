# SYGNIF — System Specification (canonical)

> Single source of truth for every Claude / agent working on SYGNIF.
> Edit only this file. `bin/sync-docs.sh` propagates the body to
> `AGENT.md`, `CLAUDE.md`, and the Windows execution-layer `CLAUDE.md`.

## 1. System architecture

SYGNIF is a three-process trading + research system spread across two boxes
plus an external Bybit demo / mainnet account.

```
┌──────────────────────────── X1 (Lenovo Yoga, Windows + WSL) ────────────────────────────┐
│                                                                                          │
│  • sygnif-x1-mcp / sygnif-bybit-mcp / sygnif-commander-mcp  (HTTP MCP servers)           │
│  • sygnif-bee-tunnel        (Swarm / Bee mainnet permanence)                             │
│  • sygnif-trader (author)   SYGNIF_TRADER_NO_EXECUTE=1 — plans only, no orders           │
│  • sygnif-dashboard / -v2   (read-only insights)                                         │
│  • master swarm.db          /var/lib/sygnif/swarm.db (group sygnif-users)                │
│  • sygnif-letscrash         boot bringup + EC2 sync (mode=bootup|refresh|sync|crashtest) │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                                ↕ Tailscale mesh (private)
┌────────────────────────── EC2 eu-central-1 (m7i-flex.large) ────────────────────────────┐
│                                                                                          │
│  • sygnif-trader (executor)  SYGNIF_ORDERS_MODE=demo — perp + options to api-demo.bybit  │
│  • sygnif-neurolinked        :8889  3000-neuron Izhikevich brain + STDP                  │
│  • sygnif-brain-insights     :8890  read-only HTTP dashboard (no GIL contention)         │
│  • sygnif-bybit-mcp          MCP for read-only bybit tape, options, positions            │
│  • sygnif-discovery.timer    every 30 min — regime / IV / GEX / max-pain snapshot        │
│  • sygnif-predict.service    every 5 min — RF/XGB/LogReg ensemble TREND_* classifier     │
│  • sygnif-swarm-x1-mirror.timer  ships swarm rows EC2 → X1 every 2 min                   │
│  • freqtrade containers      (legacy spot/futures via SygnifStrategy / MarketStrategy)   │
│  • trade_overseer            Telegram commentary, rules-only fallback (extending below)  │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                                ↕ Bybit V5 (api-demo.bybit.com)
┌──────────────────────────── Bybit demo (UTA) ───────────────────────────────────────────┐
│                                                                                          │
│  Equity ≈ $2,000        Per leg sized via portfolio.demo                                 │
│  Open structures: iron condor (theta harvest), bull/bear spreads (directional)           │
│  Live mainnet wallet exists but is essentially empty ($0.014) — chart-only TV target     │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

**X1 = brain / author.** EC2 = executor. X1 stops, EC2 keeps trading. EC2 stops,
X1 keeps learning.

## 2. Hosts, paths, and services (verified)

| Concept | Path / Endpoint | Notes |
|---|---|---|
| Canonical agent code | `~/sygnif/sygnif-agent/` (X1) | Git-versioned, this doc lives here |
| Trader code on EC2 | `/home/ubuntu/sygnif-agent-mirror/` | Mirror of X1 agent; trader runs from here |
| EC2 trader entry | `/opt/sygnif/.venv/bin/python -m agent.loop --daemon` | systemd `sygnif-trader.service` |
| EC2 trader env | `/etc/sygnif/trader.env` | Bybit demo + live keys, root:ubuntu mode 640 |
| EC2 brain code | `~/SYGNIF/third_party/neurolinked/` (real, git-versioned) | symlinked from `~/sygnif-swarm/BTC_Prediction/third_party/neurolinked` |
| EC2 brain state | `~/SYGNIF/third_party/neurolinked/brain_state/` | regions / synapses / knowledge.db / live.json (1-Hz flush) / backups |
| EC2 brain venv | `/home/ubuntu/sygnif-swarm/.venv/` | Has model2vec / sentence-transformers when installed |
| Master swarm | `/var/lib/sygnif/swarm.db` (X1) | Group `sygnif-users` rw; mirror lands EC2 rows here |
| EC2 swarm | `/var/lib/sygnif/swarm.db` (EC2) | Owned ubuntu:ubuntu; X1 reverse-push lands here |
| Discovery snapshot | `~/SYGNIF/prediction_agent/neurolinked_swarm_channel.json` | Live, NL service writes |
| Predict snapshot | `~/sygnif-swarm/BTC_Prediction/prediction_agent/...` | Predict-loop writes here (path divergence — pending unification) |
| Tailscale identities | gtaura (Win), thinkx1 (X1), sygnif-ec2 | id_ed25519 in authorized_keys both ends |
| Tailscale-SSH | DISABLED on X1 since 2026-05-05 | Plain OpenSSH key auth — no browser re-auth |

## 3. Trading doctrine

### 3.1 Position sizing (`agent/expertise.py:SIZING`)

```python
default_risk_pct          0.5    # base equity per perp trade
option_default_risk_pct   1.0    # base equity per option position
default_perp_stop_pct     1.0    # default stop distance
max_concurrent_open       7      # hard cap (lowered from 10 on 2026-04-28)
max_concurrent_per_side   3      # × 1.7 in TREND regime via tuner
min_equity_to_trade_usdc  100

max_risk_pct_default      1.5    # ceiling without size_tier flag
max_risk_pct_high_conf    6.0    # ceiling with size_tier="long_term_conf"
long_term_conf_multiplier 8.0    # boost applied to base when tier set
```

### 3.2 Leverage doctrine (`agent/expertise.py:PERP_SAFETY`, two-tier as of 2026-05-04)

```python
min_leverage             2.0     # floor — sub-2× perps add no edge over spot, reject
max_leverage_default     10.0    # default ceiling for any plan
max_leverage_high_conf   30.0    # ceiling with leverage_tier="high_conf_short_hold"
preferred_leverage       5.0     # auto-set when account exceeds active cap
btcusdt_mm_rate          0.005   # tier-1 maintenance margin
min_liq_buffer_bps       8.0     # G5 — SL must trigger ≥ 8bps before liq price
```

**Tier flags** (set by planner, default = neither):
- `plan["leverage_tier"] = "high_conf_short_hold"` → unlocks 10–30× (intent: short hold + high-conf bounce after big move)
- `plan["size_tier"]     = "long_term_conf"`        → unlocks 1.5–6% risk (intent: validated repeatable pattern)

Without tier flags, plans are capped at the default values. **As of 2026-05-05 the
planner does NOT yet emit tier flags** — observability via `tier_candidates` lives
in the `plan_authored` heartbeat for backtest analysis (Phase 1 of staged tier rollout).

### 3.3 Regime classification — two parallel sources

| Source | Cadence | Method | Output |
|---|---|---|---|
| `discovery.read` | 30 min via `sygnif-discovery.timer` | ATR-percentile bucketing | NORMAL / TREND_UP / TREND_DOWN / RANGE / HIGH_VOL_SHOCK |
| `predict_loop` (forecast topic) | 5 min via `sygnif-predict.service` | RF + XGB + LogReg ensemble | TREND_UP / TREND_DOWN / UNKNOWN / HIGH_VOL_SHOCK |

`agent.trade.plan` reads discovery as primary. Since 2026-05-05 the planner
ALSO consults predict_loop: when discovery is non-committal (NORMAL / UNKNOWN)
**and** predict_loop has been TREND_UP or TREND_DOWN for 3+ consecutive
forecasts (~15 min), predict_loop's label promotes. Audit field
`plan["context"]["regime_origin"]` = `"discovery"` or `"predict_loop_confirmed_<N>"`.

### 3.4 Pre-flight gates (every order routes through)

```
PERP                                       OPTION
G4 leverage cap        (tier-aware)        IV staleness gate (1800s threshold)
G5 liq buffer          (≥ 8 bps)           Strike-distance + DTE-band gates
Funding blackout       (±5min @ 0/8/16Z)   Liquidity (bid_ask_invalid → reject)
Kill list              (env opt-out)       Doctrine gate (gamma flip, IV regime, RR)
Regime gate            (per-scanner)       Equity / per-side cap (shared)
Equity / per-side cap                      Daily-trade cap (shared)
Daily-trade cap        (default 60)
```

### 3.5 Active scanners (perp side, post-migration)

| Scanner | Side | Regime gate | Status |
|---|---|---|---|
| `scan_swing_failure` | LONG only | any (kill-list filters short) | Live — workhorse, +$118 / +0.42 R in 31d backtest |
| `scan_bos` | LONG only | TREND_UP only | Live, currently regime-blocked unless override fires |
| `scan_psych_barrier_fade_short` | SHORT | NOT TREND_UP | Live, env-gated |
| `scan_psych_barrier_bounce_long` | LONG | any | **Shadow mode** (records, doesn't trade) |

### 3.6 Portfolio source (post-2026-05-05 paper-purge)

Trader loop reads `portfolio.demo` neuron, NOT `order.paper.portfolio`.
The neuron aggregates Bybit-demo wallet + perp positions + option positions
into `paper.portfolio()` shape. `n_order_paper_portfolio` and the 9 other
paper neurons remain registered for legacy callers but the trader-loop
no longer depends on a journal file.

## 4. Trading mechanics — entry & exit ownership

Doctrine (§3) is about *when* to trade. This section is about *who*
trades. The system has accumulated many trade-placing daemons over
time; each new one introduces attribution ambiguity and risk of
conflicting orders against the same blended Bybit UTA position.
**As of 2026-05-13 the canonical state is enforced:**

### 4.1 Authorized lifecycle paths

| Asset class | Open | Manage / Close | orderLinkId prefix |
|---|---|---|---|
| BTC perpetual | `sygnif-fast-reactor.service` | `sygnif-trailing-daemon.service` | `sygFAST<cid14>` |
| Options (theta + directional) | `sygnif-trader.service` (`agent.loop`) | `sygnif-trader.service` | `sygOL` / `sygCS` |

Nothing else may open positions. Adding a new opener requires the
protocol in §4.4.

### 4.2 Explicitly disabled openers (and why)

| Service / timer | Prefix | Reason disabled |
|---|---|---|
| `btc-predict-runner.timer` | `sygPL` | Bled **$253 over 7 days** at 50× leverage / $100k notional / 1-min ticks, 14% win rate. Stopped 2026-05-13 00:42 UTC. See `postmortem` topic in `swarm.db`. |
| `sygnif-standing-orders.timer` | `sygSTND` | Bracket conditional pairs every 5 min, never filled in normal regime, polluted order book + closed-pnl record. Timer stopped 2026-05-13 12:29 UTC. |
| `sygnif-perp-runner.service` | `perpRun` | Scanner-driven perp executor — overlapping responsibility with fast-reactor. Disabled 2026-05-13. |
| `sygnif-funding-harvester.service` | (TBD) | Funding-rate arbitrage strategy — held until a `strategy_claim` slot is built so it can't fight fast-reactor for the same symbol+side. |
| `sygnif-bounce-watcher.service` | `sygBNCE` | Replaced by fast-reactor's intel-gated path. |
| `sygnif-training-scanner.timer` | `sygTRN` | Training-mode scanner not actively in use. |
| `sygnif-bybit-daemon.service` | `sygRT` | Old action-executor pattern, replaced by fast-reactor. |
| `sygnif-trailing-manager.service` | n/a | Replaced by `sygnif-trailing-daemon`. |

### 4.3 Perp lifecycle (the only active perp path)

```
1. PRE-FLIGHT GATES — checked by fast-reactor before every fire_trade()
   • intel_summary.json fresh (< 5 min)        (read_intel mtime cache)
   • No conflicting strategy_claim entry on (symbol, side)
   • M5 momentum veto not active
       block long  if m5 ≤ -1.5 %  (catching-falling-knife)
       block short if m5 ≥ +1.5 %  (chasing pump)
   • Funding blackout: ±5 min around 0/8/16 UTC
   • Daily-trade cap (default 60)
   • Equity > $100

2. INTEL CHECK — check_intel_for_direction(direction)
   • vetoes_<direction> non-empty  →  REJECT, log intel_veto reason
   • boosts_<direction> present    →  conf_modifier = 1 + 0.1 × n  (cap 1.5)
   • Otherwise                     →  conf_modifier = 1.0

3. OPEN — fire_trade()
   • bybit-mcp vault places Market order
   • orderLinkId = "sygFAST" + cid[:14].replace("-", "")
   • strategy_claim.acquire(owner="fast-reactor", kind, entry, tp, sl, olid)

4. MANAGE — sygnif-trailing-daemon
   • watches every fast-reactor opened position
   • 0.1 % activation distance, 0.1 % trail distance
   • reduce-only market close on trigger
   • emit trade.close + outcome.attributed to swarm.db

5. CLOSE paths
   • trail fires           (most common, ~80 % of exits)
   • M5 reversal           (manual fast-reactor exit, ~15 %)
   • position rides        (~5 %, trail never triggered)

6. ATTRIBUTE — sygnif-outcome-per-trade
   • reads orderLinkId
   • decomposes realised PnL into 7 components (signal, entry_slip,
     exit_slip, fee, funding, adverse_selection, residual ≤ $0.01)
   • toolkit lives in experiments/sygnif_toolkit/edge_attrib/
```

### 4.4 Adding a new order-placing daemon — protocol

If a future strategy needs its own opener (e.g. funding harvester,
mean-reversion scanner):

1. **Fresh prefix** — must be unique. Add to §4.1 + §3 + AGENTS.md.
2. **`strategy_claim` integration** — call `acquire()` before any
   order; `release()` on close. Mutex prevents stomping on
   fast-reactor's positions on the same symbol+side.
3. **Route through bybit-mcp vault** — do NOT call Bybit V5 directly.
4. **Same pre-flight gates** as fast-reactor: intel, funding blackout,
   daily-trade cap, M5 veto.
5. **Backtest evidence** in PR: ≥ 30 d window, ≥ 40 % win rate,
   ≥ 5 fires/week, expectancy > $0/trade.
6. **Until merged, the daemon stays disabled.**

### 4.5 What ran recently (2026-05-13 audit window)

| Period | Source | Trades | Net |
|---|---|---|---|
| 7d before kill | `sygPL` (btc-predict-runner) | 98 | **−$238.67** (the bleeder) |
| 7d before kill | other | 0 | $0 |
| Post-kill (12h) | `sygFAST` (fast-reactor) | 2 | **−$23.27** (both losing longs at $82k as BTC fell to $80.2k) |
| Post-kill (12h) | `sygSTND` (standing-orders) | 0 fills | placed/cancelled 5+ bracket pairs/h until timer killed |

Fast-reactor's recent fires lost money — not because of intel
(intel was correctly bullish), but because the entries were placed
at a local high right before a $2k pullback. This is signal-noise,
not architectural; the intel-veto path successfully rejected the
shorts that would have been ratio'd by the same pullback.

### 4.6 fast-reactor enhancement attempt (2026-05-13, FAILED)

A design study proposed adding two new signal sources to fast-reactor:

1. **`fib_sfp_confluence` trigger** — bull SFP at fib_0.618 + 12 h trend
   intel boost + 5-bar dedup, intended to replace the dead `bounce`
   trigger.
2. **Lowered momentum thresholds** — drop `TRIGGER_MOMENTUM_PCT` 0.4 → 0.2
   and `TRIGGER_MOMENTUM_VOLX` 1.5 → 1.2, since current defaults fired
   0 times in 7 d.

Wet backtest on 7-day BTCUSDT 1 m data **failed acceptance gates** on
both proposals:

| Design | Fires/wk | WR | EV gross | EV net (0.10% fees) | Pass? |
|---|---|---|---|---|---|
| fib_sfp_confluence | 30.2 | 45.5% | −0.041% | **−0.141%** | ✗ |
| Lowered momentum (0.2% / 1.2×) | 27 | 27.6% | −0.091% | **−0.191%** | ✗ |
| TP/SL sensitivity sweep (8 combos) | — | best 57.6% | best 0.007% | best −0.093% | ✗ |

**Root cause**: round-trip taker fees of 0.10 % swamp the raw SFP edge
(+0.017 % EV pre-fees) by 6 ×. No parameter combo recovers positive
net-EV. Also, SFP fires at the end of pullbacks where short-term trend
filters reject it, and longer-term trend filters fail to discriminate
good SFPs from bad.

Code, tests, backtests and alternatives in
`experiments/fast_reactor_v2/`. **No changes deployed to EC2.**
Alternatives to revisit: limit-order entries (drops fee to 0.02 %),
SFP as confirmation-inside-whale-trigger, or 5 m bar timeframe.

The current state stands: `bounce` trigger remains in code (dead but
preserved for re-enablement), `momentum` trigger remains at 0.4 % /
vol×1.5 defaults, `whale` trigger active, intel-veto wired in.

## 5. Brain (NeuroLinked, EC2)

3000-neuron Izhikevich spiking network with STDP synaptic plasticity.
Region distribution at the running config (`brain/regions.py`):

| Range | Region group | Used for |
|---|---|---|
| 0–1000 | vision | unused for trading |
| 1000–2000 | audio | unused for trading |
| 2000–3000 | touch | text input encodes here today |
| (planned) 3000–4000 | language | reserved for Phase 2 of language plan, see §7 |

11 logical regions overlay this layout: sensory_cortex / motor_cortex /
association / hippocampus / prefrontal / cerebellum / brainstem /
concept_layer / feature_layer / predictive / reflex_arc.

**Live state files** (`brain_state/`):
- `meta.json` (every ~7 min on persistence save) — step_count, dev_stage, neuromodulators
- `regions/*.json` (every ~7 min) — per-neuron v / u / binding_strength
- `synapses/*.npz` (every ~7 min) — STDP weights between regions
- `knowledge.db` (sub-second) — 50k+ entries, live ingest
- `live.json` (every 1 s, added 2026-05-04) — step_count + sps + neuromodulators flushed by patched brain.step()
- `backups/` (every 7 min) — full state snapshot, kept ~10 deep

**Dashboards**:
- `:8890/` — read-only insights (this session's v2: 3D Three.js + live BTC tape + Hz)
- `:8890/original/` — GitHub master Developer Portal UI proxied through the same service (WS bypass for the GIL-locked port 8889)

## 6. Communication / sync

**EC2 → X1 master swarm** — `sygnif-swarm-x1-mirror.timer` ships rows every 2 min
via `swarm_write` JSON-RPC to X1's `:9001/rpc`. Cursor at `/var/lib/sygnif/x1-mirror.cursor`.

**X1 → EC2 reverse push** — `sygnif-letscrash` step [8/8] ships X1-author rows
(letscrash digests, plan_authored heartbeats, agent.review.*) to EC2's swarm
via SSH + sqlite3 INSERT OR IGNORE on the row PK. Idempotent over a 10-min
overlap window. Run on demand (`sygnif-letscrash sync` or as part of bootup/refresh).

**Topic conventions** (master swarm):
- `trader.heartbeat` — every cycle from any trader (X1 or EC2), agent_id `sygnif-trader-loop`
- `trade.open` / `trade.close` — execution events
- `forecast` — predict_loop signal (every 5 min, agent_id `sygnif-predict`)
- `regime` — discovery snapshot tagged
- `agent.review.ec2_trades` — letscrash daily digest of EC2 activity for X1 to ingest
- `agent.commentary` — (planned, §7.3) brain-narrated market state

## 7. Language commentary plan (in flight)

Goal: SYGNIF narrates trades + market state without an LLM dependency. Brain
provides salience + association; templated layer provides voice. Future-proof
for "more diverse agent" ambitions through additive expansion of producer
hooks, salience labels, and template families.

Architectural rule: brain owns *what to say about*; templates own *how to say it*.

### 6.1 Phase 1 — text ingest (1–2 days)

```
+ sensory/language.py            Model2Vec (potion-base-8M) → np.array(256)
                                  Cached on disk, no API, ~10MB.

+ server.py /api/input/text       POST {text, source}. Calls
                                  brain.inject_sensory_input("text", encoded,
                                  executive_boost=...).

+ Producer hooks:
   - trade_overseer/event_log.py  on every trade.open / trade.close / threshold_cross
                                  → POST text="TRADE {kind} {label} px=${px} pnl=${pnl}"
   - sygnif-discovery              on regime change → POST text="REGIME {old}→{new} ..."
   - sygnif-predict                on STRONG_* signal → POST text="PREDICT {signal} vote={n}"
   - X1 brain ingest hook (already partly wired via sygnif-trade-nl-publisher.py)
```

After 1 week of running: knowledge.db has thousands of trade/market events
with associated source labels. Synaptic associations form between event types
and outcomes.

### 6.2 Phase 2 — salience + composer (split: 2.A brain, 2.B DLP)

**Phase 2.B is documented in detail at `docs/DLP_PLAN.md`** — the
Deterministic Language Program. It owns templates, slot resolution,
precedent retrieval, and the commentary daemon. Read that doc before
shipping any commentary code.

Phase 2.A (brain salience head) and Phase 2.B (DLP) are independent —
DLP is brain-agnostic and ships first with rule-based triggers. Brain
plugs in later as another event source without changing DLP.

**Phase 2.A — brain salience head (gated, ships when DLP is stable):**

```
+ brain/output_classifier.py     Pick ~50 association/concept_layer neurons as
                                  the "salience head". Sample activation rate
                                  every 5 s. Map via small linear projection
                                  (start uniform, refine via STDP-correlated
                                  outcomes) to label ∈
                                  {routine, alert, noteworthy, risk_event,
                                   trend_change, win, loss, explain}.

+ brain/templates/               Versioned template library. Examples:
   routine_NORMAL.tpl            "Box {lo}-{hi} held {min}min. {n}/{cap}
                                  legs harvesting θ. Net uPnL {pnl:+}, eq ${eq}."
   alert_TREND_UP.tpl            "BTC broke {prev_high}. Regime → TREND_UP
                                  after {chop_min}min. {at_risk_n} legs near
                                  short strikes. Predict: {confidence}-vote."
   risk_event.tpl                "Short {strike} call now {bps} bps from spot
                                  (was {start_bps} at open). Theta no longer
                                  beating delta — consider {recommended}."
   explain_open.tpl              "Opened {structure} {expiry}. Thesis: {thesis}.
                                  Strikes via doctrine: {rule_chain}. Risk
                                  capped at ${max_loss}. Past similar: {n_sim}
                                  (avg ${avg_pnl})."
   outcome_close.tpl             "Closed {label} at ${pnl}. {reason}."

+ brain/response_composer.py     salience_label + current state →
                                  fill template → emit string.
                                  Always grounded in real numbers from
                                  portfolio.demo + discovery.read +
                                  knowledge.db nearest-neighbor recall.
```

### 6.3 Phase 3 — delivery (1 day)

```
+ Wire response_composer.compose() output to:
   - trade_overseer's commentary slot (replace ANTHROPIC_API_KEY path
     with OVERSEER_AGENT_URL=http://localhost:8889/api/commentary)
   - brain insights v2 dashboard (live commentary panel)
   - swarm topic agent.commentary (archival, agent learning)
   - Telegram via sygnif-hedge-bot
```

### 6.4 Explicitly out of scope

- Fluent natural-language generation (would require a transformer; not fixable
  by adding language neurons)
- Multi-turn dialog (this is a NARRATOR, not a chatbot)
- Novel theorising about hypothetical futures (templates can't synthesise;
  retrieval can only echo precedent)

If those become required later, route through a separate post-processor
(tiny LM rephraser or LLM gateway) — do **not** rebuild the brain to attempt them.

### 6.5 Path divergence to fix in Phase 1

```
~/SYGNIF/prediction_agent/neurolinked_swarm_channel.json                       (NL writer)
~/sygnif-swarm/BTC_Prediction/prediction_agent/neurolinked_swarm_channel.json  (predict-loop writer)
```

Symlink the latter to the former (or vice versa) before adding
`neurolinked_language_channel.json` so consumers don't have to learn two
paths. Decide canonical = `~/SYGNIF/prediction_agent/`.

## 8. Operations

### 7.1 SSH topology (verified 2026-05-05)

| Alias | Resolves to | User | Notes |
|---|---|---|---|
| `x1` / `thinkx1` | 100.71.122.115 (Tailscale) | g2thek | OpenSSH key auth, Tailscale-SSH disabled |
| `x1-magic` | thinkx1.tailff64b5.ts.net | g2thek | MagicDNS variant |
| `ec2-eu1` / `ec2-eu` / `freqtrade` | 3.64.28.14 (EIP) | ubuntu | Permanent Elastic IP — survives stop/start |

### 7.2 Bringup / health (`sygnif-letscrash`)

```bash
# X1 boot bringup, also runs daily at 14:00 CEST in refresh mode
~/sygnif/sygnif-agent/cli/sygnif-letscrash bootup    # default
~/sygnif/sygnif-agent/cli/sygnif-letscrash refresh   # bootup + bounce channeler
~/sygnif/sygnif-agent/cli/sygnif-letscrash sync      # only the EC2 trade-sync step
~/sygnif/sygnif-agent/cli/sygnif-letscrash crashtest # chaos drill

# 8-step pipeline:
# [1] wait tailscale [2] services [3] mode-specific [4] probes
# [5] EC2 SSH [6] EC2→X1 mirror freshness [7] EC2 trade digest
# [8] X1→EC2 reverse push (X1 plans → EC2 swarm)
```

### 7.3 EC2 deploy (legacy execution-layer, freqtrade containers)

```bash
aws ec2-instance-connect send-ssh-public-key \
  --instance-id i-0cd5389584d70a7fc --instance-os-user ubuntu \
  --ssh-public-key file://~/.ssh/id_ed25519.pub --region eu-central-1

ssh ubuntu@3.64.28.14 "cd ~/sygnif && git pull && \
  docker compose restart freqtrade freqtrade-futures trade-overseer"
```

### 7.4 Common queries

```bash
# Recent trader cycles (master swarm)
ssh x1 'sqlite3 /var/lib/sygnif/swarm.db \
  "SELECT datetime(created,\"unixepoch\"), agent_id, substr(content,1,140) \
   FROM swarm_entries WHERE topic=\"trader.heartbeat\" \
   ORDER BY created DESC LIMIT 10"'

# Plans authored on X1 (post-NO_EXECUTE) with tier candidates
ssh x1 'sqlite3 /var/lib/sygnif/swarm.db \
  "SELECT datetime(created,\"unixepoch\"), \
          json_extract(meta,\$.actions[0].tier_candidates) \
   FROM swarm_entries WHERE topic=\"trader.heartbeat\" \
   AND content LIKE \"%plan_authored%\" \
   ORDER BY created DESC LIMIT 20"'

# EC2 trader health / live brain state
ssh ec2-eu1 'systemctl is-active sygnif-trader sygnif-neurolinked sygnif-brain-insights'
ssh ec2-eu1 'cat /home/ubuntu/SYGNIF/third_party/neurolinked/brain_state/live.json | jq .'
```

### 7.5 Backups + reversibility

Every patch lands with a timestamped or `.pre-<change>` backup. To revert any
change, restore the backup file and `systemctl restart` the affected service.

| Service | What restart picks up |
|---|---|
| `sygnif-trader.service` (X1, EC2) | code in agent/, sygnif_neurons.py |
| `sygnif-neurolinked.service` (EC2) | brain code (state preserved if neuron count unchanged) |
| `sygnif-brain-insights.service` (EC2) | dashboard code |
| `sygnif-bybit-mcp.service` (EC2, X1) | MCP code |
| `sygnif-discovery.timer` (EC2) | sygnif_predict.py + discovery_pass.py |

## 9. Important rules (apply to every agent / change)

1. **Real data only.** Never fabricate prices, indicators, equity values, or P&L. Pull from Bybit / discovery / portfolio.demo.
2. **Timestamp everything.** Reports lead with UTC timestamp.
3. **No live execution without explicit user confirmation** in the chat. Demo path requires `confirm: True`; live mainnet path additionally requires `SYGNIF_ORDERS_LIVE=clear-for-live`.
4. **Tier flags MUST be set by the planner**, never hand-injected by tools. The whole point of two-tier sizing is the planner justifies why each request is `default` vs `high_conf_short_hold` / `long_term_conf`. If the planner can't justify, the trade is default-tier.
5. **WAIaaS triple-gate** — every mutating chain neuron requires `confirm: True` AND `i_understand_real_money: 'yes'`. Do not bypass for "convenience".
6. **Edits to brain code go to `~/SYGNIF/third_party/neurolinked/`** — both deployment paths symlink there. Restarting `sygnif-neurolinked.service` picks them up.
7. **Edits to trader code on EC2** go to `/home/ubuntu/sygnif-agent-mirror/`. X1's canonical agent at `~/sygnif/sygnif-agent/` should mirror — if they diverge, the mirror is wrong and needs sync.
8. **Implementation tax is real.** 2026-04-29 lesson: 8-min decision-to-action on a 2-leg orphan strangle bled −$29.40 (~72% of paper UPL). Pre-arm close brackets at open, mid-cross limits not panic-cross, combo orders for multi-leg, track per-trade slip as a first-class KPI.
9. **GitNexus pre-edit checks apply** — before modifying a function, run `gitnexus_impact({target})`. See §10.
10. **Sync this doc.** Editing AGENT.md / CLAUDE.md directly will be overwritten on next `bin/sync-docs.sh` run. Edit `SYGNIF.md` (this file).

## 10. Code-intelligence contract (GitNexus)

Index name: **sygnif** (~589 symbols, ~1421 relationships, ~47 execution flows).
Stale after commits — re-run `npx gitnexus analyze` (PostToolUse hook handles
this for Claude Code; manual elsewhere).

| Tool | When |
|---|---|
| `gitnexus_query({query})` | find code by concept |
| `gitnexus_context({name})` | 360° view of one symbol |
| `gitnexus_impact({target, direction:"upstream"})` | blast radius BEFORE edit |
| `gitnexus_detect_changes({scope:"staged"})` | pre-commit scope check |
| `gitnexus_rename({symbol_name, new_name, dry_run:true})` | safe multi-file rename |

Risk ladder: d=1 WILL BREAK, d=2 LIKELY AFFECTED, d=3 MAY NEED TESTING.
Never edit without impact analysis. Never ignore HIGH/CRITICAL warnings.
Never rename via find-and-replace — use `gitnexus_rename`.

## 11. References

```
Trading reference        ~/sygnif/sygnif-agent/reference/{ta-indicators,
                                                          mathematical-foundations,
                                                          market-openings,
                                                          swing-failure,
                                                          trading-structure}.md

System operating spec    ~/sygnif/sygnif-agent/instruct.md   (humans)
                         ~/sygnif/sygnif-agent/instruct.file (agent / YAML)

Lessons learned          swarm.db topic="lessons" + Bee mainnet refs
                         (e.g. bzz://2cad327f96... — implementation-tax lesson)

Postmortems              swarm.db topic="postmortem" — root-cause notes per incident

Per-feature plan docs    ~/sygnif/sygnif-agent/docs/*.md
                         (this file's Phase plans get sub-docs as they ship)
```

---

*Last updated: 2026-05-05. Edit this file, then run `bin/sync-docs.sh`.*
