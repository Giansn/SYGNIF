"""Trade Overseer configuration."""
import os

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Full Freqtrade instance list (filtered by SYGNIF_OVERSEER_INSTANCE when set).
_ALL_FT_INSTANCES = [
    {
        "name": "spot",
        "url": os.environ.get("FT_SPOT_URL", "http://127.0.0.1:8080/api/v1"),
        "user": os.environ.get("FT_SPOT_USER", "freqtrader"),
        "pass": os.environ.get("FT_SPOT_PASS", "CHANGE_ME"),
    },
    {
        "name": "futures",
        "url": os.environ.get("FT_FUTURES_URL", "http://127.0.0.1:8081/api/v1"),
        "user": os.environ.get("FT_FUTURES_USER") or os.environ.get("FT_SPOT_USER", "freqtrader"),
        "pass": os.environ.get("FT_FUTURES_PASS") or os.environ.get("FT_SPOT_PASS", "CHANGE_ME"),
    },
]


def _filter_ft_instances() -> list[dict]:
    raw = list(_ALL_FT_INSTANCES)
    f = os.environ.get("SYGNIF_OVERSEER_INSTANCE", "").strip().lower()
    if not f or f in ("all", "both", "*"):
        return raw
    if f in ("spot", "futures"):
        return [x for x in raw if x["name"] == f]
    names = {x.strip() for x in f.replace(",", " ").split() if x.strip()}
    out = [x for x in raw if x["name"] in names]
    return out if out else raw


FT_INSTANCES = _filter_ft_instances()

if not FT_INSTANCES:
    raise RuntimeError(
        "SYGNIF_OVERSEER_INSTANCE left no Freqtrade instances; use spot, futures, or all."
    )

# Polling
POLL_INTERVAL_SEC = 1800      # 30 minutes
EVAL_COOLDOWN_SEC = 1800      # Don't re-evaluate same trade within 30 min

# Thresholds for alerts
PROFIT_ALERT_HIGH = 3.0       # % — approaching TP territory
PROFIT_ALERT_LOW = -2.0       # % — approaching SL territory
STALE_TRADE_HOURS = 12        # Flag trades open longer than this
SIGNIFICANT_CHANGE_PCT = 1.5  # Profit changed more than this since last eval

# Telegram — prefer dedicated overseer bot token if set
TG_TOKEN = (
    os.environ.get("SYGNIF_HEDGE_BOT_TOKEN", "")
    or os.environ.get("FINANCE_BOT_TOKEN", "")
    or os.environ.get("TELEGRAM_BOT_TOKEN", "")
)
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

# Data paths — single-instance runs use trade_overseer/data/<name>/ so two processes do not clobber state.
if len(FT_INSTANCES) == 1:
    DATA_DIR = os.path.join(_BASE_DIR, "data", FT_INSTANCES[0]["name"])
else:
    DATA_DIR = os.path.join(_BASE_DIR, "data")

PLAYS_FILE = os.path.join(DATA_DIR, "plays.json")
STATE_FILE = os.path.join(DATA_DIR, "state.json")

# HTTP server (OVERSEER_HTTP_PORT for a second process; OVERSEER_HTTP_HOST to bind beyond localhost)
HTTP_PORT = int(os.environ.get("OVERSEER_HTTP_PORT", "8090"))
HTTP_HOST = os.environ.get("OVERSEER_HTTP_HOST", "127.0.0.1")
