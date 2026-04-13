# Security metrics (Sygnif)

## Snapshot script

Run from repo root:

```bash
python3 scripts/sec_metrics_snapshot.py
python3 scripts/sec_metrics_snapshot.py --json
```

**Emits:** whether `.env` / `.env.local` / `.env.secrets` exist, file modes, **count** of `KEY=value` lines (keys **not** listed), whether those filenames are **git-tracked** (should be **none**), and **`dry_run` / `dry_run_wallet`** from `user_data/config.json` and `config_futures.json` if present.

**Never prints** secret values.

## Demo / honeypot keys (policy)

Using **dedicated demo API keys** to detect misuse is a valid **operational** idea, but:

- Pasting keys into **chat, tickets, or screenshots** still widens the blast radius (logs, vendors, backups).
- **Rotation** after any suspected leak remains best practice even for demo accounts.
- **Code and git** should only ever reference secrets via **environment** or **secret stores**, not committed files.

## Suggested hardening (manual)

| Check | Target |
|--------|--------|
| `.env` permissions | `chmod 600 .env` on the server |
| Git | `git ls-files .env` → empty |
| Freqtrade paper | `dry_run: true` until you intentionally go live |
| Exchange | Separate **demo** vs **live** keys and URLs |
