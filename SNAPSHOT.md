# SYGNIF — System Snapshot 2026-05-13

This commit is a **point-in-time backup** of the entire SYGNIF stack as
deployed and running on 2026-05-13. The remote `main` branch is stale
(legacy freqtrade-only execution layer); this branch captures the
**current production system** which has grown well beyond that.

## What's in here

```
.                                ← X1 (Windows) execution-layer repo
├── SygnifStrategy.py            ← Freqtrade spot strategy (still live)
├── user_data/                   ← Freqtrade configs + journal dir
├── docker-compose.yml           ← 4-container freqtrade stack
├── trade_overseer/              ← Telegram commentary + NPU LLM hooks
├── finance_agent/               ← Briefing + strategy router (host:8091)
├── docs/                        ← AWS SSM, backtest parity, EC2 workflows
├── CLAUDE.md / SYGNIF.md        ← Architecture canonical docs
│
└── ec2-snapshot/                ← NEW — full snapshot of what's on EC2
    ├── services/                ← /opt/sygnif-services/  (46 daemons)
    ├── systemd/                 ← /etc/systemd/system/sygnif-*.service
    ├── neurolinked/             ← Brain code (NO state files — 1.9GB stripped)
    └── trader/                  ← agent/ + instruct + mcp_servers
```

## What's NOT in here

- **Secrets**: `.env`, `trader.env`, `bybit-mcp.env`, `swarm_operator.env`.
  Service units reference these by path; they live on EC2 outside the repo.
- **Brain state**: `~/SYGNIF/third_party/neurolinked/brain_state/` (1.9 GB
  of regions, synapses, knowledge.db, live.json). Restoring this snapshot
  on a fresh box → empty brain that starts learning from zero.
- **Trading journals**: `~/sygnif-agent-mirror/data/*.db`, `journal/`.
  Same reason — these are runtime state, not code.
- **Venvs**: `/opt/sygnif/.venv/`, `~/openvino_env/`, `~/sygnif-swarm/.venv/`.
  Pip-installable from requirements files.
- **Logs**: `/var/log/sygnif/` — runtime.

## How to restore this snapshot on a fresh EC2 box

```bash
# 1. Clone
git clone https://github.com/Giansn/SYGNIF.git -b snapshot/2026-05-13 \
    /home/ubuntu/sygnif-bootstrap

# 2. Lay out code where the running system expects it
sudo mkdir -p /opt/sygnif-services && \
  sudo cp -r sygnif-bootstrap/ec2-snapshot/services/* /opt/sygnif-services/

sudo cp sygnif-bootstrap/ec2-snapshot/systemd/sygnif-*.service /etc/systemd/system/
sudo cp -r sygnif-bootstrap/ec2-snapshot/systemd/sygnif-*.service.d \
           /etc/systemd/system/

mkdir -p /home/ubuntu/SYGNIF/third_party && \
  cp -r sygnif-bootstrap/ec2-snapshot/neurolinked \
        /home/ubuntu/SYGNIF/third_party/

mkdir -p /home/ubuntu/sygnif-agent-mirror && \
  cp -r sygnif-bootstrap/ec2-snapshot/trader/* \
        /home/ubuntu/sygnif-agent-mirror/

# 3. Recreate envs / venvs / secrets (NOT in this repo, by design)
sudo mkdir -p /etc/sygnif && \
  echo "fill these in manually" | sudo tee \
    /etc/sygnif/trader.env /etc/sygnif/bybit-mcp.env

/opt/sygnif/.venv/bin/pip install -r \
    /home/ubuntu/SYGNIF/third_party/neurolinked/requirements.txt

# 4. Enable + start
sudo systemctl daemon-reload
sudo systemctl enable --now \
    sygnif-neurolinked sygnif-brain-insights \
    sygnif-intel-aggregator sygnif-fast-reactor \
    sygnif-market-brain-feed \
    sygnif-trade-nl-publisher sygnif-brain-context sygnif-bybit-nl-feed
```

## Architecture summary (snapshot day)

Three-process system across two boxes plus Bybit demo:

| Tier | Host | What |
|---|---|---|
| **Author** | X1 (Windows) | sygnif-trader (NO_EXECUTE), MCP servers, master swarm.db, dashboards |
| **Executor** | EC2 (eu-central-1) | sygnif-trader (demo orders), NeuroLinked brain (3000 Izhikevich neurons, 312k synapses, STDP), 17+ intel daemons, freqtrade containers |
| **Venue** | Bybit demo | UTA account ≈ $2k equity, perp + options |

### Intel layer (added since last commit on `main`)

| Daemon | Purpose |
|---|---|
| `sygnif_intel_aggregator` | Pre-digests 17 sources → 4KB `intel_summary.json` every 30s |
| `sygnif_fast_reactor` | Sub-second WS reactor, reads intel_summary with mtime-cached helper |
| `sygnif_chain_intel` | UTXO age, CIH clustering, peeling chains, CoinJoin, OFAC |
| `sygnif_onchain_watcher` | Mempool pre-confirmation whale detection |
| `sygnif_evm_signals` + `_extras` | Stablecoin mints, exchange reserves, bridge flows |
| `sygnif_ecosystem` | DefiLlama + CoinGecko + Goldrush portfolio tracking |
| `sygnif_market_premium` | Coinbase / multi-exchange premium spread |
| `sygnif_market_synth` | Composite signal generator (feeds brain) |
| `sygnif_market_brain_feed` | POSTs market_synth output to brain `/api/input/text` |
| `sygnif_trailing_daemon` | 0.1%/0.1% real-time perp trailing stop |
| `sygnif_news_feed` | Macro / regulatory news with tier classification |
| `sygnif_polymarket_feed` | Prediction-market sentiment (BTC targets) |
| `sygnif_hivemind_options` | Risk-neutral options probabilities |
| `sygnif_microstructure_feed` | Funding prediction, basis, OI deltas |
| `sygnif_outcome_attribution` | FIFO trade attribution per opener daemon |
| `sygnif_disk_janitor` | Hourly disk maintenance |
| `sygnif_daily_health_report` | Telegram daily digest |

### Brain (NeuroLinked) — current readout

- 3,000 neurons (Izhikevich), 11 logical regions
- 312,291 synapses with active STDP plasticity
- 81 M simulation steps in 40.2 h uptime (≈ 137 steps/s)
- Dev stage: MATURE (dopamine 0.55, serotonin 0.97)
- 67,516 model2vec-encoded memories in knowledge.db (176 MB)
- 8 publishers feeding it at hardened cadence (90s–900s, 120s POST timeout)

### What's stale on `main`

The following directories on the current `main` branch (last commit
`74c8c15 Wire swing-failure overlay into ORB session strategies`) are
legacy and **not part of the live system**:

- `dashboard*.html`, `dashboard_server*.py` — replaced by `brain_insights.py` on :8890
- `prediction_agent/` — superseded by EC2 `sygnif-predict.service`
- `letscrash/` (subdir) — replaced by `cli/sygnif-letscrash` binary on X1
- `training_pipeline/` — abandoned
- `live_site_snapshot/`, `snapshot2.html` — old captures
- `update_movers.py`, `tf_controller.py`, `tf_switch.py` — legacy

These remain in the tree for reference but are not deployed.

---

*Generated 2026-05-13 as a backup point. To resume work from this state,
checkout this branch and follow the restore steps above.*
