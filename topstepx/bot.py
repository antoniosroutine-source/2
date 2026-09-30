"""TopstepX level sweep bot.

    python bot.py            # PAPER mode (default): real market data, no orders, logs what it would do
    python bot.py --live     # places real orders on your TopstepX account

Stop it with Ctrl+C. To stop and flatten from another window, create a file named STOP in
this folder. An open position keeps its stop and target when the bot stops.
"""
import argparse
import datetime as dt
import json
import os
import signal
import sys
import time

import config
from levels import Aggression, et, trading_day
from manage import DayGuard, next_stop
from projectx import (ORDER_LIMIT, ORDER_MARKET, ORDER_STOP, POS_LONG, SIDE_BUY, SIDE_SELL,
                      PXError, ProjectX)
from strategy import LevelSweep
from stream import MarketStream

STOP_FILE = os.path.join(config.HERE, "STOP")


def log(event, **fields):
    rec = {"ts": round(time.time(), 3), "et": f"{et(time.time()):%Y-%m-%d %H:%M:%S}", "event": event, **fields}
    with open(config.LOG_FILE, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str), flush=True)


def connect(read_only=False):
    """Log in and find the MNQ contract. Returns (client, contract)."""
    username = os.environ.get(config.USERNAME_ENV, "").strip()
    api_key = os.environ.get(config.API_KEY_ENV, "").strip()
    if not username or not api_key:
        sys.exit(f"Set {config.USERNAME_ENV} (your TopstepX username) and {config.API_KEY_ENV} first.")
    client = ProjectX(config.API_URL, username, api_key)
    try:
        client.login()
    except PXError as e:
        hints = {3: "wrong username (not your email) or API key", 9: "no ProjectX API subscription linked",
                 7: "log into TopstepX and accept the pending agreements"}
        sys.exit(f"Login failed: {hints.get(e.code, e.message)}")
    contract = client.find_contract(config.SYMBOL_SEARCH, config.SYMBOL_ID, config.LIVE_DATA)
    if not contract:
        sys.exit(f"Could not find the active {config.SYMBOL_ID} contract.")
    config.TICK_SIZE = float(contract["tickSize"])
    config.POINT_VALUE = float(contract["tickValue"]) / config.TICK_SIZE
    return client, contract


def pick_account(client):
    accounts = client.accounts()
    if config.ACCOUNT_ID:
        match = [a for a in accounts if str(a["id"]) == str(config.ACCOUNT_ID)]
        if not match:
            sys.exit(f"Account {config.ACCOUNT_ID} not found among active accounts.")
        return match[0]
    if len(accounts) == 1:
        return accounts[0]
    listed = "\n".join(f"  id={a['id']}  {a.get('name')}  balance={a.get('balance')}" for a in accounts)
    sys.exit(f"This key sees {len(accounts)} accounts. Set PX_ACCOUNT_ID to one of:\n{listed}")


# -- brokers: paper and live share one interface ---------------------------------------

class PaperBroker:
    """Simulates fills on live prices. Exits are checked against each bar's high/low and each price."""

    def __init__(self, point_value, fee):
        self.pv, self.fee = point_value, fee
        self.pos = None
        self.closed = []      # results waiting to be collected: (ts, pnl, reason)

    def position(self):
        return self.pos

    def enter(self, side, size, price, tag):
        self.pos = {"side": side, "size": size, "entry": price}
        return price

    def set_exits(self, side, size, stop, target, tag):
        self.pos.update(stop=stop, target=target)

    def move_stop(self, stop):
        self.pos["stop"] = stop

    def check(self, hi, lo, ts):
        p = self.pos
        if not p or "stop" not in p:
            return
        d = 1 if p["side"] == "long" else -1
        if (d > 0 and lo <= p["stop"]) or (d < 0 and hi >= p["stop"]):
            self._close(p["stop"], ts, "stop")
        elif (d > 0 and hi >= p["target"]) or (d < 0 and lo <= p["target"]):
            self._close(p["target"], ts, "target")

    def _close(self, price, ts, reason):
        p = self.pos
        d = 1 if p["side"] == "long" else -1
        pnl = (price - p["entry"]) * d * p["size"] * self.pv - self.fee * p["size"]
        self.closed.append((ts, round(pnl, 2), reason))
        self.pos = None

    def flatten(self, price=None):
        if self.pos and price is not None:
            self._close(price, time.time(), "flatten")
        self.pos = None

    def trade_result(self, opened_ts):
        return self.closed.pop(0) if self.closed else None

    def day_pnl(self, day_start):
        return None   # the DayGuard keeps paper P&L itself


class LiveBroker:
    def __init__(self, client, account_id, contract):
        self.c, self.acct, self.contract = client, account_id, contract
        self.cid = contract["id"]
        self.stop_id = self.target_id = None

    def position(self):
        for p in self.c.positions(self.acct):
            if p.get("contractId") == self.cid and p.get("size"):
                return {"side": "long" if p["type"] == POS_LONG else "short",
                        "size": int(p["size"]), "entry": float(p["averagePrice"])}
        return None

    def enter(self, side, size, price, tag):
        self.c.place(self.acct, self.cid, ORDER_MARKET, SIDE_BUY if side == "long" else SIDE_SELL, size, tag=tag)
        for _ in range(20):
            time.sleep(0.5)
            pos = self.position()
            if pos and pos["size"] >= size:
                return pos["entry"]
        pos = self.position()
        return pos["entry"] if pos else None

    def set_exits(self, side, size, stop, target, tag):
        exit_side = SIDE_SELL if side == "long" else SIDE_BUY
        self.stop_id = self.c.place(self.acct, self.cid, ORDER_STOP, exit_side, size, stop_price=stop, tag=f"{tag}-sl")
        self.target_id = self.c.place(self.acct, self.cid, ORDER_LIMIT, exit_side, size, limit_price=target,
                                      tag=f"{tag}-tp")
        # platform "position brackets" would add a second set of exits that could flip the position
        for o in self.c.open_orders(self.acct):
            if o.get("contractId") == self.cid and o["id"] not in (self.stop_id, self.target_id):
                self.c.cancel(self.acct, o["id"])
                log("foreign_order_cancelled", order=o)

    def move_stop(self, stop):
        self.c.modify(self.acct, self.stop_id, stop_price=stop)

    def cleanup(self):
        """Cancel every working order on the contract (the leftover exit after a stop or target)."""
        for o in self.c.open_orders(self.acct):
            if o.get("contractId") == self.cid:
                try:
                    self.c.cancel(self.acct, o["id"])
                except PXError as e:
                    log("error", error=f"cancel {o['id']}: {e}")
        self.stop_id = self.target_id = None

    def check(self, hi, lo, ts):
        pass   # the exchange handles exits

    def flatten(self, price=None):
        if self.position():
            self.c.close_position(self.acct, self.cid)
        self.cleanup()

    def _realized(self, start, this_contract=True):
        """Net realized P&L since start: closing fills carry profitAndLoss, every fill carries fees."""
        total = 0.0
        for t in self.c.trades(self.acct, start):
            if t.get("voided") or (this_contract and t.get("contractId") != self.cid):
                continue
            total += (t.get("profitAndLoss") or 0.0) - (t.get("fees") or 0.0)
        return total

    def trade_result(self, opened_ts):
        start = dt.datetime.fromtimestamp(opened_ts - 5, dt.timezone.utc)
        return (time.time(), round(self._realized(start), 2), "exchange")

    def day_pnl(self, day_start):
        return self._realized(day_start, this_contract=False)   # consistency counts every trade


# -- the bot ---------------------------------------------------------------------------

class Bot:
    def __init__(self, client, account, contract, broker, live):
        self.c, self.account, self.contract, self.broker, self.live = client, account, contract, broker, live
        self.cid = contract["id"]
        self.strategy = LevelSweep(config, config.TICK_SIZE, config.POINT_VALUE)
        self.bar_agg = Aggression(config.AGG_WINDOW_MIN)
        self.tape_agg = Aggression(config.AGG_WINDOW_MIN)
        self.stream = MarketStream(config.MARKET_HUB_URL, self._token, self.cid, self.tape_agg, log)
        self.guard = DayGuard(config)
        self.last_bar_t = 0.0
        self.trade = None            # the bot's open trade
        self.last_stop_move = 0.0
        self.last_pnl_sync = 0.0
        self.foreign_logged = False
        self.running = True

    def _token(self):
        self.c._ensure_token()
        return self.c.token

    def aggression(self, now):
        if self.stream.healthy():
            return self.tape_agg.ratio(now), "tape"
        return self.bar_agg.ratio(now), "bars"

    def walls(self, side):
        if not (config.DOM_TARGETS and self.stream.healthy()):
            return []
        return self.stream.walls(side, config.WALL_MIN_SIZE, config.WALL_MULT)

    def warm_up(self):
        end = dt.datetime.now(dt.timezone.utc)
        bars = self.c.bars_range(self.cid, end - dt.timedelta(days=3), end, live=config.LIVE_DATA)
        done = [b for b in bars if b.t + 60 <= time.time()]
        for b in done:
            self.bar_agg.add_bar(b)
            self.strategy.on_bar(b, 0.0, live=False)
        self.last_bar_t = done[-1].t if done else 0.0
        log("warm_up", bars=len(done), levels=self.strategy.levels, bias=self.strategy.bias)

    def new_bars(self, now):
        end = dt.datetime.fromtimestamp(now, dt.timezone.utc)
        bars = self.c.bars(self.cid, end - dt.timedelta(minutes=15), end, limit=30, live=config.LIVE_DATA)
        fresh = [b for b in bars if b.t > self.last_bar_t and b.t + 60 <= now]
        if fresh:
            self.last_bar_t = fresh[-1].t
        return fresh

    def step(self, now):
        signal_ = None
        for b in self.new_bars(now):
            self.bar_agg.add_bar(b)
            agg, source = self.aggression(b.t + 60)
            sig = self.strategy.on_bar(b, agg, walls=self.walls, live=True)
            self.broker.check(b.h, b.l, b.t + 60)
            if self.trade:
                d = 1 if self.trade["side"] == "long" else -1
                self.trade["peak"] = max(self.trade["peak"], b.h) if d > 0 else min(self.trade["peak"], b.l)
            log("bar", t=f"{et(b.t):%H:%M}", o=b.o, h=b.h, l=b.l, c=b.c, agg=round(agg, 3), agg_source=source,
                note=self.strategy.note, day_pnl=round(self.guard.pnl, 2))
            if sig:
                signal_ = sig
                log("signal", **sig)

        price = self.stream.last_price if self.stream.healthy() and self.stream.last_price else None
        if price:
            self.broker.check(price, price, now)
        self._sync_day_pnl(now)
        pos = self.broker.position()

        if self.trade and not pos:
            self._trade_closed(now)
        elif pos and not self.trade:
            if not self.foreign_logged:
                log("unknown_position", position=pos, message="a position the bot did not open: no new trades")
                self.foreign_logged = True
            return
        elif self.trade and pos:
            self._manage(pos, price, now)
            return

        self.foreign_logged = False
        if signal_ and "skip" not in signal_ and not self.trade:
            ok, why = self.guard.can_enter(now)
            if ok:
                self._enter(signal_, now)
            else:
                log("entry_blocked", reason=why, signal=signal_)

    def _sync_day_pnl(self, now):
        if now - self.last_pnl_sync < 60:
            return
        self.last_pnl_sync = now
        td = trading_day(now, config.DAY_START)
        h, m = config.DAY_START.split(":")
        start_et = dt.datetime.combine(td - dt.timedelta(days=1), dt.time(int(h), int(m)), tzinfo=et(now).tzinfo)
        pnl = self.broker.day_pnl(start_et)
        if pnl is not None:
            self.guard.set_day_pnl(now, pnl)

    def _enter(self, sig, now):
        side, size = sig["side"], sig["size"]
        tag = f"ls-{int(now)}"
        fill = self.broker.enter(side, size, sig["entry"], tag)
        if fill is None:
            log("critical", message="entry sent but no position appeared; check TopstepX", signal=sig)
            return
        d = 1 if side == "long" else -1
        if config.FIXED_STOP_PTS:   # keep the strict distance from the actual fill
            sig = {**sig, "stop": self.strategy.round_tick(fill - d * config.FIXED_STOP_PTS, up=(side == "short"))}
        if (fill - sig["stop"]) * d <= 0 or (sig["target"] - fill) * d <= 0:
            self.broker.flatten(fill)
            log("trade_aborted", reason="filled beyond the stop or target", fill=fill, signal=sig)
            return
        try:
            self.broker.set_exits(side, size, sig["stop"], sig["target"], tag)
        except PXError as e:
            self.broker.flatten(fill)
            log("critical", message="could not place the stop; position closed", error=str(e))
            return
        self.trade = {"side": side, "size": size, "entry": fill, "stop": sig["stop"], "initial_stop": sig["stop"],
                      "target": sig["target"], "peak": fill, "opened": now, "tag": tag}
        log("trade_open", **self.trade, risk_usd=sig["risk_usd"], reward_usd=sig["reward_usd"], bias=sig["bias"])

    def _manage(self, pos, price, now):
        t = self.trade
        d = 1 if t["side"] == "long" else -1
        if price:
            t["peak"] = max(t["peak"], price) if d > 0 else min(t["peak"], price)
        if self.guard.must_flatten(now):
            self.broker.flatten(price)
            log("flatten", reason="Topstep cutoff (4:10pm ET)")
            return
        new = next_stop(t["side"], t["entry"], t["stop"], t["peak"], t["size"], config.POINT_VALUE,
                        config.TICK_SIZE, config)
        if new != t["stop"] and now - self.last_stop_move >= 3:
            try:
                self.broker.move_stop(new)
                stage = "trail" if (new - t["entry"]) * d > config.BE_OFFSET_TICKS * config.TICK_SIZE else "breakeven"
                log("stop_moved", stage=stage, stop=new, peak=t["peak"])
                t["stop"] = new
                self.last_stop_move = now
            except PXError as e:
                log("error", error=f"stop move failed: {e}")

    def _trade_closed(self, now):
        t = self.trade
        res = self.broker.trade_result(t["opened"])
        pnl = res[1] if res else 0.0
        if self.live:
            self.broker.cleanup()
        self.guard.record(now, pnl)
        log("trade_closed", pnl=pnl, how=res[2] if res else "unknown", **t,
            day_pnl=round(self.guard.pnl, 2), losses_today=self.guard.losses)
        self.trade = None

    def run(self):
        log("start", mode="LIVE" if self.live else "PAPER", account=self.account.get("name"),
            contract=self.contract["name"], tick=config.TICK_SIZE, point_value=config.POINT_VALUE)
        self.warm_up()
        self.stream.start()
        errors = 0
        while self.running:
            if os.path.exists(STOP_FILE):
                log("stop_file", message="STOP file found: flattening and exiting")
                self.broker.flatten(self.stream.last_price)
                break
            try:
                self.step(time.time())
                errors = 0
            except (PXError, OSError, KeyError, ValueError, TypeError) as e:
                errors += 1
                log("error", error=str(e))
                time.sleep(min(60, config.POLL_SEC * 2 ** min(errors, 4)))
                continue
            time.sleep(config.POLL_SEC)
        self.stream.stop()
        if self.trade:
            log("exit", message="bot stopped with a position open; its stop and target stay in place")
        log("stop")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="place real orders (default: paper mode)")
    args = ap.parse_args()
    client, contract = connect()
    account = pick_account(client)
    if not account.get("canTrade", True):
        sys.exit(f"Account {account.get('name')} cannot trade right now.")
    if args.live:
        broker = LiveBroker(client, account["id"], contract)
        if broker.position():
            sys.exit("There is already an open MNQ position on this account. Close it before starting the bot.")
    else:
        broker = PaperBroker(config.POINT_VALUE, config.FEE_PER_CONTRACT_RT)
    bot = Bot(client, account, contract, broker, args.live)

    def stop(*_):
        bot.running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    bot.run()


if __name__ == "__main__":
    main()
