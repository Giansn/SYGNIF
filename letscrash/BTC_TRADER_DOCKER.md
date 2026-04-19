# btc_Trader_Docker — Python-Deps ohne Host-`--break-system-packages`

> **2026-04:** Dedicated BTC Docker services (`freqtrade-btc-spot`, `freqtrade-btc-0-1`, Nautilus stack) and **`docker/Dockerfile.btc_trader`** were **removed** from `docker-compose.yml`. This doc stays as **reference** for rebuilding a similar image locally; snapshot: **`archive/freqtrade-btc-dock-2026-04-13/`**.

**Ziel (historisch):** Zusätzlicher **BTC-Spot-Freqtrade**-Container mit **`yfinance`** (und gleichem Patch-Stack wie `Dockerfile.custom`), **ohne** das Ubuntu-System-`python3` mit `pip install --break-system-packages` zu belasten.

**Warum Docker reicht:** Im **Image-Build** (`docker build`) installiert `pip` in die **Container-Python-Umgebung** der Base-Image (`freqtradeorg/freqtrade:stable`). Das ist **vom Host getrennt** — PEP 668 auf dem EC2-Host bleibt irrelevant. Du brauchst **kein** `--break-system-packages` auf dem Server.

---

## 1. Artefakte

| Pfad | Rolle |
|------|--------|
| `docker/Dockerfile.btc_trader` | **Entfernt aus dem Repo** — aus dem Archiv-Snapshot wiederherstellen oder aus `Dockerfile.custom` ableiten + **`yfinance`** + **`pybit`** |
| `user_data/config_btc_spot_dedicated.example.json` | Config-Vorlage (→ `config_btc_spot_dedicated.json`) |
| `letscrash/BTC_TRADING_DOCKER_SYGNIF_INHERIT_DESIGN.md` | Netzwerk, :8091, RAM, Compose-Fragment |
| `letscrash/RULE_AND_DATA_FLOW_LOOP.md` | **Kontinuierlicher** Rule-/Evidence-Loop, Agent-Konsultation, **Datenflüsse** (TV / Bybit / crypto-market-data / yfinance) |

---

## 1b. Datenflüsse (Kurz)

| Richtung | Was |
|----------|-----|
| **Ein** | `finance-agent:8091` → Sentiment; `user_data/` → Strategy + Anpassung; Bybit → Kurse |
| **Aus** | Webhooks → `notification-handler`; Freqtrade REST über Host-Port (z. B. 8282) |

Ausführlich inkl. **TradingView-Pine**, **Indicator-Wishlist**, Schleife *prove / test / rm / apply*: **`RULE_AND_DATA_FLOW_LOOP.md`**.

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

### 3b. Nautilus research + optional BTC spot (Compose-Profile)

**`nautilus-research`** (Sink + Sidecar via **`run_nautilus_bundled.sh`**) steht in **`docker-compose.yml`** unter Profil **`btc-nautilus`**. Optional **`freqtrade-btc-spot`** unter Profil **`archived-freqtrade-btc-dock`**. Keine separaten Merge-YAMLs mehr.

```bash
cd ~/SYGNIF
docker compose --profile btc-nautilus up -d --build nautilus-research
# optional BTC-Spot-Dock wiederherstellen:
# docker compose --profile archived-freqtrade-btc-dock up -d --build freqtrade-btc-spot
```

REST-Check: `curl -sS http://127.0.0.1:8282/api/v1/ping` (JWT nur für geschützte Endpunkte nötig).  
Sink-Log: `docker logs nautilus-research --tail 20` (eine JSON-Zeile pro erfolgreichem Pull).

---

## 4. Wann doch venv / pipx auf dem Host?

- **Skripte außerhalb Docker** (Cron, einmalige Analysen): **`~/SYGNIF/.venv`** — wie bereits für `yfinance` genutzt.
- **Nur CLI-Tools:** `pipx install …` auf dem Host.

**`--break-system-packages`** nur, wenn du **bewusst** das System-`python3` dauerhaft mit pip vermischst — für **btc_Trader_Docker** ist das **nicht** nötig.

---

## 5. Rollout-Checkliste

- [ ] `config_btc_spot_dedicated.json` aus Example erzeugt (`cp …example.json …` + `openssl` für `jwt_secret_key` / `api_server.password`), **Bybit spot**: `ccxt_config.options.defaultType` = `"spot"`, `stoploss_on_exchange` = `false`.  
- [ ] **Live Bybit:** Keys in `exchange.key` / `exchange.secret` (oder `dry_run: true` ohne Keys). **Bybit demo:** `config_btc_spot_dedicated.bybit_demo.example.json` als Vorlage (`urls.api` → `api-demo.bybit.com`).  
- [ ] Image gebaut; `docker compose … up -d` für neuen Service.  
- [ ] `curl` auf API-Port (z. B. **8282**) `/api/v1/ping`.  
- [ ] Webhooks / `trading_mode` mit `notification_handler` abgestimmt.  

*Siehe auch `.cursor/rules/ruleprediction-agent.mdc` und `.cursor/rules/sygnif-agent-inherit.mdc` für Briefing-Port und Worker-Kontext.*
