# Brain state backup — scripts + procedures

The NeuroLinked brain on EC2 keeps its state at
`/home/ubuntu/SYGNIF/third_party/neurolinked/brain_state/`. This is NOT
git-tracked (it's runtime state, not code) and would otherwise be a
single-point-of-failure for everything the brain has learned.

## What's the brain learning state worth saving?

Per the 2026-05-13 audit, the **187 MB core** breaks down as:

| Component | Size | Why valuable |
|---|---|---|
| `knowledge.db` | 186 MB | 67,500+ model2vec-encoded text memories — irreplaceable |
| `synapses/*.npz` | ~580 KB | STDP-evolved weights from 92M+ simulation steps |
| `regions/*.json` | 256 KB | Per-region neuron v/u arrays (settles fast but nice to keep) |
| `live.json`, `meta.json` | < 1 KB | Heartbeat + last-persistence checkpoint |

The on-EC2 `backups/` dir (~1.8 GB of stacked 7-min rolling snapshots) is
**deliberately excluded** — it's redundant with itself and bloats every
copy.

## Scripts

### `backup_brain.sh` — one-shot backup to local X1

```bash
bash scripts/brain_backup/backup_brain.sh
```

Writes a versioned snapshot to `~/Backups/sygnif-brain/<UTC_timestamp>/`:

```
20260513_135907Z/
├── MANIFEST.json                  sha256 of every file + inline live/meta/insights
├── live.json                      heartbeat at backup time
├── meta.json                      last persistence checkpoint metadata
├── regions/*.json                 11 region files (per-region neurons)
├── synapses/*.npz                 56 STDP weight matrices
├── knowledge.db.gz                gzipped knowledge.db (186 MB → ~51 MB)
├── insights_api_state.json        brain_insights /api/state response
├── insights_api_stats.json        brain_insights /api/stats response
└── RESTORE.md                     copy-paste restore recipe
```

Captured headline stats are embedded in `MANIFEST.json["live"]`:
step_count, development_stage, total_neurons, total_synapses,
neuromodulator levels at backup time.

## Restore on a new (or wiped) EC2 box

See `RESTORE.md` inside each backup directory — it has the exact
`scp` + `tar` + `systemctl restart` sequence.

## Automation paths (not yet wired)

If you want nightly off-box backups, two patterns are viable:

1. **Nightly cron on X1** — `0 3 * * * bash $PATH/backup_brain.sh`. Keeps last 7 daily on X1.
2. **Weekly push to GitLab Generic Packages** — chunk the latest backup and `glab package upload` to `giansn1/sygnif-intelligence`. Survives X1 loss.
3. **Cloud durability** — AWS S3 lifecycle policy (Glacier after 30d). $0.05/month per backup at this size. Requires bucket + IAM setup.

None are wired today. The one-shot script above is the manual entrypoint.
