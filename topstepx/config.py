"""Settings for the TopstepX bot. Credentials come from environment variables, never this file."""
import os

# --- ProjectX / TopstepX connection -------------------------------------------------
API_URL = "https://api.topstepx.com"
MARKET_HUB_URL = "wss://rtc.topstepx.com/hubs/market"
USERNAME_ENV = "PX_USERNAME"      # your TopstepX username (not your email)
API_KEY_ENV = "PX_API_KEY"        # from TopstepX Settings > API
ACCOUNT_ID = os.environ.get("PX_ACCOUNT_ID")   # required when the key sees more than one account
LIVE_DATA = False                 # Topstep Combine/Express accounts use the sim data subscription

# --- Instrument -------------------------------------------------------------------
SYMBOL_SEARCH = "MNQ"
SYMBOL_ID = "F.US.MNQ"            # Micro E-mini Nasdaq-100
TICK_SIZE = 0.25                  # replaced at startup from the contract
POINT_VALUE = 2.0                 # $ per point per MNQ; replaced at startup (tickValue / tickSize)
FEE_PER_CONTRACT_RT = 0.74        # round-turn fees per MNQ, used for risk and backtests

# --- Risk (the trader's rules) ---------------------------------------------------------
MAX_RISK_USD = 500.0              # loss at the stop, fees included, is never more than this
MIN_CONTRACTS = 10
MAX_CONTRACTS = 20
FIXED_STOP_PTS = 20.0             # strict stop distance on every trade (None = stop at the structure)
MIN_STOP_PTS = 10.0               # no ultra-tight stops: noise stops them out, and Topstep bans exploiting tight SIM brackets
STOP_BUFFER_TICKS = 2             # stop sits this far beyond the manipulation high/low
BREAKEVEN_AT_USD = 650.0          # open profit that moves the stop to breakeven
BE_OFFSET_TICKS = 1               # breakeven stop sits 1 tick in profit to cover fees
TRAIL_AT_USD = 785.0              # open profit that starts the trail
TRAIL_KEEP = 0.65                 # trail keeps 65% of the best open profit
TARGET_CAP_USD = 1500.0           # take profit never more than this (Topstep consistency rule)
MIN_TARGET_USD = 800.0            # skip trades whose target pays less (BE and trail must be reachable)
DAILY_MAX_LOSSES = 1              # stop for the day after one losing trade
LOSS_THRESHOLD_USD = -50.0        # a trade that loses less than this (a scratch) is not a loss
DAILY_PROFIT_STOP_USD = 1500.0    # stop for the day once the day's P&L reaches this

# --- Session (New York time) ----------------------------------------------------------
ENTRY_START = "19:00"             # Asia session: new entries only between these times
ENTRY_END = "02:00"
FLAT_BY = "16:05"                 # flatten before Topstep's 4:10pm ET (3:10pm CT) cutoff
DAY_START = "18:00"               # the futures trading day starts at 6pm ET

# --- Strategy ------------------------------------------------------------------------
SWING_K = 2                       # a swing high/low needs this many bars either side (1-minute bars)
STRUCTURE_LOOKBACK_BARS = 180     # swings older than 3 hours are ignored
CONFIRM_BARS = 3                  # after the sweep, sellers/buyers must take over within this many bars
AGG_WINDOW_MIN = 15               # aggression = buy vs sell volume over the last 15 minutes
AGG_MIN_RATIO = 0.10              # |buy - sell| / total must be at least this to call aggression
NY_MOVE_MIN_PTS = 50.0            # NY session move needed to call it a dump or a rally (bias log)

# --- Order book (live only) ----------------------------------------------------------
DOM_TARGETS = True                # front-run big resting orders between entry and target
WALL_MIN_SIZE = 100               # a "wall" has at least this many contracts resting at one price...
WALL_MULT = 4.0                   # ...and at least 4x the median resting size

# --- Loop and files ------------------------------------------------------------------
POLL_SEC = 3
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "px_log.jsonl")
STATE_FILE = os.path.join(HERE, "px_state.json")
