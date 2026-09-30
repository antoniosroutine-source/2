"""Main trading loop: poll price -> build candles -> Silver Bullet -> risk check -> order -> TP/SL."""
import json
import os
import signal
import sys
import time

import config
from mfp_client import MFPClient, MFPError, pick
from risk import RiskManager
from strategy import CandleBuilder, SilverBullet


def log_event(event, **fields):
    rec = {"ts": time.time(), "strategy": config.STRATEGY_ID, "event": event, **fields}
    with open(config.LOG_FILE, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def round_tick(price, tick=None):
    tick = tick or config.PRICE_TICK
    return round(round(price / tick) * tick, 8)


def resolve_market(client, wanted):
    """Turn a symbol like "NAS100" into MFP's market id. Returns (market_id, market_info)."""
    try:
        markets = client.list_markets()
    except (MFPError, OSError) as e:
        if "|" in wanted:
            return wanted, {}
        sys.exit(f"Could not load MFP's market list ({e}). Set FPERP_MARKET to the exact id.")
    ids = lambda m: [str(pick(m, k, default="")) for k in ("id", "market_id", "symbol", "name", "base")]
    exact = [m for m in markets if any(v.lower() == wanted.lower() for v in ids(m))]
    if not exact and "|" not in wanted:
        exact = [m for m in markets if any(v.lower().endswith(wanted.lower()) for v in ids(m))]
    if len(exact) == 1:
        m = exact[0]
        return str(pick(m, "id", "market_id")), m
    if "|" in wanted:
        return wanted, {}
    near = [pick(m, "id", "market_id") for m in markets
            if any(k in " ".join(ids(m)).lower() for k in ("nas", "ndx", "us100", "xyz100", "nq"))]
    sys.exit(f"Could not pick one market for {wanted!r}. Nasdaq-like markets found: {near or 'none'}. "
             "Set FPERP_MARKET to the exact id.")


def check_key(api_key, base_url):
    """Refuse to run a live key against sandbox or a test key against live, so nothing trades by accident."""
    if not api_key:
        sys.exit(f"Set {config.API_KEY_ENV} to your MFP API key first.")
    if api_key.startswith("fp_live_") and base_url != config.LIVE_URL:
        sys.exit("Live key (fp_live_) but BASE_URL is not the live URL. Set FPERP_BASE_URL deliberately.")
    if api_key.startswith("fp_test_") and base_url == config.LIVE_URL:
        sys.exit("Sandbox key (fp_test_) used with the live URL.")


def market_matches(pos, market):
    return pick(pos, "market_id", "market") == market


def is_open(pos):
    status = str(pos.get("status", "open")).lower()
    return status == "open" and abs(float(pick(pos, "size", "quantity", default=0))) > 0


class Bot:
    def __init__(self, client, account_id, risk, strategy, market=config.MARKET, sleep=time.sleep):
        self.client = client
        self.market = market
        self.account_id = account_id
        self.risk = risk
        self.strategy = strategy
        self.candles = CandleBuilder(config.CANDLE_SEC)
        self.sleep = sleep
        self.open_trade = None  # {"position_id", "equity_at_entry", ...}
        self.running = True

    def find_position(self):
        for pos in self.client.list_positions(self.account_id):
            if market_matches(pos, self.market) and is_open(pos):
                return pos
        return None

    def wait_for_position(self, attempts=10, delay=0.2):
        for _ in range(attempts):
            pos = self.find_position()
            if pos:
                return pos
            self.sleep(delay)
        return None

    def protect(self, pos, tp, sl):
        """Place TP/SL on the position (5 tries, 200ms apart). Closes the position if that fails."""
        pos_id = pick(pos, "id", "position_id")
        size = abs(float(pick(pos, "size", "quantity")))
        last_err = None
        for _ in range(5):
            try:
                self.client.set_exit_orders(pos_id, size, tp, sl)
                return True
            except (MFPError, OSError) as e:
                last_err = e
                self.sleep(0.2)
        log_event("exit_orders_failed", position_id=pos_id, error=str(last_err))
        try:
            self.client.close_position(pos_id)
            log_event("position_closed_unprotected", position_id=pos_id)
        except (MFPError, OSError) as e:
            log_event("critical", message="could not protect or close position", position_id=pos_id, error=str(e))
        return False

    def execute(self, sig, equity):
        entry, stop = sig["entry"], sig["stop"]
        size = self.risk.position_size(equity, entry, stop)
        if size <= 0:
            log_event("skip", reason="size rounds to zero", signal=sig)
            return
        projected = size * abs(entry - stop)
        ok, reason = self.risk.check_risk(equity, projected)
        if not ok:
            log_event("risk_block", reason=reason, signal=sig)
            return

        side = "buy" if sig["side"] == "long" else "sell"
        order = self.client.place_market_order(self.account_id, self.market, side, size, entry,
                                               config.SIDE_LEVERAGE, config.MARGIN_MODE)
        order = self.client.wait_for_order(pick(order, "id", "order_id"))
        status = str(order.get("status", "")).lower()
        if status != "filled":
            log_event("order_not_filled", status=status, order=order)
            return
        pos = self.wait_for_position()
        if pos is None:
            log_event("critical", message="order filled but no position found", order=order)
            return

        fill = float(pick(pos, "entry_price", "avg_entry_price", default=None)
                     or pick(order, "avg_fill_price", "fill_price", "price", default=entry))
        # Keep the structural stop; re-derive the target from the real fill.
        if sig["side"] == "long":
            if fill <= stop:
                return self._close_now(pos, "filled beyond stop")
            tp = fill + config.RR * (fill - stop)
        else:
            if fill >= stop:
                return self._close_now(pos, "filled beyond stop")
            tp = fill - config.RR * (stop - fill)
        tp, sl = round_tick(tp), round_tick(stop)
        protected = self.protect(pos, tp, sl)
        if protected:
            self.open_trade = {"position_id": pick(pos, "id", "position_id"), "equity_at_entry": equity,
                               "side": sig["side"], "entry": fill, "tp": tp, "sl": sl, "size": size,
                               "opened": time.time()}
            log_event("trade_open", **self.open_trade)

    def _close_now(self, pos, reason):
        pos_id = pick(pos, "id", "position_id")
        self.client.close_position(pos_id)
        log_event("trade_aborted", reason=reason, position_id=pos_id)

    def step(self, now=None):
        now = now if now is not None else time.time()
        price = self.client.get_mid(self.market, config.QUOTE_SIZE)
        equity = self.client.get_equity(self.account_id)
        snap = self.risk.snapshot(equity)

        closed = self.candles.update(now, price)
        if closed is not None:
            self.strategy.on_candle(closed)
            log_event("candle", o=closed.open, h=closed.high, l=closed.low, c=closed.close,
                      t=closed.start, risk=snap, sb=self.strategy.state())

        pos = self.find_position()
        ok, reason = self.risk.check_risk(equity)
        halted = not ok
        if pos is not None:
            if halted:
                self._close_now(pos, f"risk limit: {reason}")
            return
        if self.open_trade is not None:
            pnl = equity - self.open_trade["equity_at_entry"]
            log_event("trade_closed", pnl=pnl, **self.open_trade)
            self.open_trade = None
        if halted:
            return

        sig = self.strategy.on_price(now, price)
        if sig:
            log_event("signal", **sig)
            self.execute(sig, equity)

    def run(self):
        log_event("start", account_id=self.account_id, market=self.market, base_url=config.BASE_URL)
        errors = 0
        while self.running:
            try:
                self.step()
                errors = 0
            except (MFPError, OSError, KeyError, ValueError) as e:
                errors += 1
                log_event("error", error=str(e))
                self.sleep(min(60, config.POLL_INTERVAL_SEC * 2 ** min(errors, 4)))
                continue
            self.sleep(config.POLL_INTERVAL_SEC)
        log_event("stop")


def main():
    api_key = os.environ.get(config.API_KEY_ENV, "").strip()
    check_key(api_key, config.BASE_URL)
    client = MFPClient(api_key, config.BASE_URL)
    account_id = config.ACCOUNT_ID
    if not account_id:
        accounts = client.list_accounts()
        if not accounts:
            sys.exit("No accounts found for this API key.")
        account_id = pick(accounts[0], "id", "account_id")
    market, info = resolve_market(client, config.MARKET)
    config.SIZE_STEP = float(pick(info, "size_increment", "step_size", "lot_size", default=config.SIZE_STEP))
    config.PRICE_TICK = float(pick(info, "tick_size", "price_increment", default=config.PRICE_TICK))
    risk = RiskManager(config.STARTING_BALANCE, config.MAX_DAILY_LOSS_PCT, config.MAX_DRAWDOWN_PCT,
                       config.RISK_BUFFER, config.RISK_PER_TRADE_PCT, config.MAX_NOTIONAL_MULT,
                       config.SIZE_STEP, config.RISK_STATE_FILE)
    strategy = SilverBullet(config.LIQUIDITY_LOOKBACK, config.RR, config.STOP_BUFFER_PCT,
                            config.MIN_STOP_PCT, config.SETUP_EXPIRY_BARS)
    bot = Bot(client, account_id, risk, strategy, market)

    def stop(*_):
        bot.running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    bot.run()


if __name__ == "__main__":
    main()
