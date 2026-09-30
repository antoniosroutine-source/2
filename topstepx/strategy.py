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
import math
from collections import deque
from dataclasses import dataclass

from levels import SessionTracker, bias


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
