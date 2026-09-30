"""Risk limits, checked before every trade and on every loop."""
import datetime as dt
import json
import math
import os


class RiskManager:
    def __init__(self, starting_balance, max_daily_loss_pct, max_drawdown_pct, buffer,
                 risk_per_trade_pct, max_notional_mult, size_step, state_file=None):
        self.starting_balance = starting_balance
        self.daily_limit = starting_balance * max_daily_loss_pct * buffer
        self.drawdown_limit = starting_balance * max_drawdown_pct * buffer
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_notional_mult = max_notional_mult
        self.size_step = size_step
        self.state_file = state_file
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
        # Days roll over at 00:00 UTC. Confirm the reset time MFP uses and adjust if it differs.
        today = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%d")
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

    def check_risk(self, equity, projected_loss=0.0, now=None):
        """Returns (ok, reason). projected_loss = what the next trade loses if its stop is hit."""
        s = self.snapshot(equity, now)
        if s["drawdown"] + projected_loss >= self.drawdown_limit:
            return False, f"drawdown {s['drawdown']:.2f} + {projected_loss:.2f} would reach limit {self.drawdown_limit:.2f}"
        if s["daily_loss"] + projected_loss >= self.daily_limit:
            return False, f"daily loss {s['daily_loss']:.2f} + {projected_loss:.2f} would reach limit {self.daily_limit:.2f}"
        return True, "ok"

    def position_size(self, equity, entry, stop):
        """Size that loses risk_per_trade_pct of equity at the stop, capped by notional, rounded down."""
        distance = abs(entry - stop)
        if entry <= 0 or distance <= 0:
            return 0.0
        raw = min(equity * self.risk_per_trade_pct / distance,
                  equity * self.max_notional_mult / entry)
        steps = math.floor(raw / self.size_step + 1e-9)
        return round(steps * self.size_step, 10)
