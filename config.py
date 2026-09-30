"""Bot settings. Edit values here; the API key comes from the FPERP_API_KEY env var."""
import os

# --- API ---------------------------------------------------------------------
# Test keys (fp_test_) only work on the sandbox host; live keys (fp_live_) only on the live host.
SANDBOX_URL = "https://sandbox.myfundedperpetuals.com/v1"
LIVE_URL = "https://developers.myfundedperpetuals.com/v1"
API_KEY_ENV = "FPERP_API_KEY"          # sandbox keys start fp_test_, live keys fp_live_
# The host follows the key type unless FPERP_BASE_URL is set.
_KEY = os.environ.get(API_KEY_ENV, "").strip()
BASE_URL = os.environ.get("FPERP_BASE_URL") or (LIVE_URL if _KEY.startswith("fp_live_") else SANDBOX_URL)
ACCOUNT_ID = os.environ.get("FPERP_ACCOUNT_ID")  # required when the key can see more than one account

# --- Account / risk ------------------------------------------------------------
# MFP 1-Step Select rules: 3% daily loss, 3% static max drawdown, 9% profit target, all on the
# starting balance. On $10K: $300 daily, floor at $9,700, target $900.
STARTING_BALANCE = 10_000     # Select $10K; replaced at startup by the account's real starting_balance
MAX_DRAWDOWN_PCT = 0.03       # firm max loss: 3% static ($300)
MAX_DAILY_LOSS_PCT = 0.03     # firm daily loss: 3% ($300)
RISK_BUFFER = 0.75            # bot halts at 75% of each limit ($225), before the firm would
SERVER_ROOM_RESERVE_USD = 50  # also halt if MFP reports less than this left above either firm floor
RISK_PER_TRADE_USD = 100      # loss if the stop is hit: 2 full losses, then the $225 halt blocks a third
MAX_NOTIONAL_MULT = 3.0       # position notional never exceeds 3x equity ($30K); caps risk on tight stops

# --- Strategy ------------------------------------------------------------------
# "asia_sweep" (Asia range liquidity sweep / fakeout) or "silver_bullet" (ICT Silver Bullet, NY hours)
STRATEGY_ID = os.environ.get("FPERP_STRATEGY", "asia_sweep")
# MFP's Nasdaq-100 perpetual is XYZ100 on Hyperliquid. A plain symbol is looked up in MFP's market
# list at startup; NAS100/NDX/US100/NQ are accepted as aliases for it.
MARKET = os.environ.get("FPERP_MARKET", "hyperliquid|xyz:XYZ100")
SIDE_LEVERAGE = 5             # XYZ100 allows up to 12x
MARGIN_MODE = "isolated"
RR = 1500 / 450               # take profit at 3.33x the stop distance ($100 risk -> ~$333 target)
# Asia sweep: range 19:00-20:00 NY, trade sweeps 20:00-01:00, flat by 03:00
ASIA_RANGE_MIN = 60           # minutes after 19:00 NY that form the Asia range
ASIA_TRADE_UNTIL_MIN = 360    # no new entries after 01:00 NY
ASIA_EXIT_MIN = 480           # any open trade is closed at 03:00 NY (London open)
ASIA_BUF_FRAC = 0.1           # stop sits 10% of the range width beyond the sweep wick
ASIA_MIN_STOP_PTS = 50.0      # ...and at least 50 points from entry (keeps costs a small share of risk)
ASIA_MIN_RANGE_PCT = 0.001    # skip sessions whose range is under 0.1% of price
# Silver Bullet
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
