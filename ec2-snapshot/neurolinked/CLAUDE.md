# NeuroLinked Brain — Sygnif integration

This project has a neuromorphic brain running at http://localhost:8000 (or the port you pass to `run.py`).

## Quick API reference

- **GET /api/sygnif/summary** — Read brain state (canonical JSON for Sygnif CLI and services)
- **GET /api/claude/summary** — Permanent redirect (308) to `/api/sygnif/summary`
- **POST /api/claude/observe** — Send observations (`type`, `content`, optional `source` e.g. `sygnif`, `user`, `dashboard`)
- **GET /api/claude/insights** — Brain-derived insights
- **POST /api/brain/save** — Save brain state
