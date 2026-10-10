"""Settings for the BTC RSI signal bot. It only reads public prices and alerts you; it never trades."""
import os

SYMBOL = "BTC-USD"                       # Coinbase product (no account or key needed)
TIMEFRAME_MIN = int(os.environ.get("BTC_TF", "5"))   # candle size in minutes: 1, 5, 15 or 60

RSI_LEN = 14                             # RSI period (Wilder)
WATCH_LEVEL = 65.0                       # heads-up when live RSI rises through this ("approaching overbought")
OVERBOUGHT = 70.0                        # SHORT signal level
MODE = os.environ.get("BTC_MODE", "touch")   # "touch": short when a candle closes with RSI >= 70
                                         # "turn":  wait until RSI has been >= 70 and closes back below 70
REARM_BELOW = 50.0                       # one signal per overbought run; re-arms once RSI cools below this

ATR_LEN = 14
SWING_BARS = 10                          # suggested stop: highest high of the last 10 candles...
STOP_ATR_BUFFER = 0.25                   # ...plus 0.25 ATR
TARGET_R = 1.5                           # suggested target: 1.5x the stop distance below entry
MAX_HOLD_BARS = 24                       # the idea expires after this many candles (2 hours on 5-minute candles)

POLL_SEC = 10                            # how often the live price is checked
NTFY_TOPIC = os.environ.get("BTC_NTFY_TOPIC")   # optional phone alerts through the free ntfy app
UI_HOST, UI_PORT = "127.0.0.1", int(os.environ.get("BTC_UI_PORT", "8768"))

HERE = os.path.dirname(os.path.abspath(__file__))
SIGNAL_LOG = os.path.join(HERE, "signals.jsonl")
