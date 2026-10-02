"""Audit engine: the frozen strategy (strategy.AsiaRangeSweep at the pre-registered commit) with a
corrected measurement layer. Nothing here changes entry/exit rules, parameters or filters.

Step flags (applied cumulatively in the ledger):
  next_open   1.2 entry at the next bar's open + 1 tick (bracket distances kept from the signal)
  stop2       1.3 stops fill at stop + 2 ticks; gaps at the bar open + 1 tick adverse
  through     1.4 targets fill only when price trades 1 tick through
  lookahead   1.5 assert every level/bias/range source bar closed before the decision bar
  calendar    1.7 drop sessions on NYSE holidays, early closes and CME closure eves
"""
import datetime as dt
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import config  # noqa: E402
import levels  # noqa: E402
import strategy  # noqa: E402
from levels import et, trading_day  # noqa: E402
from manage import DayGuard  # noqa: E402

TICK, PV = 0.25, 2.0


def params(**over):
    p = {k: getattr(config, k) for k in dir(config) if k.isupper()}
    p.update(STRATEGY="asia_sweep")
    p.update(over)
    return types.SimpleNamespace(**p)


# ---- 1.5 look-ahead instrumentation -----------------------------------------------------
_orig_add = levels.SessionTracker.add


def _add(self, b):
    _orig_add(self, b)
    td = trading_day(b.t, self.day_start)
    for name, (s, e) in levels.SESSIONS.items():
        if levels.in_window(b.t, s, e):
            self.groups[(name, td)]["last_t"] = b.t
    self.__dict__.setdefault("day_last", {})[td] = b.t


levels.SessionTracker.add = _add


class Checked(strategy.AsiaRangeSweep):
    """The frozen strategy plus assertions that every input closed before the decision bar."""
    checks = 0

    def on_bar(self, b, agg, walls=None, live=True):
        self._bar_t = b.t
        if self.range_bars and strategy.asia_clock(b.t)[1] < self.p.ASIA_RANGE_MIN:
            self._range_last = b.t
        return super().on_bar(b, agg, walls, live)

    def _signal(self, side, wick, b, agg):
        t = b.t   # the decision bar (its close is the signal)
        g = self.sessions.groups
        sd = self.session
        ny = g.get(("ny", sd))
        assert ny is None or ny["last_t"] + 60 <= t, "NY bias uses a bar that had not closed"
        assert getattr(self, "_range_last", 0) + 60 <= t, "range uses a bar that had not closed"
        # levels were taken at the session's first bar from *completed* sessions only
        start = dt.datetime.combine(sd, dt.time(19, 0), tzinfo=levels.ET).timestamp()
        for (name, td), grp in g.items():
            if grp["h"] in self.liq or grp["l"] in self.liq:
                if grp["h"] in self.liq and grp["last_t"] >= start and grp["h"] not in self._legit(start):
                    raise AssertionError(f"level {name} {td} not complete at session start")
        Checked.checks += 1
        return super()._signal(side, wick, b, agg)

    def _legit(self, start):
        # prices of groups whose last bar closed before the session start
        return {x for grp in self.sessions.groups.values() if grp["last_t"] + 60 <= start for x in (grp["h"], grp["l"])}


# ---- 1.7 market calendar --------------------------------------------------------------
def bad_sessions(years):
    import holidays
    out = set()
    for y in years:
        hol = holidays.financial_holidays("NYSE", years=y)
        out |= set(hol)
        for d in hol:
            if hol[d] == "Good Friday":
                out.add(d - dt.timedelta(days=1))          # CME closed Good Friday: no Thursday evening session
        for m, d in ((7, 3), (12, 24), (12, 31)):           # early closes / CME closure eves
            x = dt.date(y, m, d)
            if x.weekday() < 5:
                out.add(x)
        th = [d for d in hol if hol[d] == "Thanksgiving Day"]
        out |= {d + dt.timedelta(days=1) for d in th}       # half day
    return out


def simulate(bars, steps=(), p=None, tick=TICK, pv=PV, accept=None, strat_cls=Checked):
    p = p or params()
    steps = set(steps)
    strat = strat_cls(p, tick, pv)
    guard = DayGuard(p)
    bad = bad_sessions(range(2015, 2027)) if "calendar" in steps else set()
    trades, pos, pend = [], None, None
    sl_stop = (2 if "stop2" in steps else 1) * tick
    sl_gap = tick
    thru = tick if "through" in steps else 0.0
    for b in bars:
        if pend:                                   # 1.2: fill at this bar's open
            sig, d = pend
            entry = b.o + d * tick
            pos = _open(sig, entry, d, b.t, guard, p, pv)
            pend = None
        if pos:
            d = pos["d"]
            ex = why = None
            if (d > 0 and b.l <= pos["stop"]) or (d < 0 and b.h >= pos["stop"]):
                gap = (b.o - pos["stop"]) * d < 0
                ex, why = ((b.o - d * sl_gap) if gap else (pos["stop"] - d * sl_stop)), "stop"
            elif (d > 0 and b.h >= pos["target"] + thru) or (d < 0 and b.l <= pos["target"] - thru):
                ex, why = pos["target"], "target"
                if b.h == pos["target"] if d > 0 else b.l == pos["target"]:
                    pos["touch"] = True
            elif guard.pnl > 0 and ((d > 0 and b.h >= pos["day_cap"]) or (d < 0 and b.l <= pos["day_cap"])):
                ex, why = pos["day_cap"], "day_cap"
            elif guard.must_flatten(b.t):
                ex, why = b.o - d * tick, "flat_by"
            elif pos["exit_by"] and b.t >= pos["exit_by"]:
                ex, why = b.o - d * tick, "time_exit"
            if ex is not None:
                pnl = (ex - pos["entry"]) * d * pos["size"] * pv - p.FEE_PER_CONTRACT_RT * pos["size"]
                guard.record(b.t, pnl)
                trades.append({**pos, "exit": ex, "reason": why, "pnl": pnl, "R": pnl / pos["risk"],
                               "closed": b.t, "day": str(trading_day(b.t, p.DAY_START))})
                pos = None
        sig = strat.on_bar(b, 0.0, live=True)
        if not sig or pos or pend or "skip" in sig:
            continue
        if bad and strategy.asia_clock(b.t)[0] in bad:
            continue
        ok, _ = guard.can_enter(b.t + 60)
        if not ok or (accept and not accept(sig)):
            continue
        d = 1 if sig["side"] == "long" else -1
        sig = {**sig, "t": b.t, "w": strat.high - strat.low, "ny_move": strat.bias.get("ny_move")}
        if "next_open" in steps:
            pend = (sig, d)
        else:
            pos = _open(sig, sig["entry"] + d * tick, d, b.t + 60, guard, p, pv)
    return trades


def _open(sig, entry, d, t, guard, p, pv):
    stop_pts = abs(sig["entry"] - sig["stop"])
    tgt_pts = abs(sig["target"] - sig["entry"])
    room = p.DAILY_PROFIT_STOP_USD - guard.pnl + p.FEE_PER_CONTRACT_RT * sig["size"]
    size = sig["size"]
    return {"side": sig["side"], "d": d, "size": size, "signal_close": sig["entry"], "entry": entry,
            "stop": entry - d * stop_pts, "target": entry + d * tgt_pts, "stop_pts": stop_pts,
            "risk": size * (stop_pts * pv + p.FEE_PER_CONTRACT_RT), "opened": t, "exit_by": sig.get("exit_by"),
            "day_cap": entry + d * room / (size * pv), "sig": sig}


def load_bars(path):
    import backtest
    return backtest.load_csv(path)
