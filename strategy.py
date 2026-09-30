"""ICT Silver Bullet strategy, run on 1-minute candles built from polled prices.

Rules implemented (per Silver Bullet window, New York time 03-04, 10-11, 14-15):
1. Liquidity: buy-side = highest high, sell-side = lowest low of the LOOKBACK candles before
   the current one.
2. Sweep: a candle trades through one of those levels (takes the liquidity).
3. Displacement + fair value gap (FVG) in the opposite direction after the sweep:
     bearish FVG: candle[-3].low > candle[-1].high and candle[-2] closes down
     bullish FVG: candle[-3].high < candle[-1].low and candle[-2] closes up
4. Entry: when price retraces into the FVG. Stop beyond the sweep extreme (plus a small
   buffer, never tighter than MIN_STOP_PCT). Target = RR x risk.
5. At most one trade per window. Setups expire after SETUP_EXPIRY_BARS or at window end.
"""
import datetime as dt
from collections import deque
from dataclasses import dataclass

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception as e:  # Windows needs: pip install tzdata
    raise SystemExit("Timezone data missing. Run: pip install tzdata") from e

SILVER_BULLET_WINDOWS = ((3, 4), (10, 11), (14, 15))  # hours, New York time


@dataclass
class Candle:
    start: float  # unix seconds
    open: float
    high: float
    low: float
    close: float


class CandleBuilder:
    """Groups price samples into fixed-length OHLC candles; update() returns a candle when one completes."""

    def __init__(self, seconds):
        self.seconds = seconds
        self.current = None

    def update(self, ts, price):
        start = ts - ts % self.seconds
        closed = None
        if self.current is not None and start != self.current.start:
            closed = self.current
            self.current = None
        if self.current is None:
            self.current = Candle(start, price, price, price, price)
        else:
            c = self.current
            c.high = max(c.high, price)
            c.low = min(c.low, price)
            c.close = price
        return closed


def window_key(ts, windows=SILVER_BULLET_WINDOWS):
    """(date, start_hour) of the Silver Bullet window containing ts, else None."""
    t = dt.datetime.fromtimestamp(ts, NY)
    for start, end in windows:
        if start <= t.hour < end:
            return (t.date().isoformat(), start)
    return None


class SilverBullet:
    id = "silver_bullet"

    def __init__(self, lookback=60, rr=2.0, stop_buffer_pct=0.0005, min_stop_pct=0.002,
                 setup_expiry_bars=15, windows=SILVER_BULLET_WINDOWS):
        self.lookback = lookback
        self.rr = rr
        self.stop_buffer_pct = stop_buffer_pct
        self.min_stop_pct = min_stop_pct
        self.setup_expiry_bars = setup_expiry_bars
        self.windows = windows
        self.candles = deque(maxlen=lookback + 3)
        self.bar = 0
        self.levels = None      # {"buy_side": float, "sell_side": float}
        self.sweep = None       # {"dir": "short"|"long", "extreme": float, "bar": int}
        self.setup = None       # pending FVG waiting for a retrace
        self.traded_window = None

    @property
    def warming_up(self):
        return len(self.candles) < self.lookback + 1

    def state(self):
        return {"levels": self.levels, "sweep": self.sweep, "setup": self.setup,
                "warming_up": self.warming_up, "traded_window": self.traded_window}

    def on_candle(self, c):
        prior = list(self.candles)[-self.lookback:]
        self.candles.append(c)
        self.bar += 1
        if len(prior) < self.lookback:
            return
        self.levels = {"buy_side": max(x.high for x in prior), "sell_side": min(x.low for x in prior)}

        key = window_key(c.start, self.windows)
        if key is None or key == self.traded_window:
            self.sweep = self.setup = None
            return
        if self.sweep and self.bar - self.sweep["bar"] > self.setup_expiry_bars:
            self.sweep = None
        if self.setup and self.bar - self.setup["bar"] > self.setup_expiry_bars:
            self.setup = None

        # 2. liquidity sweep. A further push extends the active sweep; the displacement away from
        #    it often breaks the opposite level too, which must not flip the bias.
        active = self.sweep["dir"] if self.sweep else None
        if c.high > self.levels["buy_side"] and active in (None, "short"):
            ext = max(c.high, self.sweep["extreme"]) if active else c.high
            self.sweep = {"dir": "short", "extreme": ext, "bar": self.bar}
        elif c.low < self.levels["sell_side"] and active in (None, "long"):
            ext = min(c.low, self.sweep["extreme"]) if active else c.low
            self.sweep = {"dir": "long", "extreme": ext, "bar": self.bar}

        # 3. displacement leaving a fair value gap against the sweep
        if not self.sweep or len(self.candles) < 3:
            return
        a, b, last = list(self.candles)[-3:]
        if self.sweep["dir"] == "short" and a.low > last.high and b.close < b.open:
            self.setup = {"side": "short", "zone_low": last.high, "zone_high": a.low,
                          "stop": self.sweep["extreme"] * (1 + self.stop_buffer_pct), "bar": self.bar}
        elif self.sweep["dir"] == "long" and a.high < last.low and b.close > b.open:
            self.setup = {"side": "long", "zone_low": a.high, "zone_high": last.low,
                          "stop": self.sweep["extreme"] * (1 - self.stop_buffer_pct), "bar": self.bar}

    def on_price(self, ts, price):
        """Called on every price sample. Returns an entry signal dict when price retraces into the FVG."""
        s = self.setup
        key = window_key(ts, self.windows)
        if s is None or key is None or key == self.traded_window:
            return None
        if s["side"] == "short":
            if price >= s["stop"]:
                self.setup = None  # invalidated before entry
                return None
            if price < s["zone_low"]:
                return None
            stop = max(s["stop"], price * (1 + self.min_stop_pct))
            target = price - self.rr * (stop - price)
        else:
            if price <= s["stop"]:
                self.setup = None
                return None
            if price > s["zone_high"]:
                return None
            stop = min(s["stop"], price * (1 - self.min_stop_pct))
            target = price + self.rr * (price - stop)
        self.traded_window = key
        self.setup = self.sweep = None
        return {"side": s["side"], "entry": price, "stop": stop, "target": target}
