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
DAILY_MAX_TRADES = None           # no cap (the Asia sweep takes one trade per session by design); e.g. 1 = one per day
LOSS_THRESHOLD_USD = -50.0        # a trade that loses less than this (a scratch) is not a loss
DAILY_PROFIT_STOP_USD = 1500.0    # stop for the day once the day's P&L reaches this
COMBINE_START_BALANCE = 50000.0   # Combine only: near the profit target, the take profit shrinks to what
COMBINE_TARGET_USD = 3000.0       # is still needed (+$50 and fees). Set COMBINE_TARGET_USD = None once funded.

# --- Session (New York time) ----------------------------------------------------------
ENTRY_START = "19:00"             # Asia session: new entries only between these times
ENTRY_END = "02:00"
FLAT_BY = "08:25"                 # any open trade is closed before the 8:30am ET news releases (CPI, jobs)
                                  # and so always well before Topstep's 4:10pm ET cutoff
DAY_START = "18:00"               # the futures trading day starts at 6pm ET

# --- Strategy ------------------------------------------------------------------------
# "level_sweep" (the trader's levels + aggression) or "asia_sweep" (the MFP bot's Asia range fakeout)
STRATEGY = os.environ.get("PX_STRATEGY", "asia_sweep")

# Asia range sweep (STRATEGY = "asia_sweep")
ASIA_RANGE_MIN = 60               # range = the first 60 minutes from 7pm NY
ASIA_TRADE_UNTIL = "01:00"        # no new entries after 1am NY
ASIA_EXIT_AT = "03:00"            # an open trade is closed at 3am NY (London open)
ASIA_BUF_FRAC = 0.1               # stop sits 10% of the range width beyond the sweep wick...
ASIA_MIN_STOP_PTS = 30.0          # ...and at least 30 points from entry
ASIA_MIN_RANGE_PCT = 0.001        # skip sessions whose range is under 0.1% of price
ASIA_RR = 3.33                    # target = 3.33x the stop distance (capped at TARGET_CAP_USD)
ASIA_MIN_CONTRACTS = 1            # smaller sizes are allowed: wide stops still risk <= MAX_RISK_USD
ASIA_LIQ_REACH = 0.5              # >0: if a session high/low (PM high etc.) or a big resting order sits within
                                  # this many range widths beyond the range, fade the sweep of THAT level, not the range
ASIA_NY_BIAS = True               # NY dumped -> Asia longs only; NY rallied -> shorts only; Sunday -> no trade
ASIA_MIN_RANGE_PTS = 33.0         # skip sessions whose 7-8pm range is narrower than this (0 = off)
ASIA_TRAIL = False                # breakeven at +$650, then trail keeping 65% from +$785 (False: pure TP/SL)

SWING_K = 2                       # a swing high/low needs this many bars either side (1-minute bars)
STRUCTURE_LOOKBACK_BARS = 180     # swings older than 3 hours are ignored
CONFIRM_BARS = 3                  # after the sweep, sellers/buyers must take over within this many bars
AGG_WINDOW_MIN = 15               # aggression = buy vs sell volume over the last 15 minutes
AGG_MIN_RATIO = 0.10              # |buy - sell| / total must be at least this to call aggression
NY_MOVE_MIN_PTS = 50.0            # NY session move needed to call it a dump or a rally (bias log)

# --- Gamma levels (free daily feed, computed from QQQ options) ---------------------------------
GAMMA_MODE = "log"                # "off"; "log" = record whether each trade fades toward the gamma flip;
                                  # "filter" = also skip fades aimed away from the flip (0 of 7 won, Apr-Sep 2026)
GAMMA_URL = "https://raw.githubusercontent.com/haus-edge/gex-levels/master/data/gex_QQQ.txt"
GAMMA_REFRESH_SEC = 1800          # check for a new snapshot every 30 minutes (published around midday ET)
GAMMA_MAX_AGE_HOURS = 30          # an older snapshot is ignored

# --- My levels (typed in on the desk page) ----------------------------------------------
MY_LEVEL_NEAR_PTS = 5.0           # alert when price comes this close
MY_LEVEL_HOLD_PTS = 20.0          # a test HELD if price moves this far back away before...
MY_LEVEL_BREAK_PTS = 10.0         # ...trading this far through (then it BROKE)

# --- Order book (live only) ----------------------------------------------------------
DOM_TARGETS = True                # front-run big resting orders between entry and target
WALL_MIN_SIZE = 100               # a "wall" has at least this many contracts resting at one price...
WALL_MULT = 4.0                   # ...and at least 4x the median resting size

# --- Safety -------------------------------------------------------------------------
MAX_BAR_AGE_SEC = 180             # no new entries if the newest completed bar is older than this
MAX_ENTRY_DRIFT_PTS = 5.0         # skip an entry if price moved this far from the signal (confirm mode)

# --- Semi-automatic mode and the alert page ------------------------------------------
CONFIRM_TIMEOUT_SEC = 30          # a signal waits this long for Accept in confirm mode, then expires
UI_HOST = "127.0.0.1"             # the alert page: http://127.0.0.1:8766/
UI_PORT = 8766
NTFY_TOPIC = os.environ.get("PX_NTFY_TOPIC")   # optional phone alerts through the free ntfy.sh app

# --- Recording (tape + order book), to build real order-flow data ---------------------
RECORD = True

# --- Loop and files ------------------------------------------------------------------
POLL_SEC = 3
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "px_log.jsonl")
STATE_FILE = os.path.join(HERE, "px_state.json")          # daily limits survive a restart
RECORD_DIR = os.path.join(HERE, "recordings")             # one gzip file of tape + book per day
DECISIONS_FILE = os.path.join(HERE, "decisions.jsonl")    # every Accept/Reject and "my setup" mark
GAMMA_DIR = os.path.join(HERE, "gamma")                   # one file per daily gamma snapshot
MY_LEVELS_FILE = os.path.join(HERE, "my_levels.json")     # your levels (survive restarts)
MY_LEVELS_LOG = os.path.join(HERE, "my_levels.jsonl")     # every test of every level: HELD or BROKE
