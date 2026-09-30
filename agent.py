"""Main trading loop: poll price -> build candles -> Silver Bullet -> risk check -> order -> TP/SL."""
import json
import os
import signal
import sys
import time
import urllib.request

import config
from mfp_client import MFPClient, MFPError, pick
from risk import RiskManager
from strategy import AsiaSweep, Candle, CandleBuilder, SilverBullet


def log_event(event, **fields):
    rec = {"ts": time.time(), "strategy": config.STRATEGY_ID, "event": event, **fields}
    with open(config.LOG_FILE, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def round_tick(price, tick=None):
    tick = tick or config.PRICE_TICK
    return round(round(price / tick) * tick, 8)


def size_step(info):
    """Order size increment from the market's size_decimals (e.g. 4 -> 0.0001)."""
    decimals = pick(info, "size_decimals")
    return 10.0 ** -int(decimals) if decimals is not None else config.SIZE_STEP


# Common names for the Nasdaq-100; MFP lists it as XYZ100 (hyperliquid|xyz:XYZ100).
MARKET_ALIASES = {"nas100": "XYZ100", "ndx": "XYZ100", "us100": "XYZ100", "nq": "XYZ100"}


def resolve_market(client, wanted):
    """Turn a symbol like "XYZ100" into MFP's market id. Returns (market_id, market_info)."""
    wanted = MARKET_ALIASES.get(wanted.lower(), wanted)
    try:
        markets = client.list_markets()
    except (MFPError, OSError) as e:
        if "|" in wanted:
            return wanted, {}
        sys.exit(f"Could not load MFP's market list ({e}). Set FPERP_MARKET to the exact id.")
    ids = lambda m: [str(pick(m, k, default="")) for k in ("market_id", "id", "symbol", "coin")]
    exact = [m for m in markets if any(v.lower() == wanted.lower() for v in ids(m))]
    if not exact and "|" not in wanted:
        exact = [m for m in markets if any(v.lower().endswith(wanted.lower()) for v in ids(m))]
    if len(exact) == 1:
        m = exact[0]
        return str(pick(m, "market_id", "id")), m
    if "|" in wanted:
        return wanted, {}
    near = [pick(m, "market_id", "id") for m in markets
            if any(k in " ".join(ids(m)).lower() for k in ("nas", "ndx", "us100", "xyz100", "nq"))]
    sys.exit(f"Could not pick one market for {wanted!r}. Nasdaq-like markets found: {near or 'none'}. "
             "Set FPERP_MARKET to the exact id.")


def make_strategy(strategy_id=None):
    strategy_id = strategy_id or config.STRATEGY_ID
    if strategy_id == "asia_sweep":
        return AsiaSweep(config.RR, config.ASIA_RANGE_MIN, config.ASIA_TRADE_UNTIL_MIN, config.ASIA_EXIT_MIN,
                         config.ASIA_BUF_FRAC, config.ASIA_MIN_STOP_PTS, config.ASIA_MIN_RANGE_PCT)
    if strategy_id == "silver_bullet":
        return SilverBullet(config.LIQUIDITY_LOOKBACK, config.RR, config.STOP_BUFFER_PCT,
                            config.MIN_STOP_PCT, config.SETUP_EXPIRY_BARS)
    sys.exit(f"Unknown strategy {strategy_id!r}")


def fetch_history(market, hours=12, now=None):
    """Recent 1-minute candles from Hyperliquid's public API (MFP fills Hyperliquid markets from
    that venue's book), so the bot knows the current range at startup instead of waiting."""
    provider, _, coin = market.partition("|")
    if provider != "hyperliquid" or not coin:
        return []
    end = int((now or time.time()) * 1000)
    body = json.dumps({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1m",
                                                         "startTime": end - hours * 3_600_000,
                                                         "endTime": end}}).encode()
    req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        rows = json.loads(resp.read())
    return [Candle(r["t"] / 1000, float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"])) for r in rows]


def warm_up(strategy, market, now=None):
    """Replay completed candles into the strategy. Past sweeps are never traded late."""
    now = now or time.time()
    try:
        candles = fetch_history(market, now=now)
    except (OSError, ValueError, KeyError) as e:
        log_event("error", error=f"history unavailable, building candles live instead: {e}")
        return 0
    done = [c for c in candles if c.start + 60 <= now]
    for c in done:
        if isinstance(strategy, AsiaSweep):
            strategy.on_candle(c, live=False)
        else:
            strategy.on_candle(c)
    log_event("warm_up", candles=len(done), sb=strategy.state())
    return len(done)


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

    def recover_order(self, client_order_id):
        """After an ambiguous failure, look the order up by our own id instead of placing it again."""
        try:
            return self.client.find_order_by_client_id(client_order_id)
        except (MFPError, OSError) as e:
            log_event("critical", message="order state unknown; check the MFP terminal",
                      client_order_id=client_order_id, error=str(e))
            return None

    def has_exits(self, pos):
        """True when the position has resting reduce-only exits (the TP and SL attached to the entry)."""
        pos_id = pick(pos, "id", "position_id")
        try:
            orders = self.client.list_working_orders(self.account_id)
        except (MFPError, OSError) as e:
            log_event("error", error=f"could not read working orders: {e}")
            return False
        exits = [o for o in orders if o.get("reduce_only")
                 and (o.get("target_position_id") == pos_id or market_matches(o, self.market))]
        return len(exits) >= 2

    def execute(self, sig, equity, server=None):
        entry, stop = sig["entry"], sig["stop"]
        size = self.risk.position_size(equity, entry, stop)
        if size <= 0:
            log_event("skip", reason="size rounds to zero", signal=sig)
            return
        projected = size * abs(entry - stop)
        ok, reason = self.risk.check_risk(equity, projected, server=server)
        if not ok:
            log_event("risk_block", reason=reason, signal=sig)
            return

        side = "buy" if sig["side"] == "long" else "sell"
        tp, sl = round_tick(sig["target"]), round_tick(stop)
        client_order_id = f"{config.STRATEGY_ID}-{int(time.time() * 1000)}"
        try:
            # TP and SL ride on the entry order, so they exist from the moment it fills.
            order = self.client.place_market_order(self.account_id, self.market, side, size, entry,
                                                   config.SIDE_LEVERAGE, config.MARGIN_MODE,
                                                   client_order_id=client_order_id,
                                                   take_profit_price=tp, stop_loss_price=sl)
        except MFPError as e:
            if e.status < 500 and e.status != 429:
                log_event("order_rejected", status=e.status, error=e.body, signal=sig)
                return
            order = self.recover_order(client_order_id)
        except OSError:
            order = self.recover_order(client_order_id)
        if order is None:
            log_event("order_not_placed", client_order_id=client_order_id, signal=sig)
            return
        order = self.client.wait_for_order(pick(order, "id", "order_id"))
        status = str(order.get("status", "")).lower()
        if status != "filled":
            log_event("order_not_filled", status=status, order=order)
            return
        pos = self.wait_for_position()
        if pos is None:
            log_event("critical", message="order filled but no position found", order=order)
            return

        fill = float(pick(pos, "entry_price", default=None)
                     or pick(order, "fill_price", default=entry))
        if (sig["side"] == "long" and fill <= stop) or (sig["side"] == "short" and fill >= stop):
            return self._close_now(pos, "filled beyond stop")
        # The attached exits should already be there; place them separately only if they are not.
        protected = self.has_exits(pos) or self.protect(pos, tp, sl)
        if protected:
            self.open_trade = {"position_id": pick(pos, "id", "position_id"), "equity_at_entry": equity,
                               "side": sig["side"], "entry": fill, "tp": tp, "sl": sl, "size": size,
                               "opened": time.time(), "exit_by": sig.get("exit_by")}
            log_event("trade_open", **self.open_trade)

    def _close_now(self, pos, reason):
        pos_id = pick(pos, "id", "position_id")
        self.client.close_position(pos_id)
        log_event("trade_aborted", reason=reason, position_id=pos_id)

    def step(self, now=None):
        now = now if now is not None else time.time()
        price = self.client.get_mid(self.market, config.QUOTE_SIZE)
        server = self.client.get_risk(self.account_id)
        equity = server.get("equity")

        closed = self.candles.update(now, price)
        if closed is not None:
            self.strategy.on_candle(closed)
            snap = self.risk.snapshot(float(equity)) if equity is not None else None
            log_event("candle", o=closed.open, h=closed.high, l=closed.low, c=closed.close,
                      t=closed.start, risk=snap, sb=self.strategy.state())

        if equity is None:
            # MFP has no fresh mark for an open position, so equity is unknown. Take no action
            # (any open position keeps its TP/SL) and try again on the next loop.
            log_event("marks_incomplete", missing_markets=server.get("missing_markets"))
            return
        equity = float(equity)

        pos = self.find_position()
        ok, reason = self.risk.check_risk(equity, server=server)
        halted = not ok
        if pos is not None:
            if halted:
                self._close_now(pos, f"risk limit: {reason}")
            elif self.open_trade and self.open_trade.get("exit_by") and now >= self.open_trade["exit_by"]:
                self._close_now(pos, "session end")
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
            self.execute(sig, equity, server)

    def run(self):
        log_event("start", account_id=self.account_id, market=self.market, base_url=config.BASE_URL)
        errors = 0
        while self.running:
            try:
                self.step()
                errors = 0
            except (MFPError, OSError, KeyError, ValueError, TypeError) as e:
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
        if len(accounts) > 1:
            listed = ", ".join(f"{a.get('id')} ({a.get('name')}, {a.get('status')})" for a in accounts)
            sys.exit(f"This key can see {len(accounts)} accounts: {listed}. "
                     "Set FPERP_ACCOUNT_ID to the one to trade (python check.py lists them).")
        account_id = pick(accounts[0], "id", "account_id")
    account = client.get_account(account_id)
    if account.get("status") != "active":
        sys.exit(f"Account {account_id} is {account.get('status')!r}, not active.")
    starting_balance = float(account.get("starting_balance") or config.STARTING_BALANCE)
    try:
        policy = client.get_trading_policy(account_id)
        log_event("trading_policy", limits=policy.get("limits"), halt=policy.get("trading_halt"),
                  manual_trading_blocked=policy.get("manual_trading_blocked"))
        if policy.get("manual_trading_blocked"):
            sys.exit("MFP reports trading is blocked on this account. Check the MFP dashboard.")
    except (MFPError, OSError) as e:
        log_event("error", error=f"could not read trading policy: {e}")
    market, info = resolve_market(client, config.MARKET)
    config.SIZE_STEP = size_step(info)
    if config.SIDE_LEVERAGE > float(pick(info, "max_leverage", default=config.SIDE_LEVERAGE)):
        sys.exit(f"SIDE_LEVERAGE {config.SIDE_LEVERAGE} is above {market}'s max leverage {info['max_leverage']}.")
    risk = RiskManager(starting_balance, config.MAX_DAILY_LOSS_PCT, config.MAX_DRAWDOWN_PCT,
                       config.RISK_BUFFER, config.RISK_PER_TRADE_USD, config.MAX_NOTIONAL_MULT,
                       config.SIZE_STEP, config.RISK_STATE_FILE, config.SERVER_ROOM_RESERVE_USD)
    strategy = make_strategy()
    warm_up(strategy, market)
    bot = Bot(client, account_id, risk, strategy, market)

    def stop(*_):
        bot.running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    bot.run()


if __name__ == "__main__":
    main()
