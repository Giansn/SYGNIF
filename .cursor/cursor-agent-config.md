# Sygnif Agent — Cursor Cloud (ausreichend)

Der **Sygnif Agent** läuft über **Cursor Cloud Agent** + den **Worker** auf dieser Instanz. Das reicht als Hauptpfad.

## Auf der EC2-Instanz

| Service | Rolle |
|---------|--------|
| **`cursor-agent-worker.service`** | Private Worker-Verbindung zu Cursor; Workspace **`~/SYGNIF`** |
| **`finance-agent.service`** (optional) | Telegram-Bot — LLM nutzt **dieselbe** Cursor Cloud API (`CURSOR_*` in `.env`), Fallback Ollama |

Health Worker: `http://127.0.0.1:8093/healthz`  
Logs: `~/.local/share/cursor-agent/worker.log`

## `.env` (für Cloud-LLM im Telegram-Bot und einheitliches Verhalten)

| Variable | Pflicht für Cloud-LLM |
|----------|------------------------|
| `CURSOR_API_KEY` | ja ([cursor.com/settings](https://cursor.com/settings)) |
| `CURSOR_AGENT_REPOSITORY` | ja (z. B. `https://github.com/Giansn/SYGNIF`) |
| `CURSOR_AGENT_REF` | optional, default `main` |

Optional: `OLLAMA_MODEL` nur als Fallback, wenn `CURSOR_*` fehlen.

`LLM_BACKEND=ollama` erzwingt lokal; `LLM_BACKEND=none` schaltet LLM aus.

## CLI (lokal am Rechner)

`~/.cursor/cli-config.json` — Cursor IDE / Agent CLI.

## Projektregeln

- `.cursor/rules/sygnif-agent-inherit.mdc`
- `.cursor/rules/sygnif-linear-workflow.mdc` — linearer Ablauf (ein Einstieg → sequentielle Daten → Antwort)
- `.cursor/rules/sygnif-predict-workflow.mdc`

## Workflow-Schleife (Finance Agent ↔ Cursor ↔ Overseer)

| Schritt | Komponente |
|---------|----------------|
| 1 | **Telegram** `/sygnif` oder `/cursor` — sammelt Rohdaten: Worker-Health (`8093`), **Overseer** (`OVERSEER_URL`, default `8090`), `strategy_adaptation.json`, **Signals**, **Tendency**, **Macro**. |
| 2 | Derselbe Pfad wie alle Slash-Befehle: **`agent_slash_dispatch` → `llm_analyze` → Cursor Cloud** (kein zweites LLM-Backend). |
| 3 | **Freitext** im Agent-Chat: gleicher LLM-Pfad + **Chat-Verlauf** (`conversational_reply`). |
| 4 | **Overseer** bleibt autonom (Poll + HTTP); `/plays` schreibt weiter `POST …/plays`. |

Env: `OVERSEER_URL`, optional `CURSOR_WORKER_HEALTH_URL` (default `http://127.0.0.1:8093/healthz`).

Kurzbefehle: `/sygnif analytics`, `/finance-agent cycle`, `/finance-agent analytics`.
