"""Trade management and daily limits, shared by the live bot, paper mode and the backtest."""
import math

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
    """One loss, or +DAILY_PROFIT_STOP_USD, ends the trading day. Entries only in the session window."""

    def __init__(self, p):
        self.p = p
        self.day = None
        self.pnl = 0.0
        self.losses = 0
        self.trades = 0

    def _roll(self, ts):
        td = trading_day(ts, self.p.DAY_START)
        if td != self.day:
            self.day, self.pnl, self.losses, self.trades = td, 0.0, 0, 0

    def record(self, ts, pnl):
        self._roll(ts)
        self.trades += 1
        self.pnl += pnl
        if pnl < self.p.LOSS_THRESHOLD_USD:
            self.losses += 1

    def set_day_pnl(self, ts, pnl):
        """Live mode: the day's realized P&L straight from the account's trades."""
        self._roll(ts)
        self.pnl = pnl

    def can_enter(self, ts):
        self._roll(ts)
        if not in_window(ts, self.p.ENTRY_START, self.p.ENTRY_END):
            return False, "outside the entry window"
        if self.losses >= self.p.DAILY_MAX_LOSSES:
            return False, "daily stop: a losing trade today"
        if self.pnl >= self.p.DAILY_PROFIT_STOP_USD:
            return False, f"daily stop: +${self.pnl:.0f} today (consistency cap)"
        return True, "ok"

    def must_flatten(self, ts):
        """Flat before Topstep's 4:10pm NY cutoff, until the new day opens at 6pm."""
        return in_window(ts, self.p.FLAT_BY, self.p.DAY_START)
