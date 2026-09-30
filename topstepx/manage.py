"""Trade management and daily limits, shared by the live bot, paper mode and the backtest."""
import datetime as dt
import json
import math
import os

from levels import in_window, trading_day


def next_stop(side, entry, stop, peak, size, point_value, tick, p):
    """Where the stop should be, given the best price reached so far (peak).

    +BREAKEVEN_AT_USD open profit: stop to entry (+1 tick). +TRAIL_AT_USD: stop keeps TRAIL_KEEP
    of the best open profit. The stop only ever moves in the trade's favour.
    """
    d = 1 if side == "long" else -1
    best_pts = (peak - entry) * d
    best_usd = best_pts * size * point_value
    want = None
    if best_usd >= p.TRAIL_AT_USD:
        want = entry + d * p.TRAIL_KEEP * best_pts
    elif best_usd >= p.BREAKEVEN_AT_USD:
        want = entry + d * p.BE_OFFSET_TICKS * tick
    if want is None:
        return stop
    steps = want / tick
    want = round((math.floor(steps + 1e-9) if d > 0 else math.ceil(steps - 1e-9)) * tick, 10)
    return max(stop, want) if d > 0 else min(stop, want)


class DayGuard:
    """One loss, or +DAILY_PROFIT_STOP_USD, ends the trading day. Entries only in the session window.

    The day's losses and P&L are saved to state_file, so a restart cannot reset the one-loss rule.
    """

    def __init__(self, p, state_file=None):
        self.p = p
        self.state_file = state_file
        self.day, self.pnl, self.losses, self.trades = None, 0.0, 0, 0
        if state_file and os.path.exists(state_file):
            try:
                with open(state_file) as f:
                    s = json.load(f)
                self.day = dt.date.fromisoformat(s["day"])
                self.pnl, self.losses, self.trades = float(s["pnl"]), int(s["losses"]), int(s["trades"])
            except (OSError, ValueError, KeyError):
                pass

    def _save(self):
        if self.state_file:
            tmp = self.state_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"day": str(self.day), "pnl": self.pnl, "losses": self.losses, "trades": self.trades}, f)
            os.replace(tmp, self.state_file)

    def _roll(self, ts):
        td = trading_day(ts, self.p.DAY_START)
        if td != self.day:
            self.day, self.pnl, self.losses, self.trades = td, 0.0, 0, 0
            self._save()

    def record(self, ts, pnl, add_pnl=True):
        """A closed trade. Live mode passes add_pnl=False: the day's P&L comes from the account."""
        self._roll(ts)
        self.trades += 1
        if add_pnl:
            self.pnl += pnl
        if pnl < self.p.LOSS_THRESHOLD_USD:
            self.losses += 1
        self._save()

    def set_day_pnl(self, ts, pnl):
        """Live mode: the day's realized P&L straight from the account's trades."""
        self._roll(ts)
        if abs(pnl - self.pnl) > 0.005:
            self.pnl = pnl
            self._save()

    def can_enter(self, ts):
        self._roll(ts)
        if not in_window(ts, self.p.ENTRY_START, self.p.ENTRY_END):
            return False, "outside the entry window"
        if self.losses >= self.p.DAILY_MAX_LOSSES:
            return False, "daily stop: a losing trade today"
        if self.pnl >= self.p.DAILY_PROFIT_STOP_USD:
            return False, f"daily stop: +${self.pnl:.0f} today (consistency cap)"
        return True, "ok"

    def day_capped(self, open_pnl):
        """True once today's closed plus open P&L reaches the daily profit cap (lock it in)."""
        return self.pnl + open_pnl >= self.p.DAILY_PROFIT_STOP_USD

    def must_flatten(self, ts):
        """Flat from FLAT_BY (before the 8:30am ET news) until the new day opens at 6pm."""
        return in_window(ts, self.p.FLAT_BY, self.p.DAY_START)
