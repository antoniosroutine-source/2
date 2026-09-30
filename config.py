"""Bot settings. Edit values here; the API key comes from the FPERP_API_KEY env var."""
import os

# --- API ---------------------------------------------------------------------
# Test keys (fp_test_) only work on the sandbox host; live keys (fp_live_) only on the live host.
SANDBOX_URL = "https://sandbox.myfundedperpetuals.com/v1"
LIVE_URL = "https://developers.myfundedperpetuals.com/v1"
BASE_URL = os.environ.get("FPERP_BASE_URL", SANDBOX_URL)
API_KEY_ENV = "FPERP_API_KEY"          # sandbox keys start fp_test_, live keys fp_live_
ACCOUNT_ID = os.environ.get("FPERP_ACCOUNT_ID")  # optional; defaults to the first account

# --- Account / risk ------------------------------------------------------------
# MFP 1-Step Prime rules: 3% daily loss, 5% static max drawdown, 12% profit target, all on the
# starting balance. The bot's own limits below are deliberately tighter than the firm's.
STARTING_BALANCE = 25_000     # Prime account; replaced at startup by the account's real starting_balance
MAX_DRAWDOWN_PCT = 0.02       # bot's max loss: 2% static ($500); the firm allows 5% ($1,250)
MAX_DAILY_LOSS_PCT = 0.02     # bot's daily loss cap: 2% ($500); the firm allows 3% ($750)
RISK_BUFFER = 0.75            # bot halts at 75% of each of its own limits ($375)
SERVER_ROOM_RESERVE_USD = 150 # also halt if MFP reports less than this left above either firm floor
RISK_PER_TRADE_USD = 100      # loss if the stop is hit (3 full losses reach the $375 halt)
MAX_NOTIONAL_MULT = 3.0       # position notional never exceeds 3x equity ($75K)

# --- Strategy ------------------------------------------------------------------
STRATEGY_ID = "silver_bullet"
# MFP's Nasdaq-100 perpetual is XYZ100 on Hyperliquid. A plain symbol is looked up in MFP's market
# list at startup; NAS100/NDX/US100/NQ are accepted as aliases for it.
MARKET = os.environ.get("FPERP_MARKET", "hyperliquid|xyz:XYZ100")
SIDE_LEVERAGE = 5             # XYZ100 allows up to 12x
MARGIN_MODE = "isolated"
RR = 1500 / 450               # take profit at 3.33x the stop distance ($100 risk -> ~$333 target)
LIQUIDITY_LOOKBACK = 60       # candles used to find buy/sell-side liquidity
STOP_BUFFER_PCT = 0.0002      # stop sits this far beyond the sweep extreme (~6 pts at 30,000)
MIN_STOP_PCT = 0.0015         # stop never closer than 0.15% from entry (~45 pts at 30,000)
SETUP_EXPIRY_BARS = 15        # a sweep/FVG setup lapses after this many candles
POLL_INTERVAL_SEC = 5         # how often the price is sampled
CANDLE_SEC = 60               # 1-minute candles
QUOTE_SIZE = 0.01             # size used when asking for a price quote
SIZE_STEP = 0.0001            # order size is rounded down to this; replaced at startup from the market's size_decimals
PRICE_TICK = 1.0              # TP/SL prices are rounded to this (Hyperliquid keeps 5 significant figures: 1 pt at ~25,000)

# --- Files / dashboard -----------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "bot_log.jsonl")
STATE_FILE = os.path.join(HERE, "bot_state.json")
RISK_STATE_FILE = os.path.join(HERE, "risk_state.json")
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8765
