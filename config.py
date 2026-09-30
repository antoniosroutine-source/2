"""Bot settings. Edit values here; the API key comes from the FPERP_API_KEY env var."""
import os

# --- API ---------------------------------------------------------------------
SANDBOX_URL = "https://sandbox.myfundedperpetuals.com/v1"
LIVE_URL = "https://developers.myfundedperpetuals.com/v1"
BASE_URL = os.environ.get("FPERP_BASE_URL", SANDBOX_URL)
API_KEY_ENV = "FPERP_API_KEY"          # sandbox keys start fp_test_, live keys fp_live_
ACCOUNT_ID = os.environ.get("FPERP_ACCOUNT_ID")  # optional; defaults to the first account

# --- Account / risk ------------------------------------------------------------
STARTING_BALANCE = 25_000     # Prime account
MAX_DRAWDOWN_PCT = 0.02       # max loss limit: 2% static ($500)
MAX_DAILY_LOSS_PCT = 0.02     # daily loss cap (same as max loss until a tighter one is chosen)
RISK_BUFFER = 0.75            # bot halts at 75% of each limit ($375), before the firm would
RISK_PER_TRADE_PCT = 0.0025   # loss if the stop is hit: 0.25% of equity (~$62.50)
MAX_NOTIONAL_MULT = 2.0       # position notional never exceeds 2x equity

# --- Strategy ------------------------------------------------------------------
STRATEGY_ID = "silver_bullet"
MARKET = os.environ.get("FPERP_MARKET", "NAS100")  # Nasdaq-100 perp; a plain symbol is looked up
                                                   # in MFP's market list at startup ("venue|SYMBOL" is used as-is)
SIDE_LEVERAGE = 5
MARGIN_MODE = "isolated"
RR = 2.0                      # take profit at 2x the stop distance
LIQUIDITY_LOOKBACK = 60       # candles used to find buy/sell-side liquidity
STOP_BUFFER_PCT = 0.0002      # stop sits this far beyond the sweep extreme (~6 pts at 30,000)
MIN_STOP_PCT = 0.0015         # stop never closer than 0.15% from entry (~45 pts at 30,000)
SETUP_EXPIRY_BARS = 15        # a sweep/FVG setup lapses after this many candles
POLL_INTERVAL_SEC = 5         # how often the price is sampled
CANDLE_SEC = 60               # 1-minute candles
QUOTE_SIZE = 0.01             # size used when asking for a price quote
SIZE_STEP = 0.01              # order size is rounded down to this increment (overridden by market info if MFP provides it)
PRICE_TICK = 0.1              # TP/SL prices are rounded to this increment (overridden by market info if MFP provides it)

# --- Files / dashboard -----------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "bot_log.jsonl")
STATE_FILE = os.path.join(HERE, "bot_state.json")
RISK_STATE_FILE = os.path.join(HERE, "risk_state.json")
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8765
