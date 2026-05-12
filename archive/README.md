# archive/ — legacy code, kept for reference

These files were part of the original freqtrade-centric SYGNIF stack
(commit ancestry: `74c8c15` and earlier). They have been **superseded**
by newer components and are no longer deployed. Kept here so blame and
git history remain intact.

## What replaced what

| Archived file/dir | Replacement |
|---|---|
| `dashboard.html`, `dashboard_futures_full.html` | EC2 `brain_insights.py` on :8890 (`ec2-snapshot/services/brain_insights.py`) |
| `dashboard_server.py`, `dashboard_server_futures.py` | Same — `sygnif-brain-insights.service` on EC2 |
| `sygnif_bot.py`, `setup_bot.sh`, `config_claude_bot.example.json` | `trade_overseer/` Telegram bot (still active at repo root) |
| `update_movers.py` | EC2 `sygnif-predict.service` + intel aggregator |
| `tf_controller.py`, `tf_switch.py` | Strategy-claim mutex (`ec2-snapshot/trader/agent/strategy_claim.py`) |
| `fill_patch.py` | Ad-hoc one-off patch, no replacement needed |
| `telemetry.py` | `swarm.db` row inserts via `swarm_x1_mirror` |
| `freqtrade_claude_setup.md` | `CLAUDE.md` at repo root (current canonical docs) |
| `mcp_rethink/` | `ec2-snapshot/trader/mcp_servers/` (4 production MCP servers) |

## What's still live at the repo root (NOT archived)

The freqtrade execution layer is still running on EC2 alongside the
agent stack:

- `SygnifStrategy.py` — freqtrade spot strategy
- `user_data/strategies/MarketStrategy.py` — freqtrade futures strategy
- `user_data/config.json`, `config_futures.json` — freqtrade configs
- `docker-compose.yml` — 4-container stack (freqtrade spot, futures, notification-handler, trade-overseer)
- `trade_overseer/` — Telegram commentary + NPU LLM hooks
- `finance_agent/` — host-side briefing + strategy router
- `notification_handler.py` — webhook fan-out
- `notify.sh` — utility script

See `SNAPSHOT.md` for the full architecture overview and `CLAUDE.md`
for the canonical system spec.
