"""TopstepX level sweep bot.

    python bot.py                 # PAPER: real market data, no orders; asks "would you take it?"
    python bot.py --live          # LIVE, semi-automatic: each trade waits for Accept on the desk page
    python bot.py --live --auto   # LIVE, fully automatic

The desk page is at http://127.0.0.1:8766/. Stop with Ctrl+C: an open trade keeps its linked stop
and target. To flatten and exit from another window, create a file named STOP in this folder.

Live mode needs the account in Auto OCO Brackets mode (TopstepX Settings > Risk Settings): the entry
order carries a linked stop and target, so one exit filling always cancels the other.
"""
import argparse
import datetime as dt
import json
import math
import os
import signal
import sys
import time

import config
from gamma import Gamma
from mylevels import MyLevels
from levels import Aggression, et, in_window, trading_day
from manage import DayGuard, next_stop
from projectx import (ORDER_LIMIT, ORDER_MARKET, ORDER_STOP, POS_LONG, SIDE_BUY, SIDE_SELL,
                      PXError, ProjectX)
from strategy import make_strategy
from stream import MarketStream, Recorder
from ui import Desk, serve

STOP_FILE = os.path.join(config.HERE, "STOP")
BRACKET_HINT = ("Turn on Auto OCO Brackets for this account in TopstepX (Settings > Risk Settings) "
                "so every entry carries a linked stop and target.")


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
    """Simulated fills on live prices: 1 tick of entry slippage, the stop fills on a touch, the target
    only when price trades 1 tick through it."""

    def __init__(self, point_value, fee, tick):
        self.pv, self.fee, self.tick = point_value, fee, tick
        self.pos = None
        self.closed = []      # (ts, pnl, reason)

    def position(self):
        return self.pos

    def balance(self):
        return None           # paper mode: no Combine balance, the target is never shortened

    def enter(self, side, size, price, tag, stop_ticks, target_ticks):
        d = 1 if side == "long" else -1
        fill = price + d * self.tick
        self.pos = {"side": side, "size": size, "entry": fill,
                    "stop": fill - d * stop_ticks * self.tick, "target": fill + d * target_ticks * self.tick}
        return fill

    def stop_order(self):
        return {"stopPrice": self.pos["stop"]} if self.pos else None

    def move_stop(self, stop):
        self.pos["stop"] = stop

    def check(self, hi, lo, ts):
        p = self.pos
        if not p:
            return
        d = 1 if p["side"] == "long" else -1
        if (d > 0 and lo <= p["stop"]) or (d < 0 and hi >= p["stop"]):
            self._close(p["stop"], ts, "stop")
        elif (d > 0 and hi >= p["target"] + self.tick) or (d < 0 and lo <= p["target"] - self.tick):
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

    def trade_result(self, trade, price, now):
        return self.closed.pop(0) if self.closed else (now, 0.0, "unknown")

    def day_pnl(self, day_start):
        return None   # the DayGuard keeps paper P&L itself

    def working_orders(self):
        return []


class LiveBroker:
    def __init__(self, client, account_id, contract):
        self.c, self.acct, self.contract = client, account_id, contract
        self.cid = contract["id"]

    def position(self):
        for p in self.c.positions(self.acct):
            if p.get("contractId") == self.cid and p.get("size"):
                return {"side": "long" if p["type"] == POS_LONG else "short",
                        "size": int(p["size"]), "entry": float(p["averagePrice"])}
        return None

    def working_orders(self):
        return [o for o in self.c.open_orders(self.acct) if o.get("contractId") == self.cid]

    def balance(self):
        for a in self.c.accounts():
            if str(a.get("id")) == str(self.acct) and a.get("balance") is not None:
                return float(a["balance"])
        return None

    def enter(self, side, size, price, tag, stop_ticks, target_ticks):
        """Market order with a linked stop and target. Returns the fill price, or None if no position
        appeared yet (the bot keeps watching for it and adopts it when it does)."""
        self.c.place(self.acct, self.cid, ORDER_MARKET, SIDE_BUY if side == "long" else SIDE_SELL, size,
                     tag=tag, stop_ticks=stop_ticks, target_ticks=target_ticks)
        for _ in range(20):
            time.sleep(0.5)
            pos = self.position()
            if pos:
                return pos["entry"]
        return None

    def stop_order(self):
        """The working protective stop (the bracket's stop leg), if any."""
        stops = [o for o in self.working_orders() if o.get("type") == ORDER_STOP]
        return stops[0] if stops else None

    def move_stop(self, stop):
        order = self.stop_order()
        if not order:
            raise PXError("move_stop", "no-stop", "no working stop order found")
        self.c.modify(self.acct, order["id"], stop_price=stop)

    def check(self, hi, lo, ts):
        pass   # the exchange handles exits

    def flatten(self, price=None):
        if self.position():
            self.c.close_position(self.acct, self.cid)
        for o in self.working_orders():
            try:
                self.c.cancel(self.acct, o["id"])
            except PXError as e:
                log("error", error=f"cancel {o['id']}: {e}")

    def _fills(self, start, this_contract=True):
        return [t for t in self.c.trades(self.acct, start)
                if not t.get("voided") and (not this_contract or t.get("contractId") == self.cid)]

    def trade_result(self, trade, price, now):
        """(ts, net P&L, how) once the closing fill is listed; None while waiting (up to 30 s);
        after that an estimate from the last price, so the daily loss rule still counts it."""
        start = dt.datetime.fromtimestamp(trade["opened"] - 5, dt.timezone.utc)
        fills = self._fills(start)
        if any(t.get("profitAndLoss") is not None for t in fills):
            net = sum((t.get("profitAndLoss") or 0.0) - (t.get("fees") or 0.0) for t in fills)
            return (now, round(net, 2), "exchange")
        if now - trade.get("gone_at", now) < 30:
            return None
        d = 1 if trade["side"] == "long" else -1
        est = ((price or trade["stop"]) - trade["entry"]) * d * trade["size"] * config.POINT_VALUE
        return (now, round(est - config.FEE_PER_CONTRACT_RT * trade["size"], 2), "estimated")

    def day_pnl(self, day_start):
        # consistency counts every trade on the account
        return sum((t.get("profitAndLoss") or 0.0) - (t.get("fees") or 0.0)
                   for t in self._fills(day_start, this_contract=False))


# -- the bot ---------------------------------------------------------------------------

def _session_name(ts):
    for name, (s, e) in (("Asia", ("18:00", "02:00")), ("London", ("02:00", "08:00")), ("NY AM", ("08:00", "12:00")),
                         ("NY PM", ("12:00", "16:10"))):
        if in_window(ts, s, e):
            return name
    return "Closed"


class Bot:
    def __init__(self, client, account, contract, broker, mode, desk=None, record=False):
        self.c, self.account, self.contract, self.broker = client, account, contract, broker
        self.mode = mode                     # "paper", "confirm" or "auto"
        self.live = mode != "paper"
        self.cid = contract["id"]
        self.strategy = make_strategy(config, config.TICK_SIZE, config.POINT_VALUE)
        self.bar_agg = Aggression(config.AGG_WINDOW_MIN)
        self.tape_agg = Aggression(config.AGG_WINDOW_MIN)
        recorder = Recorder(config.RECORD_DIR) if record else None
        self.stream = MarketStream(config.MARKET_HUB_URL, self._token, self.cid, self.tape_agg, log, recorder)
        self.guard = DayGuard(config, config.STATE_FILE if self.live else None)
        self.desk = desk or Desk(config.DECISIONS_FILE, log, config.NTFY_TOPIC)
        self.last_bar_t = 0.0
        self.last_close = None
        self.trade = None            # the bot's open trade
        self.awaiting = None         # (desk id, signal) waiting for Accept in confirm mode
        self.pending_entry = None    # an entry sent whose position has not appeared yet
        self.halted = None           # reason trading is stopped for good (e.g. brackets not enabled)
        self.last_stop_move = 0.0
        self.last_pnl_sync = 0.0
        self.last_stale_log = 0.0
        self.foreign_logged = False
        self.last_bias = None
        self.gamma = Gamma(config, self._price_at, log) if config.GAMMA_MODE != "off" else None
        self.my_levels = MyLevels(config, self.desk._push, log)
        self.desk.levels = self.my_levels
        self.desk.bias_file = config.BIAS_FILE
        if os.path.exists(config.BIAS_FILE):
            try:
                with open(config.BIAS_FILE) as f:
                    self.desk.bias_choice = json.load(f) or {}
            except (OSError, ValueError):
                pass
        self.running = True

    def _price_at(self, ts):
        """MNQ close of the minute at ts (to convert the gamma levels from cash to futures prices)."""
        t = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
        bars = self.c.bars(self.cid, t - dt.timedelta(minutes=5), t + dt.timedelta(minutes=1), limit=10,
                           live=config.LIVE_DATA)
        return bars[-1].c if bars else None

    def _gamma_check(self, sig, now):
        """Attach the gamma levels to a signal; in "filter" mode drop fades aimed away from the flip."""
        if not self.gamma:
            return sig
        g = self.gamma.check(sig, now)
        sig = {**sig, "gamma": g}
        if g is None:
            log("gamma_missing", message="no fresh gamma levels: signal not checked", signal=sig)
        elif config.GAMMA_MODE == "filter" and not g["toward_flip"]:
            why = f"fade aimed away from the gamma flip ({g['flip']})"
            log("entry_blocked", reason=why, signal=sig)
            self.desk.event(f"{sig['side']} not taken: {why}")
            return None
        return sig

    def _token(self):
        self.c._ensure_token()
        return self.c.token

    def aggression(self, now):
        # the tape is only trusted once it covers a full window; right after connecting it holds
        # a few seconds of trades, so the bar estimate is used until then
        if self.stream.healthy() and self.tape_agg.covered(time.time()):
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
        if done:
            self.last_bar_t, self.last_close = done[-1].t, done[-1].c
        log("warm_up", bars=len(done), levels=self.strategy.levels, bias=self.strategy.bias)

    def new_bars(self, now):
        end = dt.datetime.fromtimestamp(now, dt.timezone.utc)
        bars = self.c.bars(self.cid, end - dt.timedelta(minutes=15), end, limit=30, live=config.LIVE_DATA)
        fresh = [b for b in bars if b.t > self.last_bar_t and b.t + 60 <= now]
        if fresh:
            self.last_bar_t, self.last_close = fresh[-1].t, fresh[-1].c
        return fresh

    def price(self):
        if self.stream.healthy() and self.stream.last_price:
            return self.stream.last_price
        return self.last_close

    # -- one loop -----------------------------------------------------------------------
    def step(self, now):
        new_signal = None
        td = str(trading_day(now, config.DAY_START))
        self.strategy.override = self.desk.bias_for(td)
        for b in self.new_bars(now):
            self.bar_agg.add_bar(b)
            agg, source = self.aggression(b.t + 60)
            sig = self.strategy.on_bar(b, agg, walls=self.walls, live=True)
            self.my_levels.update(b.l, b.h, b.t + 60)
            self.broker.check(b.h, b.l, b.t + 60)
            if self.trade:
                d = 1 if self.trade["side"] == "long" else -1
                self.trade["peak"] = max(self.trade["peak"], b.h) if d > 0 else min(self.trade["peak"], b.l)
            log("bar", t=f"{et(b.t):%H:%M}", o=b.o, h=b.h, l=b.l, c=b.c, agg=round(agg, 3), agg_source=source,
                note=self.strategy.note, day_pnl=round(self.guard.pnl, 2))
            bias = getattr(self.strategy, "bias", None)
            if bias and bias != self.last_bias:
                self.last_bias = bias
                log("bias", **bias)
                if bias.get("why"):
                    allow = bias.get("allow", "both")
                    self.desk.event(f"Bias: {bias['why']} -> " + ("longs and shorts" if allow == "both" else
                                    "no trade tonight" if allow == "none" else f"{allow}s only tonight"))
            if sig and "skip" not in sig:
                sig = self._gamma_check(sig, now)
            if sig:
                log("signal", **sig)
                if "skip" not in sig:
                    new_signal = sig

        if self.gamma:
            had = self.gamma.snap
            try:
                self.gamma.refresh(now)
            except (PXError, OSError) as e:
                log("gamma_error", error=str(e))
            if self.gamma.snap is not had and self.gamma.snap:
                g = self.gamma.levels()
                self.desk.event(f"Gamma ({g['regime']}): flip {g['flip']}  call wall {g['call_wall']}  "
                                f"put wall {g['put_wall']} (MNQ prices)")
        price = self.price()
        if price:
            self.broker.check(price, price, now)
            self.my_levels.update(price, price, now)
        self._sync_day_pnl(now)
        self._stale_check(now)
        pos = self.broker.position()
        self._update_desk(price, pos, now)

        # reconcile the account with what the bot believes
        if self.trade and not pos:
            self._trade_closed(price, now)
            return
        if pos and not self.trade:
            if self.pending_entry and now - self.pending_entry["sent"] < 120:
                self._adopt(pos, now)
            else:
                if not self.foreign_logged:
                    log("unknown_position", position=pos, message="a position the bot did not open: no new trades")
                    self.desk.event("A position the bot did not open is on the account; the bot is standing aside.")
                    self.foreign_logged = True
                return
        if self.trade and pos:
            if pos["side"] != self.trade["side"] or pos["size"] != self.trade["size"]:
                self.broker.flatten(price)
                log("critical", message="position does not match the bot's trade: flattened",
                    position=pos, trade=self.trade)
                self.desk.event("Position mismatch: the bot flattened it. Check TopstepX.")
                self.trade = None
                return
            self._manage(pos, price, now)
            return
        self.foreign_logged = False
        if self.pending_entry and now - self.pending_entry["sent"] >= 120:
            log("error", error="entry sent but no position appeared within 2 minutes", signal=self.pending_entry["sig"])
            self.pending_entry = None

        # entries
        if new_signal:
            if self.mode == "confirm":
                if not self.awaiting:
                    sid = self.desk.ask(new_signal, "confirm", config.CONFIRM_TIMEOUT_SEC, self._snapshot(price))
                    self.awaiting = (sid, new_signal)
            else:
                self.desk.ask(new_signal, "paper", config.CONFIRM_TIMEOUT_SEC, self._snapshot(price))
                self._try_enter(new_signal, price, now)
        if self.awaiting:
            sid, sig = self.awaiting
            answer = self.desk.answer(sid)
            if answer:
                self.awaiting = None
                self.desk.done(sid)
                if answer == "accept":
                    self._try_enter(sig, price, now)
                else:
                    log("signal_" + answer, signal=sig)

    def _snapshot(self, price):
        agg, source = self.aggression(time.time())
        return {"price": price, "agg": round(agg, 3), "agg_source": source, "note": self.strategy.note,
                "bias": self.strategy.bias, "levels": self.strategy.levels,
                "walls_above": self.walls("long")[:5], "walls_below": self.walls("short")[:5]}

    def _update_desk(self, price, pos, now):
        agg, source = self.aggression(now)
        t = self.trade
        trade = None
        if t:
            d = 1 if t["side"] == "long" else -1
            risk = abs(t["entry"] - t["initial_stop"]) * t["size"] * config.POINT_VALUE
            open_pnl = None if price is None else (price - t["entry"]) * d * t["size"] * config.POINT_VALUE
            trade = {"side": t["side"], "size": t["size"], "entry": t["entry"], "stop": t["stop"],
                     "target": t["target"], "open_pnl": None if open_pnl is None else round(open_pnl, 2),
                     "open_r": None if open_pnl is None or not risk else round(open_pnl / risk, 2),
                     "minutes": round((now - t["opened"]) / 60)}
        if self.live and now - getattr(self, "_bal_t", 0) > 60:
            self._bal_t = now
            try:
                self._balance = self.broker.balance()
            except (PXError, OSError):
                pass
        bal = getattr(self, "_balance", None)
        combine = None
        if bal is not None and config.COMBINE_TARGET_USD:
            combine = {"balance": bal, "profit": round(bal - config.COMBINE_START_BALANCE, 2),
                       "target": config.COMBINE_TARGET_USD}
        can, why = self.guard.can_enter(now)
        st = self.strategy.state() if hasattr(self.strategy, "state") else {}
        g = self.gamma.levels() if self.gamma else {}
        bias = st.get("bias") or {}
        votes = []
        if bias.get("why"):
            votes.append({"source": "NY session", "side": bias.get("allow") if bias.get("allow") in ("long", "short") else None,
                          "why": bias["why"] + " (NY down -> Asia recovers)"})
        if g.get("flip") and price:
            toward = "short" if price > g["flip"] else "long"
            votes.append({"source": "Gamma", "side": toward,
                          "why": f"{(g.get('regime') or '').replace('_', ' ')}; price {'above' if price > g['flip'] else 'below'} the flip "
                                 f"{g['flip']:,.2f}: fades toward the flip won 47% (Apr-Sep 2026)"})
        if abs(agg) >= config.AGG_MIN_RATIO:
            votes.append({"source": "Tape aggression", "side": "long" if agg > 0 else "short",
                          "why": f"{agg:+.2f} over 15 min ({'buyers' if agg > 0 else 'sellers'} in control)"})
        if bias.get("ny_close") and price:
            votes.append({"source": "Price vs NY close", "side": "long" if price > bias["ny_close"] else "short",
                          "why": f"{price:,.2f} vs {bias['ny_close']:,.2f} (above = buyers held the gains)"})
        td = str(trading_day(now, config.DAY_START))
        override = self.desk.bias_for(td)
        allow_now = override or bias.get("allow") or "both"
        self.desk.update(trading_day=td, votes=votes, bias_override=override,
                         allow_now={"long": "longs only", "short": "shorts only", "both": "longs and shorts",
                                    "none": "no trade"}.get(allow_now, allow_now))
        self.desk.update(
            mode={"paper": "PAPER", "confirm": "LIVE (confirm)", "auto": "LIVE (auto)"}[self.mode],
            strategy=config.STRATEGY, contract=self.contract.get("name"),
            clock=et(now).strftime("%a %H:%M:%S ET"), session=_session_name(now),
            price=price, agg=round(agg, 3), agg_source=source, position=pos, trade=trade,
            stop=t["stop"] if t else None, day_pnl=round(self.guard.pnl, 2), losses=self.guard.losses,
            trades=self.guard.trades, max_losses=config.DAILY_MAX_LOSSES, day_cap=config.DAILY_PROFIT_STOP_USD,
            can_enter=can, why_not=None if can else why, combine=combine,
            bias=st.get("bias") or {}, range_high=st.get("range_high"), range_low=st.get("range_low"),
            gamma=g, walls=self.stream.top_walls(config.WALL_MIN_SIZE, config.WALL_MULT, price)
            if self.stream.healthy() else {"above": [], "below": []},
            data_age=round(now - (self.last_bar_t + 60)) if self.last_bar_t else None,
            stream_ok=self.stream.healthy(), note=self.halted or self.strategy.note)

    def _stale_check(self, now):
        age = now - (self.last_bar_t + 60)
        if age > config.MAX_BAR_AGE_SEC and now - self.last_stale_log > 300 and self.guard.can_enter(now)[0]:
            log("stale_data", seconds_since_last_bar=round(age))
            self.last_stale_log = now

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

    def _try_enter(self, sig, price, now):
        if self.halted:
            log("entry_blocked", reason=self.halted, signal=sig)
            return
        ok, why = self.guard.can_enter(now)
        if ok and self.trade:
            ok, why = False, "a trade is already open"
        if ok and now - (self.last_bar_t + 60) > config.MAX_BAR_AGE_SEC:
            ok, why = False, "market data is stale"
        d = 1 if sig["side"] == "long" else -1
        if ok and price is not None and abs(price - sig["entry"]) > config.MAX_ENTRY_DRIFT_PTS:
            ok, why = False, f"price moved {abs(price - sig['entry']):.2f} pts from the signal"
        if ok and price is not None and ((price - sig["stop"]) * d <= 0 or (sig["target"] - price) * d <= 0):
            ok, why = False, "price already past the stop or target"
        if not ok:
            log("entry_blocked", reason=why, signal=sig)
            self.desk.event(f"{sig['side']} not taken: {why}")
            return
        sig = self._combine_target(sig)
        tick = config.TICK_SIZE
        stop_ticks = round((self._fixed_stop(sig) or abs(sig["entry"] - sig["stop"])) / tick)
        target_ticks = round(abs(sig["target"] - sig["entry"]) / tick)
        tag = f"ls-{int(now)}"
        self.pending_entry = {"sent": now, "sig": sig, "tag": tag}
        try:
            fill = self.broker.enter(sig["side"], sig["size"], price or sig["entry"], tag, stop_ticks, target_ticks)
        except PXError as e:
            self.pending_entry = None
            if "Position Brackets" in str(e.message):
                self.halted = "trading halted: " + BRACKET_HINT
            log("entry_failed", error=str(e), hint=BRACKET_HINT if self.halted else None, signal=sig)
            self.desk.event(f"Entry failed: {e.message}")
            return
        except OSError as e:
            # the order may or may not have reached TopstepX: keep pending_entry so a position is adopted
            log("error", error=f"entry request failed ({e}); watching for the position", signal=sig)
            return
        if fill is None:
            log("entry_pending", message="order sent; waiting for the position to appear", signal=sig)
            return
        self._adopt(self.broker.position(), now)

    def _combine_target(self, sig):
        """Combine: if the trade's target would pass the profit target with room to spare, aim only for
        what is still needed (+$50 and fees). Losing less of a winner to a reversal near the goal."""
        if not config.COMBINE_TARGET_USD:
            return sig
        try:
            bal = self.broker.balance()
        except (PXError, OSError) as e:
            log("error", error=f"balance unavailable ({e}); target unchanged")
            return sig
        if bal is None:
            return sig
        need = config.COMBINE_START_BALANCE + config.COMBINE_TARGET_USD - bal
        if need <= 0:
            return sig
        size, pv, tick = sig["size"], config.POINT_VALUE, config.TICK_SIZE
        d = 1 if sig["side"] == "long" else -1
        pts = (need + 50 + config.FEE_PER_CONTRACT_RT * size) / (size * pv)
        pts = max(tick, math.ceil(pts / tick - 1e-9) * tick)
        if pts >= abs(sig["target"] - sig["entry"]):
            return sig
        target = round(sig["entry"] + d * pts, 10)
        log("target_shortened", balance=bal, still_needed=round(need, 2), old_target=sig["target"], target=target)
        self.desk.event(f"${need:,.0f} left to pass the Combine: target shortened to {target}")
        return {**sig, "target": target, "reward_usd": round(pts * size * pv, 2)}

    @staticmethod
    def _fixed_stop(sig):
        """The strict stop distance applies to the level sweep; the Asia sweep stops at its structure."""
        return config.FIXED_STOP_PTS if sig.get("strategy", "level_sweep") == "level_sweep" else None

    def _adopt(self, pos, now):
        """Start managing a position the bot's own entry created."""
        sig = self.pending_entry["sig"]
        self.pending_entry = None
        if not pos:
            return
        d = 1 if pos["side"] == "long" else -1
        tick = config.TICK_SIZE
        stop = pos["entry"] - d * round((self._fixed_stop(sig) or abs(sig["entry"] - sig["stop"])) / tick) * tick
        target = pos["entry"] + d * round(abs(sig["target"] - sig["entry"]) / tick) * tick
        self.trade = {"side": pos["side"], "size": pos["size"], "entry": pos["entry"], "stop": stop,
                      "initial_stop": stop, "target": target, "peak": pos["entry"], "opened": now,
                      "exit_by": sig.get("exit_by"),
                      "trail": sig.get("trail") or sig.get("strategy", "level_sweep") == "level_sweep" or config.ASIA_TRAIL}
        log("trade_open", **self.trade, risk_usd=sig.get("risk_usd"), reward_usd=sig.get("reward_usd"),
            bias=sig.get("bias"))
        self.desk.event(f"OPEN {pos['side']} {pos['size']} @ {pos['entry']}  stop {stop}  target {target}")

    def _manage(self, pos, price, now):
        t = self.trade
        d = 1 if t["side"] == "long" else -1
        if self.live and now - t["opened"] > 10 and not self.broker.stop_order():
            self.broker.flatten(price)
            log("critical", message="no working stop on the position: flattened", trade=t)
            self.desk.event("No stop was working on the position, so the bot flattened it.")
            return
        if self.guard.must_flatten(now):
            self.broker.flatten(price)
            log("flatten", reason=f"flat before the {config.FLAT_BY} ET news window")
            return
        if t.get("exit_by") and now >= t["exit_by"]:
            self.broker.flatten(price)
            log("flatten", reason="time exit")
            return
        if price:
            t["peak"] = max(t["peak"], price) if d > 0 else min(t["peak"], price)
            open_pnl = (price - t["entry"]) * d * t["size"] * config.POINT_VALUE
            if self.guard.day_capped(open_pnl):
                self.broker.flatten(price)
                log("flatten", reason=f"day reached +${config.DAILY_PROFIT_STOP_USD:.0f} with the open trade (consistency)")
                return
        if not t.get("trail", True):
            return
        if t.get("trail") == "ema":
            new = self.strategy.trail_stop(t["side"])
            if new is None or (new - t["stop"]) * d <= 0:
                return
        else:
            new = next_stop(t["side"], t["entry"], t["stop"], t["peak"], t["size"], config.POINT_VALUE,
                            config.TICK_SIZE, config)
        if new == t["stop"] or now - self.last_stop_move < 3:
            return
        if price and (price - new) * d <= config.TICK_SIZE:
            self.broker.flatten(price)   # price is already back through where the stop belongs
            log("flatten", reason="price fell back through the trailing stop level", stop=new, price=price)
            return
        try:
            self.broker.move_stop(new)
            stage = "trail" if (new - t["entry"]) * d > config.BE_OFFSET_TICKS * config.TICK_SIZE else "breakeven"
            log("stop_moved", stage=stage, stop=new, peak=t["peak"])
            self.desk.event(f"stop moved to {new} ({stage})")
            t["stop"] = new
            self.last_stop_move = now
        except PXError as e:
            log("error", error=f"stop move failed: {e}")

    def _trade_closed(self, price, now):
        t = self.trade
        t.setdefault("gone_at", now)
        res = self.broker.trade_result(t, price, now)
        if res is None:
            return   # waiting for the closing fill to be listed
        if self.live:
            for o in self.broker.working_orders():   # any leftover leg
                try:
                    self.c.cancel(self.account["id"], o["id"])
                except PXError as e:
                    log("error", error=f"cancel {o['id']}: {e}")
        self.guard.record(now, res[1], add_pnl=not self.live)
        log("trade_closed", pnl=res[1], how=res[2], **t, day_pnl=round(self.guard.pnl, 2),
            losses_today=self.guard.losses)
        self.desk.event(f"CLOSED {t['side']}: ${res[1]:,.2f} ({res[2]})")
        self.desk.update(last_result={"pnl": round(res[1], 2), "side": t["side"], "ts": now})
        self.trade = None

    def run(self):
        log("start", mode=self.mode.upper(), strategy=config.STRATEGY, account=self.account.get("name"),
            contract=self.contract["name"],
            tick=config.TICK_SIZE, point_value=config.POINT_VALUE)
        self.warm_up()
        self.stream.start()
        errors = 0
        while self.running:
            if os.path.exists(STOP_FILE):
                log("stop_file", message="STOP file found: flattening and exiting")
                self.broker.flatten(self.price())
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
            log("exit", message="bot stopped with a position open; its linked stop and target stay in place")
        log("stop")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="place real orders (each waits for Accept unless --auto)")
    ap.add_argument("--auto", action="store_true", help="with --live: trade without asking")
    args = ap.parse_args()
    if args.auto and not args.live:
        sys.exit("--auto only applies with --live.")
    client, contract = connect()
    account = pick_account(client)
    if not account.get("canTrade", True):
        sys.exit(f"Account {account.get('name')} cannot trade right now.")
    mode = "auto" if args.auto else "confirm" if args.live else "paper"
    if args.live:
        broker = LiveBroker(client, account["id"], contract)
        if broker.position() or broker.working_orders():
            sys.exit("There is already an MNQ position or working MNQ order on this account. "
                     "Close/cancel it before starting the bot.")
    else:
        broker = PaperBroker(config.POINT_VALUE, config.FEE_PER_CONTRACT_RT, config.TICK_SIZE)
    desk = Desk(config.DECISIONS_FILE, log, config.NTFY_TOPIC)
    try:
        serve(desk, config.UI_HOST, config.UI_PORT)
    except OSError as e:
        sys.exit(f"Could not open the desk page on port {config.UI_PORT}: {e}")
    print(f"Desk page: http://{config.UI_HOST}:{config.UI_PORT}/", flush=True)
    bot = Bot(client, account, contract, broker, mode, desk=desk, record=config.RECORD)

    def stop(*_):
        bot.running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    bot.run()


if __name__ == "__main__":
    main()
