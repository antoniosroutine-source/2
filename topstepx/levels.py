"""Session levels (liquidity), market bias and aggression. All times are New York time."""
import datetime as dt
import threading
from collections import deque

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception as e:  # Windows needs: pip install tzdata
    raise SystemExit("Timezone data missing. Run: pip install tzdata") from e

# name: (start, end) in NY time. Asia wraps past midnight.
SESSIONS = {
    "asia": ("19:00", "02:00"),
    "london": ("02:00", "05:00"),
    "ny_am": ("09:30", "12:00"),
    "ny": ("09:30", "16:00"),
}


def et(ts):
    return dt.datetime.fromtimestamp(ts, ET)


def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def minute_of_day(ts):
    t = et(ts)
    return t.hour * 60 + t.minute


def in_window(ts, start, end):
    """True if ts falls in [start, end) NY time; handles windows that wrap past midnight."""
    m, s, e = minute_of_day(ts), minutes(start), minutes(end)
    return s <= m < e if s < e else (m >= s or m < e)


def trading_day(ts, day_start="18:00"):
    """The futures trading day a timestamp belongs to (a new day starts at 6pm NY time)."""
    shift = 24 * 60 - minutes(day_start)
    return (et(ts) + dt.timedelta(minutes=shift)).date()


class SessionTracker:
    """Keeps session and daily highs/lows up to date, one bar at a time."""

    def __init__(self, day_start="18:00"):
        self.day_start = day_start
        self.groups = {}   # (session, trading_day) -> {"h", "l", "o", "c"}
        self.days = {}     # trading_day -> [high, low]

    def add(self, b):
        td = trading_day(b.t, self.day_start)
        d = self.days.setdefault(td, [b.h, b.l])
        d[0], d[1] = max(d[0], b.h), min(d[1], b.l)
        for name, (s, e) in SESSIONS.items():
            if in_window(b.t, s, e):
                g = self.groups.setdefault((name, td), {"h": b.h, "l": b.l, "o": b.o, "c": b.c})
                g["h"], g["l"], g["c"] = max(g["h"], b.h), min(g["l"], b.l), b.c
        if len(self.days) > 10:   # keep memory bounded
            old = min(self.days)
            del self.days[old]
            for k in [k for k in self.groups if k[1] <= old]:
                del self.groups[k]

    def levels(self, now_ts):
        """Highs/lows of the most recent *completed* sessions and of the previous trading day.

        Returns (levels, info): levels is a list of {"price", "label"}; info holds the NY
        session's open/close for the bias.
        """
        today = trading_day(now_ts, self.day_start)
        out, info = [], {}
        for name, (s, e) in SESSIONS.items():
            done = [td for (n, td) in self.groups
                    if n == name and not (td == today and in_window(now_ts, s, e))]
            if not done:
                continue
            g = self.groups[(name, max(done))]
            out.append({"price": g["h"], "label": f"{name}_high"})
            out.append({"price": g["l"], "label": f"{name}_low"})
            if name == "ny":
                info["ny_open"], info["ny_close"] = g["o"], g["c"]
        prev = [td for td in self.days if td < today]
        if prev:
            h, l = self.days[max(prev)]
            out.append({"price": h, "label": "prev_day_high"})
            out.append({"price": l, "label": "prev_day_low"})
        return out, info


def ema(values, n):
    k, out = 2 / (n + 1), None
    for v in values:
        out = v if out is None else out + k * (v - out)
    return out


def bias(hourly_closes, last_close, close_2d_ago, info, ny_move_min):
    """Context for the log: the NY reaction, the 1-hour trend and the last two days."""
    out = {}
    if "ny_open" in info:
        move = info["ny_close"] - info["ny_open"]
        out["ny_move"] = round(move, 2)
        out["ny"] = "dumped" if move <= -ny_move_min else "rallied" if move >= ny_move_min else "flat"
        out["ny_lean"] = {"dumped": "long (recovery)", "rallied": "short", "flat": "none"}[out["ny"]]
    if len(hourly_closes) >= 20:
        out["h1_trend"] = "up" if last_close > ema(hourly_closes[-60:], 20) else "down"
    if close_2d_ago is not None:
        out["two_day"] = "up" if last_close > close_2d_ago else "down"
    return out


class Aggression:
    """Buy vs sell aggression over a rolling window: (buy - sell) / (buy + sell), from -1 to 1.

    Fed either by the live tape (each trade tagged buy or sell by ProjectX) or, when the tape
    is unavailable and in backtests, by 1-minute bars: a bar's volume counts as buying or
    selling in proportion to where it closed within its range.
    """

    def __init__(self, window_min):
        self.window = window_min * 60
        self.events = deque()   # (ts, signed volume, volume)
        self.lock = threading.Lock()   # the live tape arrives on another thread
        self.since = None       # time of the first event: the reading is only complete after a full window

    def covered(self, now):
        """True once the meter has seen a full window of data (not just a few seconds after connecting)."""
        return self.since is not None and now - self.since >= self.window

    def add_trade(self, ts, volume, is_buy):
        with self.lock:
            if self.since is None:
                self.since = ts
            self.events.append((ts, volume if is_buy else -volume, volume))
            self._trim(ts)

    def add_bar(self, b):
        rng = b.h - b.l
        signed = b.v * (b.c - b.o) / rng if rng > 0 else 0.0
        with self.lock:
            self.events.append((b.t + 60, signed, b.v))
            self._trim(b.t + 60)

    def _trim(self, now):
        while self.events and self.events[0][0] < now - self.window:
            self.events.popleft()

    def ratio(self, now=None):
        with self.lock:
            if now is not None:
                self._trim(now)
            total = sum(e[2] for e in self.events)
            return sum(e[1] for e in self.events) / total if total > 0 else 0.0
