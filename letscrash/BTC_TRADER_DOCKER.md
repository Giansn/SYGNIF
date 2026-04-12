# btc_Trader_Docker — Python-Deps ohne Host-`--break-system-packages`

**Ziel:** Zusätzlicher **BTC-Spot-Freqtrade**-Container mit **`yfinance`** (und gleichem Patch-Stack wie `Dockerfile.custom`), **ohne** das Ubuntu-System-`python3` mit `pip install --break-system-packages` zu belasten.

**Warum Docker reicht:** Im **Image-Build** (`docker build`) installiert `pip` in die **Container-Python-Umgebung** der Base-Image (`freqtradeorg/freqtrade:stable`). Das ist **vom Host getrennt** — PEP 668 auf dem EC2-Host bleibt irrelevant. Du brauchst **kein** `--break-system-packages` auf dem Server.

---

## 1. Artefakte

| Pfad | Rolle |
|------|--------|
| `docker/Dockerfile.btc_trader` | Wie `Dockerfile.custom` + **`yfinance`** |
| `user_data/config_btc_spot_dedicated.example.json` | Config-Vorlage (→ `config_btc_spot_dedicated.json`) |
| `letscrash/BTC_TRADING_DOCKER_SYGNIF_INHERIT_DESIGN.md` | Netzwerk, :8091, RAM, Compose-Fragment |

---

## 2. Build

```bash
cd ~/SYGNIF
docker build -f docker/Dockerfile.btc_trader -t sygnif-freqtrade-btc:latest .
```

---

## 3. Compose (Auszug — Service an Haupt-`docker-compose.yml` anfügen)

Nutze **`dockerfile: ./docker/Dockerfile.btc_trader`** und **`image: sygnif-freqtrade-btc:latest`** (oder lasse Compose bauen ohne `image`, dann generierter Name).

Wichtig: weiterhin **`SYGNIF_SENTIMENT_HTTP_URL`** → `http://finance-agent:8091/sygnif/sentiment`, **`user_data`**-Mount, **`--config`** → `config_btc_spot_dedicated.json`, **`--db-url`** → eigenes SQLite (siehe Design-Doc §6).

---

## 4. Wann doch venv / pipx auf dem Host?

- **Skripte außerhalb Docker** (Cron, einmalige Analysen): **`~/SYGNIF/.venv`** — wie bereits für `yfinance` genutzt.
- **Nur CLI-Tools:** `pipx install …` auf dem Host.

**`--break-system-packages`** nur, wenn du **bewusst** das System-`python3` dauerhaft mit pip vermischst — für **btc_Trader_Docker** ist das **nicht** nötig.

---

## 5. Rollout-Checkliste

- [ ] `config_btc_spot_dedicated.json` aus Example erzeugt, Keys gesetzt.  
- [ ] Image gebaut; `docker compose … up -d` für neuen Service.  
- [ ] `curl` auf API-Port (z. B. **8282**) `/api/v1/ping`.  
- [ ] Webhooks / `trading_mode` mit `notification_handler` abgestimmt.  

*Siehe auch `.cursor/rules/ruleprediction-agent.mdc` und `.cursor/rules/sygnif-agent-inherit.mdc` für Briefing-Port und Worker-Kontext.*
