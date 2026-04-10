---
name: finance-consultant
version: 1.0.0
description: |
  Sygnif Finance Consultant — a unified crypto finance skill that orchestrates
  market scanning, technical analysis, AI-powered research, strategy-aligned
  signal detection, and investment opportunity evaluation. Combines all
  finance-agent capabilities into a single conversational interface backed by
  the Sygnif trading strategy.
allowed-tools:
  - Task
  - Bash
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - WebSearch
  - WebFetch
license: MIT
---

# Finance Consultant Skill

## Overview

The Finance Consultant is a comprehensive crypto analysis skill that unifies
the capabilities of the Sygnif Finance Agent. It acts as a strategy-aware
financial advisor that can scan markets, run technical analysis aligned with
the live Sygnif trading bot, evaluate open positions, generate AI investment
plays, and deliver market research — all through natural language requests.

### Core Capabilities

| Capability | Description | Underlying Command |
|---|---|---|
| **Market Overview** | Top 15 crypto by volume with price/change/volume | `/market` |
| **Market Tendency** | Bull/bear market reading with AI insight | `/tendency` |
| **Technical Analysis** | Full TA with strategy signals, TA score, oscillators | `/ta <TICKER>` |
| **Signal Scanner** | Active entry signals across top pairs | `/signals` |
| **Deep Scan** | Signals + news + AI conviction ranking | `/scan` |
| **AI Research** | Full research report (TA + news + sentiment + AI) | `/research <TICKER>` |
| **Investment Plays** | 3 actionable AI-generated trading opportunities | `/plays` |
| **Top Movers** | Top 5 gainers and losers (24h) | `/movers` |
| **News Headlines** | Latest crypto news from RSS feeds | `/news` |
| **Trade Overview** | Open trades + P/L + TA context (via overseer) | `/overview` |
| **Trade Evaluation** | AI-classified HOLD/TRAIL/CUT per open position | `/evaluate` |

## When This Skill Activates

Use this skill when the user:
- Asks about cryptocurrency markets, prices, or trends
- Requests technical analysis for any trading pair
- Wants to know what signals the Sygnif bot is seeing
- Asks for investment ideas or trading opportunities
- Wants a market overview or sentiment reading
- Asks about open trades or portfolio evaluation
- Mentions specific tickers (BTC, ETH, SOL, XRP, etc.)
- Uses phrases like "scan the market", "what should I trade?", "how's the market?"
- Wants to understand the strategy's current view on a coin

**Example triggers:**
- "What's the market looking like?"
- "Run TA on ETH"
- "Any strong signals right now?"
- "Give me investment plays"
- "Research SOL for me"
- "How are my trades doing?"
- "What are the biggest movers today?"
- "Is BTC bullish or bearish?"
- "Scan for opportunities"
- "Evaluate my open positions"

## Architecture

```
finance_agent/
├── bot.py                         # Core engine: Telegram bot + all analysis logic
├── run.sh                         # Launcher script
├── AI Upload/
│   ├── finance-consultant/        # ← This skill
│   │   └── SKILL.md
│   ├── technical-analyzer/        # TA calculation reference
│   │   └── SKILL.md
│   ├── market-movers-scanner/     # Market mover scanning reference
│   │   └── SKILL.md
│   └── crypto-research/           # Multi-agent research framework
│       ├── SKILL.md
│       ├── agent-prompts/         # 7 specialized agent prompts
│       ├── workflows/             # Execution modes
│       ├── scripts/               # Utility scripts
│       └── reference/             # Documentation
```

### Component Relationships

The Finance Consultant orchestrates three sub-skills:

1. **Technical Analyzer** (`technical-analyzer/SKILL.md`):
   Provides the methodology for computing indicators from OHLCV data —
   EMA, RSI, MACD, VWAP, Bollinger Bands, support/resistance detection,
   and pattern recognition across multiple timeframes.

2. **Market Movers Scanner** (`market-movers-scanner/SKILL.md`):
   Scans for significant price movements and unusual volume across
   crypto markets using exchange APIs and data aggregators.

3. **Crypto Research** (`crypto-research/SKILL.md`):
   Multi-agent research system that orchestrates 4–12 specialized AI
   agents in parallel for comprehensive market/coin/macro/plays analysis.

## How It Works

### Stage 1: Request Classification

When the user asks a finance question, classify the request:

| User Intent | Action | Speed |
|---|---|---|
| Quick price check | `/market` or `/ta <TICKER>` | Fast (<5s) |
| Market direction | `/tendency` | Medium (~15s) |
| Active signals | `/signals` | Medium (~15s) |
| Deep opportunity scan | `/scan` | Slow (~30s) |
| Full research report | `/research <TICKER>` | Slow (~30s) |
| Investment ideas | `/plays` | Slow (~30s) |
| Trade evaluation | `/evaluate` | Medium (~15s) |
| News check | `/news` | Fast (<5s) |
| Portfolio overview | `/overview` | Medium (~15s) |

### Stage 2: Data Gathering

All analysis is built on real-time data from Bybit's V5 API:

- **Spot tickers**: `GET /v5/market/tickers?category=spot`
- **OHLCV candles**: `GET /v5/market/kline` (1h interval, 200 candles)
- **News**: RSS feeds from CoinTelegraph, CoinDesk, CryptoPanic

### Stage 3: Indicator Computation

The engine computes indicators that mirror the live `SygnifStrategy.py`:

**Moving Averages:**
- EMA 9, 12, 21, 26, 50, 120, 200

**Oscillators:**
- RSI (14-period and 3-period momentum)
- Williams %R (14-period)
- StochRSI (14-period, smoothed)
- CCI (20-period)
- CMF (Chaikin Money Flow, 20-period)

**Trend & Momentum:**
- MACD (12/26/9)
- Aroon Up/Down (14-period)
- ROC (9-period rate of change)
- ATR (14-period, for leverage sizing)
- Bollinger Bands (20-period, 2σ)

**Swing Failure Detection:**
- 48-bar support/resistance window
- Volatility filter (>3% distance from EMA 120)
- Stability check (S/R unchanged for 2 bars)

### Stage 4: Strategy TA Score (0–100)

The TA score mirrors `_calculate_ta_score_vectorized()` from the live strategy:

| Component | Range | Weight |
|---|---|---|
| RSI 14 | -15 to +15 | Overbought/oversold zones |
| RSI 3 momentum | -10 to +10 | Extreme momentum |
| EMA crossover | -10 to +10 | 9/26 cross state |
| Bollinger Bands | -8 to +8 | Price at band edges |
| Aroon | -8 to +8 | Strong trend confirmation |
| StochRSI | -5 to +5 | Overbought/oversold |
| CMF | -5 to +5 | Money flow direction |
| Volume ratio | -3 to +3 | Volume confirmation |

**Score interpretation:**
- **≥ 65**: Bullish — qualifies for `strong_ta` long entry
- **55–64**: Lean Bullish
- **45–54**: Neutral
- **35–44**: Lean Bearish
- **≤ 25**: Bearish — qualifies for `strong_ta_short` entry

### Stage 5: Signal Detection

Entry signals match the live strategy's conditions:

| Signal | Side | Condition |
|---|---|---|
| `strong_ta_long` | Long | TA ≥ 65 + volume > 1.2× avg |
| `strong_ta_short` | Short | TA ≤ 25 |
| `ambiguous_long` | Long | TA 40–70 (Claude sentiment zone) |
| `ambiguous_short` | Short | TA 30–60 (Claude sentiment zone) |
| `sf_long` | Long | Swing failure at support |
| `sf_short` | Short | Swing failure at resistance |

Exit signals:
- `willr_overbought`: Williams %R > -5
- `willr_oversold`: Williams %R < -95

### Stage 6: Leverage Tier Calculation

| Pair Type | Default Leverage |
|---|---|
| Majors (BTC, ETH, SOL, XRP) | 5× |
| Other | 3× |
| ATR > 2% | Capped at 3× |
| ATR > 3% | Capped at 2× |

### Stage 7: AI Enhancement (Claude Haiku)

For research, plays, tendency, scan, and evaluate commands, the engine
calls Claude Haiku to synthesize indicator data with news and market context
into actionable insights.

### Stage 8: Output Formatting

All outputs are formatted for Telegram (Markdown) with:
- Monospace values for easy scanning
- Emoji indicators (green/red/neutral)
- UTC timestamps
- Persistent reply keyboard for quick access

## Integration Points

### Trade Overseer (port 8090)

The Finance Consultant connects to the Trade Overseer for:
- `/overview` — Fetches open trades and P/L from `GET /trades`
- `/evaluate` — Gets trade data for AI classification
- `/plays` — Posts generated plays to `POST /plays`

### Briefing HTTP Server (port 8091)

Exposes a lightweight briefing endpoint for the Trade Overseer's LLM:
- `GET /briefing?symbols=BTC,ETH,SOL` — Compact pipe-delimited TA data
- `GET /health` — Health check

## Pair Filtering

All scans automatically:
- Filter to USDT pairs only
- Exclude stablecoins (USDC, BUSD, DAI, TUSD, FDUSD, USDD, USDP, USDS, USDE)
- Exclude leveraged tokens (2L, 3L, 5L, 2S, 3S, 5S)
- Apply minimum turnover thresholds ($500K–$2M depending on command)

## Usage Patterns

### Quick Market Check
```
User: "How's the market?"
→ Run /tendency for bull/bear reading with AI insight
→ Optionally follow up with /signals for active entries
```

### Coin Deep-Dive
```
User: "Analyze ETH"
→ Run /ta ETH for full technical analysis with strategy signals
→ If user wants more: /research ETH for AI-powered full report
```

### Trading Opportunities
```
User: "Find me trades"
→ Run /scan for signal + news + AI ranked opportunities
→ Or /plays for 3 structured investment plays with entry/exit
```

### Portfolio Management
```
User: "How are my positions?"
→ Run /overview for trades + TA context + market tendency
→ If user wants action: /evaluate for AI HOLD/TRAIL/CUT per trade
```

### Multi-Agent Research (Advanced)
```
User: "I need deep research on Solana"
→ Invoke the crypto-research sub-skill in comprehensive mode
→ Launches 12 specialized agents across 3 model tiers
→ Produces timestamped output in organized directories
```

## Error Handling

- **No market data**: Returns "Failed to fetch data." — check Bybit API connectivity
- **Insufficient candles**: Returns "Not enough data for TICKER" — pair may be too new
- **Claude unavailable**: Returns "_Analysis unavailable._" — check ANTHROPIC_API_KEY
- **Overseer unavailable**: Returns "Overseer unavailable: ..." — check port 8090
- **Telegram send failure**: Logged but does not crash the bot

## Environment Variables

| Variable | Purpose | Required |
|---|---|---|
| `FINANCE_BOT_TOKEN` | Telegram Bot API token | Yes |
| `TELEGRAM_CHAT_ID` | Authorized chat for commands | Yes |
| `ANTHROPIC_API_KEY` | Claude Haiku for AI analysis | For AI features |

## Running the Finance Agent

```bash
# Direct
export FINANCE_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="your-chat-id"
export ANTHROPIC_API_KEY="your-key"
python3 finance_agent/bot.py

# Via launcher
./finance_agent/run.sh
```

## Related Skills

- **Technical Analyzer** (`technical-analyzer/SKILL.md`): Detailed indicator calculation reference with pattern recognition guidelines
- **Market Movers Scanner** (`market-movers-scanner/SKILL.md`): Methodology for tracking significant price movements
- **Crypto Research** (`crypto-research/SKILL.md`): Multi-agent orchestration framework with 7 specialized agent prompts

## Version History

- v1.0.0 (2026-04): Initial skill creation — consolidates all finance agent capabilities
