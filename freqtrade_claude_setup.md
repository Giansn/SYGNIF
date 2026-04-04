# Freqtrade + Claude API: Sentiment-Enhanced Trading Bot

## Architektur

```
┌─────────────────────────────────────────────────┐
│                   Freqtrade                      │
│  ┌───────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ Technische│  │ Strategie│  │   Exchange    │ │
│  │ Indikatoren│→│  Engine  │→│  (Binance/    │ │
│  │ (TA-Lib)  │  │          │  │   Kraken)     │ │
│  └───────────┘  └────┬─────┘  └──────────────┘ │
│                      │                           │
│              Bei unsicheren                       │
│              Signalen (Score                      │
│              zwischen 40-60)                     │
│                      ▼                           │
│  ┌──────────────────────────────────────────┐   │
│  │         Claude Sentiment Layer            │   │
│  │  ┌────────────┐    ┌─────────────────┐   │   │
│  │  │ News Fetch │    │ Claude API       │   │   │
│  │  │ (CryptoP., │ →  │ (Haiku 4.5)     │   │   │
│  │  │  RSS, GDELT)│   │ Sentiment Score  │   │   │
│  │  └────────────┘    └─────────────────┘   │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

## Kosten (geschätzt)

| Komponente | Kosten |
|-----------|--------|
| Freqtrade | Gratis (Open Source) |
| Claude API (Haiku, ~20 Calls/Tag) | ~$0.50–1.00/Monat |
| News-Daten (RSS/GDELT) | Gratis |
| Exchange API (Binance/Kraken) | Gratis (nur Trading Fees) |
| **Server (optional)** | Gratis lokal / ~$5 VPS |

## Setup

### 1. Freqtrade installieren

```bash
# Via Docker (empfohlen)
mkdir ft_userdata && cd ft_userdata
curl https://raw.githubusercontent.com/freqtrade/freqtrade/stable/docker-compose.yml -o docker-compose.yml
docker compose pull
docker compose run --rm freqtrade create-userdir --userdir user_data
docker compose run --rm freqtrade new-config --config user_data/config.json

# Oder nativ (Python 3.11+)
git clone https://github.com/freqtrade/freqtrade.git
cd freqtrade
./setup.sh -i
```

### 2. Claude API Key holen

```bash
# 1. Account erstellen: https://console.anthropic.com
# 2. API Key generieren
# 3. Als Environment Variable setzen:
export ANTHROPIC_API_KEY="sk-ant-..."
```

### 3. Dependencies für Sentiment-Layer

```bash
pip install anthropic feedparser requests
```

## Strategie: `ClaudeSentimentStrategy`

Die Strategie funktioniert in 3 Stufen:

1. **Technische Analyse** (Freqtrade-Standard): RSI, EMA, MACD berechnen
2. **Signal-Scoring**: Wenn TA-Signale "unsicher" sind (Score 40-60), wird Claude gefragt
3. **Claude Sentiment**: Analysiert aktuelle News und gibt einen Score zurück

---

## Dateien

Alle Dateien kommen in `user_data/strategies/`
