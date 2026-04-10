#!/bin/bash
# Quick-start: Freqtrade + Claude Sentiment Bot
# ================================================

set -e

echo "=== Freqtrade + Claude Sentiment Bot Setup ==="
echo ""

# 1. Check prerequisites
command -v docker >/dev/null 2>&1 || { echo "Docker required. Install: https://docs.docker.com/get-docker/"; exit 1; }

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "⚠  ANTHROPIC_API_KEY not set."
    echo "   Get your key at: https://console.anthropic.com"
    echo "   Then: export ANTHROPIC_API_KEY='sk-ant-...'"
    echo ""
fi

# 2. Create project directory
PROJECT_DIR="${SYGNIF_REPO:-$HOME/SYGNIF}"
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"

# 3. Download Freqtrade docker-compose
if [ ! -f docker-compose.yml ]; then
    echo "Downloading Freqtrade..."
    curl -sL https://raw.githubusercontent.com/freqtrade/freqtrade/stable/docker-compose.yml -o docker-compose.yml
    docker compose pull
fi

# 4. Create userdata directories
docker compose run --rm freqtrade create-userdir --userdir user_data 2>/dev/null || true

# 5. Copy strategy and config
echo "Copying strategy files..."
# NOTE: Replace these paths with where you downloaded the files
# cp /path/to/ClaudeSentimentStrategy.py user_data/strategies/
# cp /path/to/config_claude_bot.json user_data/config.json

# 6. Install extra dependencies in container
echo "Installing Python dependencies..."
docker compose run --rm freqtrade pip install anthropic feedparser

# 7. Download historical data for backtesting
echo "Downloading historical data for XRP..."
docker compose run --rm freqtrade download-data \
    --pairs XRP/USDT BTC/USDT ETH/USDT \
    --timeframe 1h \
    --days 90

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo ""
echo "1. BACKTEST (immer zuerst!):"
echo "   docker compose run --rm freqtrade backtesting \\"
echo "     --strategy ClaudeSentimentStrategy \\"
echo "     --timeframe 1h"
echo ""
echo "2. DRY RUN (Paper Trading):"
echo "   docker compose up -d"
echo ""
echo "3. LIVE (erst nach ausgiebigem Testen!):"
echo "   - Exchange API Keys in config.json eintragen"
echo "   - dry_run auf false setzen"
echo "   - docker compose up -d"
echo ""
echo "Telegram Bot einrichten:"
echo "   1. @BotFather auf Telegram → /newbot"
echo "   2. Token in config.json eintragen"
echo "   3. Chat ID via @userinfobot holen"
echo ""
echo "FreqUI (Web Dashboard):"
echo "   http://localhost:8080"
