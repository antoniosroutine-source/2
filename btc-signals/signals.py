"""RSI overbought SHORT signals for BTC.

Rules (on closed candles, so a signal never disappears afterwards):
- WATCH: the live RSI of the forming candle rises through WATCH_LEVEL (65): overbought is near.
- SHORT, MODE "touch": a candle closes with RSI >= OVERBOUGHT (70).
  SHORT, MODE "turn":  RSI has closed >= 70 and then a candle closes back below 70 (momentum turning).
- One signal per overbought run: after a SHORT the engine waits until RSI closes below REARM_BELOW (50).
- Each SHORT comes with levels: stop = highest high of the last SWING_BARS candles + STOP_ATR_BUFFER x ATR;
  target = entry - TARGET_R x (stop - entry). The idea expires after MAX_HOLD_BARS candles.
"""
from collections import deque
from dataclasses import dataclass

from indicators import ATR, RSI


@dataclass
class Candle:
    t: float          # start, unix seconds
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


class SignalEngine:
    def __init__(self, p):
        self.p = p
        self.rsi = RSI(p.RSI_LEN)
        self.atr = ATR(p.ATR_LEN)
        self.highs = deque(maxlen=p.SWING_BARS)
        self.armed = True
        self.watch_sent = False
        self.overbought = False
        self.last = None

    def on_candle(self, c):
        """A closed candle. Returns a list of events (dicts)."""
        r = self.rsi.update(c.c)
        a = self.atr.update(c.h, c.l, c.c)
        self.highs.append(c.h)
        self.last = c
        ev = []
        if r is None or a is None:
            return ev
        p = self.p
        if r < p.REARM_BELOW and not self.armed:
            self.armed, self.watch_sent, self.overbought = True, False, False
            ev.append({"kind": "rearmed", "t": c.t, "rsi": round(r, 1)})
        if not self.armed:
            return ev
        if r >= p.OVERBOUGHT:
            if p.MODE == "touch":
                ev.append(self._short(c, r, a, f"RSI closed at {r:.1f} (>= {p.OVERBOUGHT:g})"))
            elif not self.overbought:
                self.overbought = True
                ev.append({"kind": "overbought", "t": c.t, "rsi": round(r, 1), "price": c.c,
                           "text": f"RSI {r:.1f} overbought: waiting for it to turn back below {p.OVERBOUGHT:g}"})
        elif self.overbought and p.MODE == "turn":
            ev.append(self._short(c, r, a, f"RSI turned down from overbought, closed at {r:.1f}"))
        return ev

    def _short(self, c, r, a, why):
        p = self.p
        self.armed, self.overbought = False, False
        entry = c.c
        stop = max(self.highs) + p.STOP_ATR_BUFFER * a
        stop = max(stop, entry + 0.5 * a)              # never closer than half an ATR
        risk = stop - entry
        return {"kind": "short", "t": c.t, "price": round(entry, 2), "rsi": round(r, 1), "why": why,
                "stop": round(stop, 2), "target": round(entry - p.TARGET_R * risk, 2),
                "risk_pct": round(risk / entry * 100, 3), "atr": round(a, 2),
                # with a payout multiplier M a stake is gone after a 1/M move: above this the stop is never reached
                "max_mult": int(100 / (risk / entry * 100)) if risk > 0 else None,
                "expires_bars": p.MAX_HOLD_BARS}

    def live(self, price):
        """The forming candle's price: live RSI, and a WATCH event the first time it nears overbought."""
        r = self.rsi.peek(price)
        if r is None:
            return r, None
        if self.armed and not self.watch_sent and r >= self.p.WATCH_LEVEL:
            self.watch_sent = True
            return r, {"kind": "watch", "rsi": round(r, 1), "price": round(price, 2),
                       "text": f"BTC RSI {r:.1f} approaching overbought ({self.p.OVERBOUGHT:g}). Get ready for a short."}
        return r, None
