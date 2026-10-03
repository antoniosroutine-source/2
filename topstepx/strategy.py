"""Level sweep strategy: follow the aggression, sell the manipulation high / buy the manipulation low.

On 1-minute bars (New York time):
1. Aggression decides direction: sellers (buy-sell volume ratio <= -AGG_MIN_RATIO over the last
   15 minutes) allow only shorts; buyers allow only longs; neither means no trade.
2. Push: the latest swing low is a new low (lower than the swing low before it) for shorts,
   or the latest swing high is a new high for longs.
3. Consolidation: at least one swing high forms after that push low (swing low after a push high).
4. Manipulation: a bar trades above the consolidation high and closes back below it (a stop hunt),
   without the pullback retracing past where the push started. Mirror image for longs.
5. Confirmation: within CONFIRM_BARS, a bar closes beyond the manipulation bar's other end while
   the aggression still agrees. That close is the entry. Never on a breakout.
6. Stop a fixed FIXED_STOP_PTS from entry (or, if that is None, beyond the manipulation extreme);
   size so the loss at the stop is <= MAX_RISK_USD.
7. Target: the new low (just past the push low) or the next liquidity level beyond it, the first
   that pays at least MIN_TARGET_USD, capped at TARGET_CAP_USD; front-runs big resting orders.
"""
import datetime as dt
import math
from collections import deque
from dataclasses import dataclass

from levels import SessionTracker, bias, et


@dataclass
class Bar:
    t: float        # bar start, unix seconds
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


class LevelSweep:
    id = "level_sweep"

    def __init__(self, p, tick_size, point_value):
        self.p = p
        self.tick = tick_size
        self.pv = point_value
        self.bars = deque(maxlen=3 * 1440 + 10)
        self.n = -1                  # index of the latest bar
        self.highs = []              # swing highs: (bar index, price)
        self.lows = []
        self.sessions = SessionTracker(p.DAY_START)
        self.hourly = {}
        self.pending = None
        self.levels = []
        self.bias = {}
        self.note = "warming up"

    # -- helpers ------------------------------------------------------------------
    def round_tick(self, price, up=None):
        steps = price / self.tick
        steps = math.ceil(steps - 1e-9) if up else math.floor(steps + 1e-9) if up is False else round(steps)
        return round(steps * self.tick, 10)

    def _bar(self, idx):
        return self.bars[idx - self.n - 1]

    def _pivots(self):
        k = self.p.SWING_K
        if len(self.bars) < 2 * k + 1:
            return
        mid = self.n - k
        window = [self._bar(i) for i in range(mid - k, mid + k + 1)]
        m = window[k]
        if m.h > max(b.h for b in window[:k]) and m.h >= max(b.h for b in window[k + 1:]):
            self.highs.append((mid, m.h))
        if m.l < min(b.l for b in window[:k]) and m.l <= min(b.l for b in window[k + 1:]):
            self.lows.append((mid, m.l))
        oldest = self.n - self.p.STRUCTURE_LOOKBACK_BARS
        self.highs = [s for s in self.highs if s[0] >= oldest]
        self.lows = [s for s in self.lows if s[0] >= oldest]

    def _swept_since(self, idx, price, side):
        """True if any bar after idx (before the current bar) already traded through price."""
        for i in range(idx + 1, self.n):
            b = self._bar(i)
            if (side == "short" and b.h > price) or (side == "long" and b.l < price):
                return True
        return False

    def structure(self, side):
        """The push extreme, the consolidation level to be swept, and where the push started."""
        pushes, counters = (self.lows, self.highs) if side == "short" else (self.highs, self.lows)
        if len(pushes) < 2:
            return None
        (p_idx, push), (_, before) = pushes[-1], pushes[-2]
        if (side == "short" and push >= before) or (side == "long" and push <= before):
            return None                                   # no new low/high: no push
        after = [s for s in counters if s[0] > p_idx]
        if not after:
            return None                                   # no consolidation yet
        ref_idx, ref = (max if side == "short" else min)(after, key=lambda s: s[1])
        origin = [s for s in counters if s[0] < p_idx]
        start = origin[-1][1] if origin else None
        if start is not None and ((side == "short" and ref >= start) or (side == "long" and ref <= start)):
            return None                                   # retraced the whole push
        if self._swept_since(ref_idx + self.p.SWING_K, ref, side):
            return None
        return {"push": push, "push_idx": p_idx, "ref": ref, "start": start}

    # -- main entry point ---------------------------------------------------------
    def on_bar(self, b, agg, walls=None, live=True):
        """Feed one completed 1-minute bar. Returns an entry signal dict or None.

        agg is the current aggression ratio (-1 sellers .. +1 buyers). walls(side) may return
        prices of big resting orders the target should front-run. live=False replays history
        without arming setups.
        """
        self.bars.append(b)
        self.n += 1
        self.sessions.add(b)
        self.hourly[int(b.t // 3600)] = b.c
        if len(self.hourly) > 100:
            del self.hourly[min(self.hourly)]
        self._pivots()
        if self.n % 15 == 0 or not self.levels:
            self.levels, info = self.sessions.levels(b.t + 60)
            old = next((x.c for x in self.bars if x.t >= b.t - 2 * 86400), None)
            closes = [self.hourly[k] for k in sorted(self.hourly)]
            self.bias = bias(closes, b.c, old if self.bars[0].t <= b.t - 2 * 86400 else None,
                             info, self.p.NY_MOVE_MIN_PTS)
        if not live:
            self.pending = None
            return None

        sellers = agg <= -self.p.AGG_MIN_RATIO
        buyers = agg >= self.p.AGG_MIN_RATIO
        pend = self.pending
        if pend:
            short = pend["side"] == "short"
            pend["extreme"] = max(pend["extreme"], b.h) if short else min(pend["extreme"], b.l)
            if self.n - pend["bar"] > self.p.CONFIRM_BARS:
                self.pending, self.note = None, "setup expired: no follow-through"
            elif (short and b.c > pend["ref"]) or (not short and b.c < pend["ref"]):
                self.pending, self.note = None, "setup failed: price held beyond the level"
            elif (short and b.c < pend["trigger"] and sellers) or (not short and b.c > pend["trigger"] and buyers):
                self.pending = None
                return self._signal(pend, b, agg, walls)
            return None

        for side, ok in (("short", sellers), ("long", buyers)):
            if not ok:
                continue
            s = self.structure(side)
            if not s:
                continue
            if side == "short" and b.h > s["ref"] and b.c < s["ref"]:
                self.pending = {"side": side, "bar": self.n, "ref": s["ref"], "extreme": b.h,
                                "trigger": b.l, "push": s["push"]}
            elif side == "long" and b.l < s["ref"] and b.c > s["ref"]:
                self.pending = {"side": side, "bar": self.n, "ref": s["ref"], "extreme": b.l,
                                "trigger": b.h, "push": s["push"]}
            if self.pending:
                self.note = f"{side} manipulation at {s['ref']}: waiting for confirmation"
                return None
        self.note = ("sellers in control" if sellers else "buyers in control" if buyers
                     else "no clear aggression") + ": watching for a manipulation"
        return None

    def _signal(self, pend, b, agg, walls):
        p, side = self.p, pend["side"]
        d = -1 if side == "short" else 1
        entry = b.c
        stop = self.round_tick(pend["extreme"] - d * p.STOP_BUFFER_TICKS * self.tick, up=(side == "short"))
        if getattr(p, "FIXED_STOP_PTS", None):
            stop = self.round_tick(entry - d * p.FIXED_STOP_PTS, up=(side == "short"))
        elif (entry - stop) * d < p.MIN_STOP_PTS:
            stop = self.round_tick(entry - d * p.MIN_STOP_PTS, up=(side == "short"))
        stop_pts = (entry - stop) * d
        size = min(p.MAX_CONTRACTS, int(p.MAX_RISK_USD // (stop_pts * self.pv + p.FEE_PER_CONTRACT_RT)))
        base = {"side": side, "entry": entry, "stop": stop, "stop_pts": stop_pts, "agg": round(agg, 3),
                "bias": self.bias, "ref": pend["ref"]}
        if size < p.MIN_CONTRACTS:
            self.note = f"{side} skipped: {stop_pts:.2f}-pt stop is too wide for $500 at {p.MIN_CONTRACTS} MNQ"
            return {**base, "skip": self.note}

        # candidate targets: just past the push extreme (the new low/high), then liquidity beyond it
        new_extreme = pend["push"] + d * self.tick
        pool = [x["price"] for x in self.levels] + [s[1] for s in (self.lows if side == "short" else self.highs)]
        beyond = sorted({x - d * self.tick for x in pool if (x - pend["push"]) * d > 0}, key=lambda x: (x - entry) * d)
        cands = [new_extreme] + beyond
        per_pt = size * self.pv
        cap = self.round_tick(entry + d * p.TARGET_CAP_USD / per_pt, up=(side == "short"))
        target = next((c for c in cands if (c - entry) * d * per_pt >= p.MIN_TARGET_USD), cap)
        if (target - cap) * d > 0:
            target = cap
        if walls:
            for w in sorted(walls(side), key=lambda x: (x - entry) * d):
                front = w - d * self.tick
                if 0 < (front - entry) * d < (target - entry) * d and (front - entry) * d * per_pt >= p.MIN_TARGET_USD:
                    target = front
                    break
        reward = (target - entry) * d * per_pt
        if reward < p.MIN_TARGET_USD:
            self.note = f"{side} skipped: target pays ${reward:.0f}, under ${p.MIN_TARGET_USD:.0f}"
            return {**base, "skip": self.note}
        self.note = f"{side} signal at {entry}"
        return {**base, "size": size, "target": target,
                "risk_usd": round(size * (stop_pts * self.pv + p.FEE_PER_CONTRACT_RT), 2),
                "reward_usd": round(reward, 2)}

    def state(self):
        return {"note": self.note, "pending": self.pending, "bias": self.bias,
                "levels": self.levels, "swing_highs": self.highs[-3:], "swing_lows": self.lows[-3:]}


def asia_clock(ts, start="19:00"):
    """(session date, minutes since the Asia session started) for ts, in New York time."""
    import datetime as dt
    from levels import ET, minutes
    t = dt.datetime.fromtimestamp(ts, ET) - dt.timedelta(minutes=minutes(start))
    return t.date(), t.hour * 60 + t.minute


class AsiaRangeSweep:
    """Asia range liquidity sweep (fakeout), ported from the MFP bot. 1-minute bars, New York time.

    1. Range: high/low of the first ASIA_RANGE_MIN minutes of the session (19:00-20:00).
    2. Sweep: until ASIA_TRADE_UNTIL (01:00) a bar trades beyond one side of the range and closes
       back inside: fade it at that close. Never on a breakout; a close a full range width outside
       means a trend session, and the session is skipped.
    3. Stop beyond the sweep wick plus ASIA_BUF_FRAC of the range width, at least ASIA_MIN_STOP_PTS
       from entry. Target ASIA_RR x the stop distance, capped at TARGET_CAP_USD.
    4. One trade per session; no Friday, Saturday or Sunday evening sessions (Sunday nights lost money
       in the backtest); flat by ASIA_EXIT_AT (03:00).
    Filters (council review): ASIA_NY_BIAS - trade against that day's NY session (NY dumped -> longs
    only, NY rallied -> shorts only; no NY session, i.e. Sunday -> no trade); the session's first sweep
    decides, so a sweep against the bias ends the session. ASIA_MIN_RANGE_PTS - skip narrow ranges.
    Size: as many MNQ as keep the loss at the stop, fees included, <= MAX_RISK_USD.
    """
    id = "asia_sweep"

    def __init__(self, p, tick_size, point_value):
        self.p, self.tick, self.pv = p, tick_size, point_value
        self.session = None
        self.high = self.low = None
        self.range_bars = 0
        self.done = False
        self.levels, self.bias = [], {}
        self.liq = []                    # untaken session highs/lows (NY, PM, London, previous day)
        self.sessions = SessionTracker(p.DAY_START)
        self.seen_hi = self.seen_lo = None   # session extremes after the range, before this bar
        self.note = "waiting for the Asia session"
        from levels import minutes
        self.until = minutes(p.ASIA_TRADE_UNTIL) - minutes("19:00") + 24 * 60
        self.exit_min = minutes(p.ASIA_EXIT_AT) - minutes("19:00") + 24 * 60

    round_tick = LevelSweep.round_tick

    def on_bar(self, b, agg, walls=None, live=True):
        self.sessions.add(b)
        sdate, mos = asia_clock(b.t)
        if mos >= self.exit_min:
            self.note = "waiting for the next Asia session (7pm NY)"
            return None
        if sdate != self.session:
            self.session, self.high, self.low, self.range_bars = sdate, None, None, 0
            self.seen_hi = self.seen_lo = None
            self.liq = [lv["price"] for lv in self.sessions.levels(b.t)[0]]
            self.bias = self._ny_bias(sdate)
            self.done = sdate.weekday() >= 4      # Friday, Saturday and Sunday evenings: no session
            self.note = "weekend: no session" if self.done else "building the Asia range"
        if self.done:
            return None
        p = self.p
        if mos < p.ASIA_RANGE_MIN:
            self.high = b.h if self.high is None else max(self.high, b.h)
            self.low = b.l if self.low is None else min(self.low, b.l)
            self.range_bars += 1
            self.levels = [{"price": self.high, "label": "asia_range_high"},
                           {"price": self.low, "label": "asia_range_low"}]
            return None
        if self.range_bars < p.ASIA_RANGE_MIN * 0.7 or self.high - self.low < p.ASIA_MIN_RANGE_PCT * self.high:
            self.done, self.note = True, "range incomplete or too narrow: skipping the session"
            return None
        min_w = getattr(p, "ASIA_MIN_RANGE_PTS", 0) or 0
        if self.high - self.low < min_w:
            self.done = True
            self.note = f"range {self.high - self.low:.2f} pts is under {min_w:g}: skipping the session"
            return None
        if mos >= self.until:
            self.done, self.note = True, "trade window over"
            return None
        w = self.high - self.low
        hi, lo = self._fade_levels(w, walls if live else None)
        self.seen_hi = b.h if self.seen_hi is None else max(self.seen_hi, b.h)
        self.seen_lo = b.l if self.seen_lo is None else min(self.seen_lo, b.l)
        if b.c > hi + w or b.c < lo - w:
            self.done, self.note = True, "trend session: standing aside"
            return None
        side = None
        if b.h > hi and b.c < hi:
            side, wick = "short", b.h
        elif b.l < lo and b.c > lo:
            side, wick = "long", b.l
        if not side:
            extra = (f" (waiting for the liquidity at {hi:.2f})" if hi != self.high else "") + \
                    (f" (waiting for the liquidity at {lo:.2f})" if lo != self.low else "")
            self.note = f"range {self.low:.2f}-{self.high:.2f}: watching for a sweep{extra}"
            return None
        self.done = True
        if not live:
            self.note = "a sweep happened before the bot started: skipping the session"
            return None
        allow = self.bias.get("allow", "both")
        if allow != "both" and allow != side:
            only = "no trade" if allow == "none" else f"{allow}s only"
            self.note = f"{side} sweep skipped: {self.bias.get('why')} ({only}); done for the session"
            return {"side": side, "entry": b.c, "bias": self.bias, "strategy": self.id, "skip": self.note}
        return self._signal(side, wick, b, agg)

    def _ny_bias(self, sdate):
        """Today's NY session (09:30-16:00) move decides the Asia direction: NY dump -> Asia recovery."""
        g = self.sessions.groups.get(("ny", sdate))
        if not g:
            info = {"why": "no NY session today", "ny_move": None}
        else:
            move = round(g["c"] - g["o"], 2)
            info = {"ny_open": g["o"], "ny_close": g["c"], "ny_move": move,
                    "why": f"NY {'dumped' if move < 0 else 'rallied' if move > 0 else 'closed flat'} {abs(move):.2f} pts"}
        if not getattr(self.p, "ASIA_NY_BIAS", False):
            return {**info, "allow": "both"}
        m = info["ny_move"]
        return {**info, "allow": "long" if m is not None and m < 0 else "short" if m is not None and m > 0 else "none"}

    def _fade_levels(self, w, walls):
        """The prices to fade. Normally the range high/low; but if untaken liquidity (a session
        high/low such as the PM high, or a big resting order) sits just beyond the range, price is
        drawn to it: wait for that level to be swept instead of fading the range early."""
        reach = getattr(self.p, "ASIA_LIQ_REACH", 0) * w
        if reach <= 0:
            return self.high, self.low
        hi_taken = max(self.high, self.seen_hi or self.high)
        lo_taken = min(self.low, self.seen_lo or self.low)
        above = [x for x in self.liq if hi_taken < x <= self.high + reach]
        below = [x for x in self.liq if self.low - reach <= x < lo_taken]
        if walls:
            above += [x for x in walls("long") if hi_taken < x <= self.high + reach]
            below += [x for x in walls("short") if self.low - reach <= x < lo_taken]
        return (min(above) if above else self.high), (max(below) if below else self.low)

    def _signal(self, side, wick, b, agg):
        p = self.p
        d = -1 if side == "short" else 1
        entry = b.c
        buf = p.ASIA_BUF_FRAC * (self.high - self.low)
        stop = wick - d * buf
        if (entry - stop) * d < p.ASIA_MIN_STOP_PTS:
            stop = entry - d * p.ASIA_MIN_STOP_PTS
        stop = self.round_tick(stop, up=(side == "short"))
        stop_pts = (entry - stop) * d
        size = min(p.MAX_CONTRACTS, int(p.MAX_RISK_USD // (stop_pts * self.pv + p.FEE_PER_CONTRACT_RT)))
        base = {"side": side, "entry": entry, "stop": stop, "stop_pts": stop_pts, "agg": round(agg, 3),
                "bias": self.bias, "ref": self.high if side == "short" else self.low, "strategy": self.id}
        if size < p.ASIA_MIN_CONTRACTS:
            self.note = f"{side} skipped: {stop_pts:.2f}-pt stop is too wide for ${p.MAX_RISK_USD:.0f}"
            return {**base, "skip": self.note}
        per_pt = size * self.pv
        reward_pts = min(p.ASIA_RR * stop_pts, p.TARGET_CAP_USD / per_pt)
        target = self.round_tick(entry + d * reward_pts, up=(side == "short"))
        ny_end = (b.t // 60) * 60 + (self.exit_min - asia_clock(b.t)[1]) * 60
        self.note = f"{side} signal at {entry}"
        return {**base, "size": size, "target": target, "exit_by": ny_end,
                "risk_usd": round(size * (stop_pts * self.pv + p.FEE_PER_CONTRACT_RT), 2),
                "reward_usd": round((target - entry) * d * per_pt, 2)}

    def state(self):
        return {"note": self.note, "session": str(self.session), "range_high": self.high,
                "range_low": self.low, "done": self.done, "bias": self.bias}


class NYOpenEMA:
    """NY open: the first 5-minute candle (09:30-09:34 NY) vs the 12 EMA of 5-minute closes.

    Close above the EMA -> long, below -> short, at the 09:35 open. Initial stop beyond the candle's far
    end (1 tick); size so the loss at the stop incl. fees <= MAX_RISK_USD (max MAX_CONTRACTS). After each
    completed 5-minute candle the stop trails to the EMA (1 tick beyond), never loosening. Profit capped at
    TARGET_CAP_USD; flat at FLAT_BY (15:55). One trade per day. Pre-registered in audit/NYOPEN-PREREG.md.
    """
    id = "ny_open"

    def __init__(self, p, tick_size, point_value):
        self.p, self.tick, self.pv = p, tick_size, point_value
        self.alpha = 2 / (p.NYO_EMA + 1)
        self.k = None                 # current 5-minute bucket (unix // 300)
        self.cur = None               # [o, h, l, c, bars]
        self.closed_k = None
        self.ema = None               # EMA after the last COMPLETED 5-minute candle
        self.done_day = None
        self.levels, self.bias = [], {}
        self.candle = None
        self.note = "waiting for the 9:30 NY open"

    round_tick = LevelSweep.round_tick

    def on_bar(self, b, agg, walls=None, live=True):
        k = int(b.t // 300)
        if k != self.k:
            if self.cur and self.closed_k != self.k:
                self._close(self.k, live)          # a bucket whose last minute never printed
            self.k, self.cur = k, [b.o, b.h, b.l, b.c, 0]
        else:
            self.cur[1], self.cur[2], self.cur[3] = max(self.cur[1], b.h), min(self.cur[2], b.l), b.c
        self.cur[4] += 1
        if b.t + 60 >= (k + 1) * 300 and self.closed_k != k:
            return self._close(k, live)
        return None

    def _close(self, k, live):
        self.closed_k = k
        o, h, l, c, n = self.cur
        self.ema = c if self.ema is None else self.ema + self.alpha * (c - self.ema)
        t = et(k * 300)
        if t.weekday() >= 5 or (t.hour, t.minute) != (9, 30):
            return None
        d = t.date()
        if self.done_day == d:
            return None
        self.done_day = d
        self.candle = {"o": o, "h": h, "l": l, "c": c, "ema": round(self.ema, 2)}
        self.levels = [{"price": h, "label": "first_5m_high"}, {"price": l, "label": "first_5m_low"}]
        side = "long" if c > self.ema else "short" if c < self.ema else None
        self.bias = {"why": f"first 5-min candle closed {c:.2f} vs 12 EMA {self.ema:.2f}",
                     "allow": side or "none"}
        if n < 4 or not side:
            self.note = "first candle incomplete or on the EMA: no trade today"
            return None
        if not live:
            self.note = "the 9:30 candle closed before the bot started: no trade today"
            return None
        p = self.p
        dd = 1 if side == "long" else -1
        stop = self.round_tick(l - self.tick if dd > 0 else h + self.tick)
        stop_pts = (c - stop) * dd
        size = min(p.MAX_CONTRACTS, int(p.MAX_RISK_USD // (stop_pts * self.pv + p.FEE_PER_CONTRACT_RT))) if stop_pts > 0 else 0
        base = {"side": side, "entry": c, "stop": stop, "stop_pts": stop_pts, "agg": 0.0, "bias": self.bias,
                "ref": self.ema, "strategy": self.id, "trail": "ema"}
        if size < 1:
            self.note = f"{side} skipped: {stop_pts:.2f}-pt stop is too wide for ${p.MAX_RISK_USD:.0f}"
            return {**base, "skip": self.note}
        target = self.round_tick(c + dd * p.TARGET_CAP_USD / (size * self.pv), up=(dd < 0))
        flat = dt.datetime.combine(d, dt.time(*map(int, p.FLAT_BY.split(":"))), tzinfo=t.tzinfo).timestamp()
        self.note = f"{side} signal at {c}"
        return {**base, "size": size, "target": target, "exit_by": flat,
                "risk_usd": round(size * (stop_pts * self.pv + p.FEE_PER_CONTRACT_RT), 2),
                "reward_usd": round((target - c) * dd * size * self.pv, 2)}

    def trail_stop(self, side):
        """Where the EMA trail puts the stop now (from completed 5-minute candles only)."""
        if self.ema is None:
            return None
        dd = 1 if side == "long" else -1
        return self.round_tick(self.ema - dd * self.tick, up=(dd < 0))

    def state(self):
        c = self.candle or {}
        return {"note": self.note, "bias": self.bias, "range_high": c.get("h"), "range_low": c.get("l"),
                "ema": None if self.ema is None else round(self.ema, 2)}


def make_strategy(p, tick_size, point_value):
    return {"level_sweep": LevelSweep, "asia_sweep": AsiaRangeSweep, "ny_open": NYOpenEMA}[p.STRATEGY](p, tick_size, point_value)
