# BTC governance — delegate swarm + R01

**Role:** Single entry to **compose** Sygnif BTC signals for operators and automation:

- **Swarm** — `finance_agent.swarm_knowledge.compute_swarm()` (file sidecars + optional Bybit `mn` / `ac` / admin wallet band).
- **R01** — `r01_registry_bridge.load_r01_governance()` + optional `training_channel_output.json` snapshot for context.

**Not trading logic** — no orders; strategy remains `BTC_Strategy_0_1` / Freqtrade.

## Embedder CLI note

[`embedder-dev/embedder-cli`](https://github.com/embedder-dev/embedder-cli) (install via [embedder.com](https://embedder.com)) targets **embedded software / MCU** workflows (datasheets, firmware), **not** vector embedding of JSON for ML retrieval.

This package’s `embedder_cli.py` only **optionally** shells out to an `embedder` binary when `SYGNIF_EMBEDDER_CLI=1` — for teams that use that tool in the same repo. **Disk recovery** here is handled by `archive.py` (gzip rotation), not by that CLI.

For **semantic / vector** search over prediction history, use a separate indexer (e.g. Chroma + local embeddings); not bundled in Sygnif core.

## Commands

```bash
cd ~/SYGNIF
export PYTHONPATH="$PWD:$PWD/prediction_agent"
python3 scripts/run_btc_governance.py delegate --print-json
python3 scripts/run_btc_governance.py archive --dry-run
```

Use ``run_btc_governance.py`` (not ``btc_governance.py``) so Python does not load the CLI file as the ``btc_governance`` package name.

## Env (archive)

- `BTC_GOV_ARCHIVE_DAYS` — default `14`; files older than this may be gzipped.
- `BTC_GOV_ARCHIVE_PATHS` — colon-separated globs under repo (optional).
- `BTC_GOV_ARCHIVE_DRY_RUN` — `1` = log only.

## Env (embedder hook)

- `SYGNIF_EMBEDDER_CLI` — `1` to attempt `embedder` on `PATH` (non-interactive probe only by default).
