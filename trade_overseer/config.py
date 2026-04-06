"""Trade Overseer configuration."""
import os

# Freqtrade instances
FT_INSTANCES = [
    {
        "name": "spot",
        "url": "http://127.0.0.1:8080/api/v1",
        "user": os.environ.get("FT_SPOT_USER", "freqtrader"),
        "pass": os.environ.get("FT_SPOT_PASS", "CHANGE_ME"),
    },
    {
        "name": "futures",
        "url": "http://127.0.0.1:8081/api/v1",
        "user": os.environ.get("FT_FUTURES_USER", "freqtrader"),
        "pass": os.environ.get("FT_FUTURES_PASS", "CHANGE_ME"),
    },
]

# Ollama
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "plutus-3b"

# Polling
POLL_INTERVAL_SEC = 300       # 5 minutes
EVAL_COOLDOWN_SEC = 600       # Don't re-evaluate same trade within 10 min

# Thresholds for alerts
PROFIT_ALERT_HIGH = 3.0       # % — approaching TP territory
PROFIT_ALERT_LOW = -2.0       # % — approaching SL territory
STALE_TRADE_HOURS = 12        # Flag trades open longer than this
SIGNIFICANT_CHANGE_PCT = 1.5  # Profit changed more than this since last eval

# Telegram — use FINANCE_BOT_TOKEN (same bot as finance_agent)
TG_TOKEN = os.environ.get("FINANCE_BOT_TOKEN", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

# Data paths
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PLAYS_FILE = os.path.join(DATA_DIR, "plays.json")
STATE_FILE = os.path.join(DATA_DIR, "state.json")

# Conversation history
EVAL_HISTORY_SIZE = 3             # Keep last N evaluations for context

# HTTP server
HTTP_PORT = 8090
