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
5. At most one trade per window, weekdays only. Setups expire after SETUP_EXPIRY_BARS or at window end.
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
    if t.weekday() >= 5:  # the Nasdaq cash session is closed at weekends
        return None
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


ASIA_START_HOUR = 19  # New York time; the Asia session runs 19:00-03:00 (Tokyo/Sydney, until London opens)


def asia_session(ts):
    """(session_date, minutes since 19:00 NY) for ts. Sessions start Sunday-Thursday evening."""
    t = dt.datetime.fromtimestamp(ts, NY) - dt.timedelta(hours=ASIA_START_HOUR)
    return t.date(), t.hour * 60 + t.minute


class AsiaSweep:
    """Asia range liquidity sweep (fakeout), on 1-minute candles.

    1. Range: high/low of the first RANGE_MIN minutes of the Asia session (19:00-20:00 NY).
    2. Sweep: until TRADE_UNTIL_MIN, a candle trades beyond one side of the range and closes back inside.
    3. Entry: fade the fakeout on the next price sample. Stop beyond the sweep wick plus
       buf_frac x range width, and at least min_stop_pts from entry. Target = rr x risk.
    4. Flat by EXIT_MIN (03:00 NY). One trade per session. A close beyond the range by a full
       range width means a trend session: stand aside.
    """
    id = "asia_sweep"

    def __init__(self, rr=3.33, range_min=60, trade_until_min=360, exit_min=480, buf_frac=0.1,
                 min_stop_pts=50.0, min_range_pct=0.001):
        self.rr = rr
        self.range_min = range_min
        self.trade_until_min = trade_until_min
        self.exit_min = exit_min
        self.buf_frac = buf_frac
        self.min_stop_pts = min_stop_pts
        self.min_range_pct = min_range_pct
        self.session = None
        self.high = self.low = None
        self.range_bars = 0
        self.pending = None     # sweep waiting for entry
        self.done = False       # traded, skipped or stood aside this session
        self.note = "waiting for the Asia session"

    @property
    def warming_up(self):
        return self.session is None

    def state(self):
        return {"session": str(self.session), "range_high": self.high, "range_low": self.low,
                "range_bars": self.range_bars, "pending": self.pending, "done": self.done, "note": self.note}

    def _range_ok(self):
        return (self.range_bars >= self.range_min * 0.7 and self.high is not None
                and self.high - self.low >= self.min_range_pct * self.high)

    def on_candle(self, c, live=True):
        """live=False while replaying history at startup: an old sweep is never traded late."""
        sdate, mos = asia_session(c.start)
        if mos >= self.exit_min:
            self.pending = None
            return
        if sdate != self.session:
            self.session, self.high, self.low, self.range_bars = sdate, None, None, 0
            self.pending, self.done = None, sdate.weekday() >= 4   # no Friday/Saturday evening sessions
            self.note = "weekend: no session" if self.done else "building range"
        if self.done:
            return
        if mos < self.range_min:
            self.high = c.high if self.high is None else max(self.high, c.high)
            self.low = c.low if self.low is None else min(self.low, c.low)
            self.range_bars += 1
            return
        if not self._range_ok():
            self.done, self.note = True, "range incomplete or too narrow: skipping session"
            return
        if mos >= self.trade_until_min:
            self.done, self.pending, self.note = True, None, "trade window over"
            return
        w = self.high - self.low
        if c.close > self.high + w or c.close < self.low - w:
            self.done, self.pending, self.note = True, None, "trend session: standing aside"
            return
        if self.pending:
            return
        sweep = None
        if c.high > self.high and c.close < self.high:
            sweep = {"side": "short", "wick": c.high}
        elif c.low < self.low and c.close > self.low:
            sweep = {"side": "long", "wick": c.low}
        if sweep:
            if live:
                self.pending, self.note = sweep, f"{sweep['side']} sweep: entering"
            else:
                self.done, self.note = True, "sweep happened before the bot started: skipping session"

    def on_price(self, ts, price):
        p = self.pending
        if p is None or self.done:
            return None
        sdate, mos = asia_session(ts)
        if sdate != self.session or mos >= self.trade_until_min:
            self.pending = None
            return None
        self.pending, self.done = None, True
        buf = self.buf_frac * (self.high - self.low)
        if p["side"] == "short":
            stop = max(p["wick"] + buf, price + self.min_stop_pts)
            target = price - self.rr * (stop - price)
        else:
            stop = min(p["wick"] - buf, price - self.min_stop_pts)
            target = price + self.rr * (price - stop)
        exit_by = ts + (self.exit_min - mos) * 60 - ts % 60
        self.note = f"{p['side']} signal at {price}"
        return {"side": p["side"], "entry": price, "stop": stop, "target": target, "exit_by": exit_by}
