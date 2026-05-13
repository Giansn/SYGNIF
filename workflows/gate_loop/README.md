# SYGNIF gate_loop — Render Workflow

Closes the **Predict → Analyze → Proofread → Adjust** loop for the swarm gate.

Validates that `predict_protocol_gate_optimizer` proposals actually improve
prediction quality before they reach the live trader, and emits a finetune
signal that the next `sygnif-finetune-automation` tick consumes.

## Tasks

| Task | Plan | Purpose |
|------|------|---------|
| `sweep_challenger(window_hours, trials, folds, engine)` | standard | Runs `scripts/predict_protocol_gate_optimizer.py` (walk-forward TPE/PSO/random); returns proposed gate env |
| `proofread_offline(champion_env, challenger_env, oos_hours)` | standard | Replays **both** gates through `predict_protocol_offline_swarm_backtest.run_simulation` on the same held-out OOS window; returns deltas |
| `proofread_live(oos_hours)` | starter | Reads `prediction_agent/btc_eval_outcomes.jsonl` over the OOS window — true hold-out from `btc_forecast_eval` |
| `decide_promotion(offline, live)` | starter | Promotes only if `delta_pnl ≥ +2%`, `delta_win_rate ≥ 0`, `delta_max_dd ≤ 0`, `n_resolved ≥ 30`, `live_pass_rate ≥ 0.50`; else `reject` / `abstain` |
| `append_ledger(row)` | starter | Appends verdict to `prediction_agent/challenger_promotions.jsonl` |
| `orchestrate(...)` | standard | Top-level: chains the five above. Entry point for the Render cron trigger. |

## Closed loop

```
                  ┌─ sweep_challenger ─┐
orchestrate ─────►│ proofread_offline  │
  (cron 6h)       │ proofread_live     │
                  │ decide_promotion   │ → append_ledger
                  └────────────────────┘                ↓
                                              challenger_promotions.jsonl
                                                        ↓
                              finetune_with_promotions reads the ledger
                              on the next sygnif-finetune-automation tick
                                                        ↓
                              channel_training.py applies sample-weights
                                                        ↓
                              btc_predict_runner publishes improved
                              btc_prediction_output.json
                                                        ↓
                              next sweep_challenger starts from a
                              stronger baseline ──── loop closes
```

## Local Development

```bash
cd ~/SYGNIF/workflows/gate_loop
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# start the local task server
render workflows dev -- .venv/bin/python main.py

# in another terminal, trigger the full loop with a small sweep
render workflows tasks start orchestrate --local --input='{
  "window_hours": 48,
  "trials": 10,
  "folds": 2,
  "oos_hours": 12
}'

# or run individual tasks
render workflows tasks start sweep_challenger --local --input='{"trials": 5}'
render workflows tasks start proofread_live --local --input='{"oos_hours": 24}'
```

## Deploy to Render

```bash
render workflows create \
  --name gate_loop \
  --runtime python \
  --build-command "pip install -r workflows/gate_loop/requirements.txt" \
  --run-command "python workflows/gate_loop/main.py" \
  --repo https://github.com/Giansn/SYGNIF
```

Then in the Render Dashboard:

1. Set **Root Directory** to repo root (so `scripts/`, `prediction_agent/`, etc. are reachable).
2. Add a **cron job** that triggers `orchestrate` every 6h via the Render SDK client.
3. Add env vars: `RENDER_API_KEY` for cross-task calls; optional `BYBIT_DEMO_*` if the optimizer's `--offline-hm-source demo_*` is used.

## Promotion gate (default)

| Criterion | Threshold |
|-----------|-----------|
| Offline OOS PnL delta | `≥ +2%` |
| Offline OOS win-rate delta | `≥ 0` |
| Offline OOS max-drawdown delta | `≤ 0` |
| Live resolved samples in window | `≥ 30` |
| Live `direction_correct` rate | `≥ 0.50` |

Verdict is one of `promote` / `reject` / `abstain`. Only `promote` rows feed the finetune loop.

## Consumer (next PR)

`training_pipeline/finetune_with_promotions.py` will:

1. Read all `verdict: "promote"` rows in `challenger_promotions.jsonl` since the last finetune tick.
2. Compute per-bar sample weights over the recent training window (upweight bars where the promoted gate would have correctly traded).
3. Pass the weights as `--sample-weight-jsonl` to `training_pipeline/channel_training.py`.

## Files

```
workflows/gate_loop/
  main.py            — task definitions + orchestrator
  requirements.txt   — render_sdk + SYGNIF subset (numpy, pandas, optuna, sklearn, requests)
  .env.example
  README.md          — this file
```

## See also

- `scripts/predict_protocol_gate_optimizer.py` — the sweep (Phase 3.2)
- `scripts/predict_protocol_offline_swarm_backtest.py` — the offline simulator
- `prediction_agent/btc_forecast_eval.py` — append-pending / resolve-outcome
- `scripts/prediction_horizon_check.py` — +24h / +48h verdict
- `scripts/sygnif_finetune_automation.sh` — hourly finetune loop (consumer of the ledger)
- `.cursor/rules/sygnif-predict-workflow.mdc` — Predict → Analyze → Proofread → Adjust spec
