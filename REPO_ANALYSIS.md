# SYGNIF Repository Analysis

## Overview

SYGNIF is an algorithmic trading system that bridges traditional technical analysis with AI-driven sentiment analysis. The system executes both spot and futures trades on the Bybit exchange, utilizing dual Freqtrade Docker containers and an overarching "Trade Overseer" that integrates with the Anthropic API (Claude 3.5 Haiku) and Telegram for commentary and sentiment signals.

## Architecture & Components

The repository is divided into several main components:

### 1. Trading Strategies (Freqtrade)
*   **SygnifStrategy**: The main spot trading strategy based heavily on NostalgiaForInfinityX7 (NFI) concepts.
    *   Uses multi-timeframe analysis (5m base + 15m/1h/4h/1d).
    *   Incorporates NFI-style indicators (RSI_3/14, Aroon, StochRSI, CMF, etc.) and global protections to prevent entries during market crashes.
    *   **AI Sentiment Layer**: When technical signals fall into an ambiguous zone, the strategy queries Claude via API using recent news headlines to generate a sentiment score that confirms or rejects the trade.
*   **MarketStrategy**: Inherits from a frozen v1 snapshot of SygnifStrategy (`MarketStrategy1`) to handle the futures trading container (`freqtrade-futures`), allowing short positions and leverage scaling based on pairs and volatility (ATR).

### 2. Docker Containers (`docker-compose.yml`)
The execution environment uses four main services:
*   `freqtrade`: Runs the spot market strategy on port 8080.
*   `freqtrade-futures`: Runs the futures market strategy on port 8081.
*   `notification-handler`: Manages webhook fan-outs.
*   `trade-overseer`: Monitors Freqtrade APIs, generating Telegram commentary on trade states, employing a local LLM or fallback rules.

### 3. Trade Overseer & Finance Agent
*   **Trade Overseer** (`trade_overseer/`): An external monitor that observes the running Freqtrade containers. It generates insights on active trades, tracks execution metrics (like threshold hit rates), and posts updates to Telegram (`@Sygnif_hedge_bot`).
*   **Finance Agent** (`finance_agent/`): An HTTP service orchestrating market research via parallel subagents. It provides market briefings, technical analysis, and sentiment exploration, responding to Telegram bot commands (e.g., `/market`, `/research`, `/plays`).

### 4. Code Intelligence
*   **GitNexus**: The project leverages GitNexus MCP for intelligent code analysis, impact checks, and refactoring safeguards. `CLAUDE.md` explicitly enforces GitNexus usage before modifying core files to avoid introducing regressions in critical execution flows.

## Execution Flows

1.  **Market Data Intake**: Freqtrade ingests 5m candle data along with higher timeframe info.
2.  **Signal Generation**:
    *   *Strong TA*: Entries are triggered purely by technical indicators.
    *   *Ambiguous TA*: A request is sent to the Anthropic API with recent crypto news (fetched via GDELT/RSS/Reddit). Claude responds with a sentiment score.
    *   *Combined Trigger*: If the TA + sentiment score meets the threshold, the trade is entered.
3.  **Trade Management**: Active trades are managed with profit-tiered RSI exits, volatility-adjusted Stop Losses, and doom stoplosses (cooldowns prevent immediate re-entry).
4.  **Monitoring**: The `trade-overseer` polls the Freqtrade APIs and posts interpreted updates/commentary back to Telegram users.

## Recent Architectural Shifts
*   The system operates alongside a broader architecture defined in `AGENT.md`/`SYGNIF.md`, where higher-level agents on separate infrastructure (X1/EC2) handle regime classification, AI narrations (NeuroLinked), and portfolio sizing.
*   Legacy files (dashboards, setup scripts) have been archived, centralizing monitoring into the `trade-overseer` and `finance_agent`.