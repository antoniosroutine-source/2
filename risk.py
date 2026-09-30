"""Risk limits, checked before every trade and on every loop."""
import datetime as dt
import json
import math
import os

from strategy import NY


class RiskManager:
    def __init__(self, starting_balance, max_daily_loss_pct, max_drawdown_pct, buffer,
                 risk_per_trade_usd, max_notional_mult, size_step, state_file=None,
                 server_room_reserve_usd=0.0):
        self.starting_balance = starting_balance
        self.daily_limit = starting_balance * max_daily_loss_pct * buffer
        self.drawdown_limit = starting_balance * max_drawdown_pct * buffer
        self.risk_per_trade_usd = risk_per_trade_usd
        self.max_notional_mult = max_notional_mult
        self.size_step = size_step
        self.state_file = state_file
        self.server_room_reserve_usd = server_room_reserve_usd
        self.state = self._load()

    def _load(self):
        if self.state_file and os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
        return {"day": None, "day_start_equity": None}

    def _save(self):
        if self.state_file:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f)

    def _roll_day(self, equity, now):
        # MFP resets the daily loss at midnight New York time (04:00 or 05:00 UTC, with DST).
        today = (now or dt.datetime.now(dt.timezone.utc)).astimezone(NY).strftime("%Y-%m-%d")
        if self.state["day"] != today:
            self.state = {"day": today, "day_start_equity": equity}
            self._save()

    def snapshot(self, equity, now=None):
        self._roll_day(equity, now)
        daily_loss = max(0.0, self.state["day_start_equity"] - equity)
        drawdown = max(0.0, self.starting_balance - equity)
        return {
            "equity": equity,
            "daily_loss": daily_loss,
            "daily_limit": self.daily_limit,
            "drawdown": drawdown,
            "drawdown_limit": self.drawdown_limit,
        }

    def check_risk(self, equity, projected_loss=0.0, now=None, server=None):
        """Returns (ok, reason). projected_loss = what the next trade loses if its stop is hit.

        server is MFP's risk snapshot. Its daily_loss_room / max_drawdown_room (equity minus each
        firm floor) are authoritative, so they also cover losses this bot never saw (manual trades,
        restarts mid-day). The bot keeps server_room_reserve_usd of room above each floor.
        """
        s = self.snapshot(equity, now)
        for key in ("daily_loss_room", "max_drawdown_room"):
            room = (server or {}).get(key)
            if room is not None and float(room) - projected_loss <= self.server_room_reserve_usd:
                return False, (f"MFP {key} {float(room):.2f} - {projected_loss:.2f} would leave less than "
                               f"{self.server_room_reserve_usd:.2f}")
        if s["drawdown"] + projected_loss >= self.drawdown_limit:
            return False, f"drawdown {s['drawdown']:.2f} + {projected_loss:.2f} would reach limit {self.drawdown_limit:.2f}"
        if s["daily_loss"] + projected_loss >= self.daily_limit:
            return False, f"daily loss {s['daily_loss']:.2f} + {projected_loss:.2f} would reach limit {self.daily_limit:.2f}"
        return True, "ok"

    def position_size(self, equity, entry, stop):
        """Size that loses risk_per_trade_usd at the stop, capped by notional, rounded down."""
        distance = abs(entry - stop)
        if entry <= 0 or distance <= 0:
            return 0.0
        raw = min(self.risk_per_trade_usd / distance,
                  equity * self.max_notional_mult / entry)
        steps = math.floor(raw / self.size_step + 1e-9)
        return round(steps * self.size_step, 10)
