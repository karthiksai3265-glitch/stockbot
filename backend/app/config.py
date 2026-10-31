"""Central configuration. Values can be overridden with environment variables or a .env file."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv is optional
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("STOCKBOT_DB", str(BASE_DIR / "stockbot.db"))

# --- Stock universe -------------------------------------------------------
# Which markets to track. Supported: "SP500", "ASX200", "NIFTY50". Add more in data/universe.py.
UNIVERSE = [u.strip() for u in os.getenv("STOCKBOT_UNIVERSE", "SP500,ASX200,NIFTY50").split(",") if u.strip()]

# --- Price history --------------------------------------------------------
PRICE_HISTORY_PERIOD = os.getenv("STOCKBOT_PRICE_PERIOD", "2y")   # first-time backfill
PRICE_INTERVAL = "1d"

# --- News + AI (LLM) ------------------------------------------------------
# LLM sentiment is optional. Without a key the pipeline falls back to a simple
# keyword scorer so everything still runs.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.getenv("STOCKBOT_LLM_MODEL", "claude-sonnet-5-5")
NEWS_MAX_ITEMS_PER_TICKER = int(os.getenv("STOCKBOT_NEWS_MAX", "15"))

# --- Broker ---------------------------------------------------------------
# "none" = manual execution (default). "alpaca" = one-tap approval through Alpaca (US stocks only).
# Nothing is ever sent to a broker without you approving that specific order.
BROKER = os.getenv("STOCKBOT_BROKER", "none")
ALPACA_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_API_SECRET", "")
ALPACA_LIVE = os.getenv("STOCKBOT_ALPACA_LIVE", "no").strip().lower() == "yes"   # default: PAPER money

# Hard limits applied when you approve an order (they cannot be bypassed from the website)
MAX_ORDER_USD = float(os.getenv("STOCKBOT_MAX_ORDER_USD", "1000"))      # largest single order
MAX_DAILY_ORDERS = int(os.getenv("STOCKBOT_MAX_DAILY_ORDERS", "5"))     # approved orders per UTC day
MAX_DAILY_USD = float(os.getenv("STOCKBOT_MAX_DAILY_USD", "3000"))      # total approved per UTC day
ACCOUNT_SIZE = float(os.getenv("STOCKBOT_ACCOUNT_SIZE", "0"))           # 0 = use the broker's equity for sizing
TICKET_TTL_HOURS = int(os.getenv("STOCKBOT_TICKET_TTL_HOURS", "24"))    # unapproved tickets expire after this
# Needed to approve over HTTP (the website). Leave empty to allow approval from the command line only.
APPROVE_TOKEN = os.getenv("STOCKBOT_APPROVE_TOKEN", "")

# --- Built-in website + automatic updates (python -m app.serve --schedule) --------------------------------------
TIMEZONE = os.getenv("STOCKBOT_TZ", "Australia/Sydney")
# Local times (weekdays) at which the full update runs: refresh data, grade calls, new recommendations, portfolio, learning.
RUN_TIMES = [t.strip() for t in os.getenv("STOCKBOT_RUN_TIMES", "07:30,17:30").split(",") if t.strip()]
