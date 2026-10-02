import datetime as dt
import gzip
import io
import json
import os
import sys
import tempfile
import time
import types
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest  # noqa: E402
import bot  # noqa: E402
import config  # noqa: E402
from levels import ET, Aggression, SessionTracker, in_window, trading_day  # noqa: E402
from manage import DayGuard, next_stop  # noqa: E402
from projectx import PXError, ProjectX  # noqa: E402
from strategy import AsiaRangeSweep, Bar, LevelSweep  # noqa: E402
from stream import MarketStream, Recorder  # noqa: E402
from ui import Desk  # noqa: E402


def params(**over):
    p = types.SimpleNamespace(**{k: getattr(config, k) for k in dir(config) if k.isupper()})
    for k, v in over.items():
        setattr(p, k, v)
    return p


def ny(day, hour, minute=0):
    """Unix time for a New York wall-clock time in October 2026 (5 Oct is a Monday)."""
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=ET).timestamp()


# A down move with a push to a new low, a consolidation, a sweep of the consolidation high that
# closes back below it (the manipulation), then a close below the sweep bar's low (confirmation).
SHORT_SETUP = [
    (100, 101, 99, 100), (100, 102, 99.5, 101), (101, 110, 100, 108), (108, 108.5, 103, 104),
    (104, 105, 98, 99), (99, 100, 90, 91), (91, 96, 91, 95), (95, 97, 93, 96),
    (96, 96.5, 88, 89), (89, 89.5, 80, 81), (81, 86, 81, 85), (85, 92, 84, 90),
    (90, 91, 86, 87), (87, 88, 85, 86),
    (86, 93, 85.5, 88),     # 14: trades above the 92 consolidation high, closes back below
    (88, 88.5, 83, 84),     # 15: closes below 85.5, the sweep bar's low -> short at 84
]


def make_bars(rows, start):
    return [Bar(start + 60 * i, *r, v=100) for i, r in enumerate(rows)]


class StrategyTests(unittest.TestCase):
    def run_setup(self, agg, p=None):
        s = LevelSweep(p or params(), 0.25, 2.0)
        out = None
        for b in make_bars(SHORT_SETUP, ny(5, 20)):
            out = s.on_bar(b, agg) or out
        return s, out

    def test_short_manipulation_signal(self):
        s, sig = self.run_setup(-0.5)
        self.assertEqual(sig["side"], "short")
        self.assertEqual(sig["entry"], 84)
        self.assertEqual(sig["stop"], 104)                 # strict 20-point stop
        self.assertEqual(sig["size"], 12)                  # 500 // (20 * 2 + 0.74)
        self.assertLessEqual(sig["risk_usd"], 500)
        self.assertAlmostEqual(sig["target"], 84 - 1500 / 24)   # capped at $1,500
        self.assertEqual(sig["reward_usd"], 1500)

    def test_no_trade_without_seller_aggression(self):
        for agg in (0.0, 0.5):
            _, sig = self.run_setup(agg)
            self.assertIsNone(sig)

    def test_structure_stop_when_not_fixed(self):
        _, sig = self.run_setup(-0.5, params(FIXED_STOP_PTS=None, MIN_STOP_PTS=6.0))
        self.assertEqual(sig["stop"], 93.5)                # sweep high 93 + 2 ticks
        self.assertEqual(sig["size"], 20)                  # 9.5 pts * $2 * 20 = $380, capped at 20

    def test_stop_too_wide_is_skipped(self):
        _, sig = self.run_setup(-0.5, params(FIXED_STOP_PTS=40.0))
        self.assertIn("skip", sig)                         # 40 pts: 6 MNQ < the 10 minimum

    def test_target_prefers_first_level_paying_enough(self):
        # 12 MNQ = $24/pt. From 84: the new low 79.75 pays $102 and ny_low (70.25) $330, both
        # under $800; london_low (front-run to 45.25) pays $930 and is inside the $1,500 cap.
        s = LevelSweep(params(), 0.25, 2.0)
        levels = [{"price": 70.0, "label": "ny_low"}, {"price": 45.0, "label": "london_low"}]
        with mock.patch.object(s.sessions, "levels", return_value=(levels, {})):
            sig = None
            for b in make_bars(SHORT_SETUP, ny(5, 20)):
                sig = s.on_bar(b, -0.5) or sig
        self.assertEqual(sig["target"], 45.25)
        self.assertEqual(sig["reward_usd"], 930)

    def test_walls_are_front_run(self):
        s = LevelSweep(params(), 0.25, 2.0)
        bars = make_bars(SHORT_SETUP, ny(5, 20))
        sig = None
        for b in bars:
            sig = s.on_bar(b, -0.5, walls=lambda side: [45.0]) or sig
        self.assertEqual(sig["target"], 45.25)             # a bid wall at 45: exit a tick above it
        self.assertLess(45.25, 84 - 1500 / 24 + 40)       # it replaced the further $1,500 cap

    def test_warm_up_never_signals(self):
        s = LevelSweep(params(), 0.25, 2.0)
        for b in make_bars(SHORT_SETUP, ny(5, 20)):
            self.assertIsNone(s.on_bar(b, -0.5, live=False))


class AsiaLiquidityTests(unittest.TestCase):
    def run_asia(self, reach, sweeps):
        s = AsiaRangeSweep(params(ASIA_LIQ_REACH=reach), 0.25, 2.0)
        s.on_bar(Bar(ny(5, 14), 205, 210, 204, 206), 0.0)          # NY PM high 210
        for i in range(60):                                         # Asia range 195-200
            s.on_bar(Bar(ny(5, 19, i), 197, 200, 195, 197), 0.0)
        return [s.on_bar(Bar(ny(5, 20, i), *r), 0.0) for i, r in enumerate(sweeps)]

    def test_fades_the_range_high_without_liquidity_filter(self):
        out = self.run_asia(0.0, [(199, 202, 198, 199)])
        self.assertEqual(out[0]["side"], "short")
        self.assertEqual(out[0]["entry"], 199)

    def test_waits_for_the_pm_high_then_fades_it(self):
        out = self.run_asia(2.0, [(199, 202, 198, 199), (199, 205, 199, 204), (204, 211, 204, 208)])
        self.assertIsNone(out[0])                                   # PM high still untaken above
        self.assertIsNone(out[1])
        self.assertEqual(out[2]["side"], "short")
        self.assertEqual(out[2]["entry"], 208)


class ManageTests(unittest.TestCase):
    def test_breakeven_then_trail_65(self):
        p = params()
        # 12 MNQ long from 100: $650 = 27.08 pts, $785 = 32.71 pts
        self.assertEqual(next_stop("long", 100, 80, 120, 12, 2.0, 0.25, p), 80)
        self.assertEqual(next_stop("long", 100, 80, 127.5, 12, 2.0, 0.25, p), 100.25)
        self.assertEqual(next_stop("long", 100, 80, 140, 12, 2.0, 0.25, p), 126.0)   # 65% of 40
        self.assertEqual(next_stop("long", 100, 126.0, 130, 12, 2.0, 0.25, p), 126.0)  # never loosens

    def test_short_trail(self):
        p = params()
        self.assertEqual(next_stop("short", 100, 120, 60, 12, 2.0, 0.25, p), 74.0)

    def test_one_loss_ends_the_day(self):
        g = DayGuard(params(DAILY_MAX_TRADES=99))          # the loss rule on its own
        t = ny(5, 20)
        self.assertTrue(g.can_enter(t)[0])
        g.record(t, -30)                                   # a scratch
        self.assertTrue(g.can_enter(t)[0])
        g.record(t, -500)
        self.assertFalse(g.can_enter(t + 60)[0])
        self.assertTrue(g.can_enter(ny(6, 20))[0])         # next trading day

    def test_profit_stop_and_window(self):
        g = DayGuard(params(DAILY_MAX_TRADES=99))          # the profit cap on its own
        g.record(ny(5, 20), 1510)
        self.assertIn("consistency", g.can_enter(ny(5, 21))[1])
        self.assertFalse(DayGuard(params()).can_enter(ny(6, 10))[0])   # 10am: outside Asia
        self.assertTrue(g.must_flatten(ny(6, 16, 6)))
        self.assertFalse(g.must_flatten(ny(6, 18, 1)))

    def test_trading_day_rolls_at_6pm(self):
        self.assertEqual(trading_day(ny(5, 17, 59)), dt.date(2026, 10, 5))
        self.assertEqual(trading_day(ny(5, 18, 0)), dt.date(2026, 10, 6))
        self.assertTrue(in_window(ny(6, 1, 30), "19:00", "02:00"))


class LevelTests(unittest.TestCase):
    def test_session_levels(self):
        st = SessionTracker()
        # Monday: NY session 9:30-16:00 from 200 down to 150, then Asia from 19:00
        for i, m in enumerate(range(9 * 60 + 30, 16 * 60)):
            px = 200 - 50 * i / 389
            st.add(Bar(ny(5, 0) + m * 60, px, px + 1, px - 1, px))
        for m in range(19 * 60, 19 * 60 + 30):
            st.add(Bar(ny(5, 0) + m * 60, 160, 170, 155, 165))
        levels, info = st.levels(ny(5, 19, 30))
        names = {x["label"]: x["price"] for x in levels}
        self.assertEqual(names["ny_high"], 201)
        self.assertEqual(names["ny_low"], 149)
        self.assertNotIn("asia_high", names)               # Asia still running
        self.assertEqual(names["prev_day_high"], 201)
        self.assertLess(info["ny_close"] - info["ny_open"], -45)

    def test_aggression(self):
        a = Aggression(15)
        a.add_trade(1000, 30, True)
        a.add_trade(1001, 10, False)
        self.assertAlmostEqual(a.ratio(1002), 0.5)
        self.assertEqual(a.ratio(1000 + 16 * 60), 0.0)     # rolled out of the window
        self.assertFalse(a.covered(1002))                  # seconds of tape: not a full reading
        self.assertTrue(a.covered(1000 + 15 * 60))
        a.add_bar(Bar(5000, 100, 101, 95, 96, v=50))       # closed near the low: mostly selling
        self.assertLess(a.ratio(5060), -0.5)


class StreamTests(unittest.TestCase):
    def test_tape_quotes_and_walls(self):
        agg = Aggression(15)
        s = MarketStream("wss://x", lambda: "tok", "CON.F.US.MNQ.Z26", agg, lambda *a, **k: None)
        s.handle("GatewayTrade", ["CON", [{"type": 1, "volume": 40, "price": 30000.25},
                                          {"type": 0, "volume": 10, "price": 30000.5}]])
        self.assertAlmostEqual(agg.ratio(), -0.6)
        self.assertEqual(s.last_price, 30000.5)
        s.handle("GatewayQuote", ["CON", {"lastPrice": 30001.0}])
        self.assertEqual(s.last_price, 30001.0)
        for i, v in enumerate([5, 6, 4, 5, 300, 7]):
            s.handle("GatewayDepth", ["CON", {"type": 2, "price": 29990 - i, "volume": v}])
        self.assertEqual(s.walls("short", 100, 4.0), [29986.0])
        s.handle("GatewayDepth", ["CON", {"type": 2, "price": 29986.0, "volume": 0}])
        self.assertEqual(s.walls("short", 100, 4.0), [])
        s.handle("GatewayDepth", ["CON", {"type": 6}])
        self.assertEqual(s.bids, {})


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ClientTests(unittest.TestCase):
    def test_order_placement_is_never_retried(self):
        c = ProjectX("https://api.x", "trader", "key")
        c.token, c.token_time = "T", 1e18
        err = urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO(b"busy"))
        with mock.patch("urllib.request.urlopen", side_effect=[err]) as m, mock.patch("time.sleep"):
            with self.assertRaises(PXError):
                c.place(5, "CON", 2, 0, 12, stop_ticks=80, target_ticks=250)
        self.assertEqual(m.call_count, 1)

    def test_bracket_body(self):
        c = ProjectX("https://api.x", "trader", "key")
        c.token, c.token_time = "T", 1e18
        ok = FakeResp(json.dumps({"success": True, "orderId": 3}).encode())
        with mock.patch("urllib.request.urlopen", return_value=ok) as m:
            c.place(5, "CON", 2, 1, 12, tag="ls-1", stop_ticks=80, target_ticks=250)    # sell (short)
        body = json.loads(m.call_args[0][0].data)
        self.assertEqual(body["stopLossBracket"], {"ticks": 80, "type": 4})        # stop above a short
        self.assertEqual(body["takeProfitBracket"], {"ticks": -250, "type": 1})   # target below
        ok = FakeResp(json.dumps({"success": True, "orderId": 4}).encode())
        with mock.patch("urllib.request.urlopen", return_value=ok) as m:
            c.place(5, "CON", 2, 0, 12, tag="ls-2", stop_ticks=80, target_ticks=250)    # buy (long)
        body = json.loads(m.call_args[0][0].data)
        # TopstepX: "Invalid stop loss ticks (80). Ticks should be less than zero when longing."
        self.assertEqual(body["stopLossBracket"], {"ticks": -80, "type": 4})
        self.assertEqual(body["takeProfitBracket"], {"ticks": 250, "type": 1})

    def test_login_and_order_body(self):
        c = ProjectX("https://api.x", "trader", "key")
        responses = [FakeResp(json.dumps({"success": True, "token": "T"}).encode()),
                     FakeResp(json.dumps({"success": True, "orderId": 77}).encode())]
        with mock.patch("urllib.request.urlopen", side_effect=responses) as m:
            oid = c.place(5, "CON.F.US.MNQ.Z26", 4, 1, 12, stop_price=104.0, tag="ls-1-sl")
        self.assertEqual(oid, 77)
        login_req, order_req = m.call_args_list[0][0][0], m.call_args_list[1][0][0]
        self.assertEqual(json.loads(login_req.data), {"userName": "trader", "apiKey": "key"})
        body = json.loads(order_req.data)
        self.assertEqual((body["type"], body["side"], body["size"], body["stopPrice"]), (4, 1, 12, 104.0))
        self.assertIsNone(body["stopLossBracket"])
        self.assertEqual(order_req.get_header("Authorization"), "Bearer T")

    def test_failure_raises(self):
        c = ProjectX("https://api.x", "trader", "key")
        c.token, c.token_time = "T", 1e18
        bad = FakeResp(json.dumps({"success": False, "errorCode": 2, "errorMessage": "Brackets..."}).encode())
        with mock.patch("urllib.request.urlopen", return_value=bad):
            with self.assertRaises(PXError) as ctx:
                c.cancel(1, 2)
        self.assertEqual(ctx.exception.code, 2)

    def test_bars_sorted(self):
        c = ProjectX("https://api.x", "trader", "key")
        c.token, c.token_time = "T", 1e18
        payload = {"success": True, "bars": [
            {"t": "2026-10-06T00:01:00+00:00", "o": 2, "h": 3, "l": 1, "c": 2, "v": 5},
            {"t": "2026-10-06T00:00:00+00:00", "o": 1, "h": 2, "l": 0, "c": 1, "v": 4}]}
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(json.dumps(payload).encode())):
            bars = c.bars("CON", dt.datetime(2026, 10, 6, tzinfo=dt.timezone.utc),
                          dt.datetime(2026, 10, 6, 1, tzinfo=dt.timezone.utc))
        self.assertEqual([b.o for b in bars], [1, 2])


class FakePX:
    """A fake ProjectX account: orders, positions and fills are scripted by each test."""

    def __init__(self):
        self.orders, self.cancelled, self.modified, self.closed = [], [], [], []
        self.open = []
        self.pos = []
        self.fills = []
        self.reject = None

    def place(self, acct, cid, typ, side, size, limit_price=None, stop_price=None, tag=None,
              stop_ticks=None, target_ticks=None):
        if self.reject:
            raise PXError("/api/Order/place", 2, self.reject)
        self.orders.append({"type": typ, "side": side, "size": size, "tag": tag,
                            "stop_ticks": stop_ticks, "target_ticks": target_ticks})
        return len(self.orders)

    def positions(self, acct):
        return self.pos

    def open_orders(self, acct):
        return self.open

    def cancel(self, acct, oid):
        self.cancelled.append(oid)
        self.open = [o for o in self.open if o["id"] != oid]

    def modify(self, acct, oid, stop_price=None, **k):
        self.modified.append((oid, stop_price))

    def close_position(self, acct, cid):
        self.closed.append(cid)
        self.pos = []

    def trades(self, acct, start):
        return self.fills


class LiveBrokerTests(unittest.TestCase):
    def setUp(self):
        self.px = FakePX()
        self.b = bot.LiveBroker(self.px, 9, {"id": "CON"})

    def test_entry_carries_linked_brackets(self):
        self.px.pos = [{"contractId": "CON", "type": 2, "size": 12, "averagePrice": 83.75}]
        with mock.patch("time.sleep"):
            fill = self.b.enter("short", 12, 84, "ls-1", 80, 250)
        self.assertEqual(fill, 83.75)
        o = self.px.orders[0]
        self.assertEqual((o["type"], o["side"], o["size"], o["stop_ticks"], o["target_ticks"]), (2, 1, 12, 80, 250))

    def test_move_stop_modifies_the_bracket_stop(self):
        self.px.open = [{"id": 5, "contractId": "CON", "type": 1}, {"id": 6, "contractId": "CON", "type": 4}]
        self.b.move_stop(62.0)
        self.assertEqual(self.px.modified, [(6, 62.0)])

    def test_trade_result_waits_for_the_closing_fill(self):
        t = {"opened": 1000.0, "side": "short", "size": 12, "entry": 83.75, "stop": 103.75, "gone_at": 2000.0}
        self.px.fills = [{"contractId": "CON", "profitAndLoss": None, "fees": 4.44}]
        self.assertIsNone(self.b.trade_result(t, 90.0, 2010.0))            # not listed yet: wait
        est = self.b.trade_result(t, 90.0, 2031.0)                           # 30 s later: estimate
        self.assertEqual(est[2], "estimated")
        self.assertAlmostEqual(est[1], (83.75 - 90.0) * 24 - 0.74 * 12, places=2)
        self.px.fills.append({"contractId": "CON", "profitAndLoss": -500.0, "fees": 4.44})
        self.assertEqual(self.b.trade_result(t, 90.0, 2040.0)[1:], (-508.88, "exchange"))

    def test_day_pnl_counts_every_contract(self):
        self.px.fills = [{"contractId": "CON", "profitAndLoss": 600.0, "fees": 4.44},
                         {"contractId": "OTHER", "profitAndLoss": 100.0, "fees": 1.0},
                         {"contractId": "CON", "profitAndLoss": 50.0, "fees": 1.0, "voided": True}]
        self.assertAlmostEqual(self.b.day_pnl(dt.datetime.now(dt.timezone.utc)), 694.56)


class FakeClient:
    """Enough of the REST client to drive Bot.step()."""

    def __init__(self, bars):
        self.all = bars
        self.visible = 0

    def bars(self, cid, start, end, limit=30, live=False):
        return self.all[:self.visible]

    def _ensure_token(self):
        pass


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(config, "LOG_FILE", os.path.join(self.tmp.name, "log.jsonl")),
                        mock.patch.object(config, "STATE_FILE", os.path.join(self.tmp.name, "state.json")),
                        mock.patch("builtins.print")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def events(self):
        with open(config.LOG_FILE) as f:
            return [json.loads(line) for line in f]

    def make_bot(self, bars, broker, mode="paper"):
        client = FakeClient(bars)
        desk = Desk(os.path.join(self.tmp.name, "decisions.jsonl"), lambda *a, **k: None)
        b = bot.Bot(client, {"id": 9, "name": "test"}, {"id": "CON", "name": "MNQZ26"}, broker, mode, desk=desk)
        b.aggression = lambda now: (-0.5, "test")
        return b, client

    def feed(self, b, client, bars, upto=None):
        for i in range(1, (upto or len(bars)) + 1):
            client.visible = i
            b.step(bars[i - 1].t + 61)

    def trail_bars(self):
        start = ny(5, 20)
        bars = make_bars(SHORT_SETUP, start)
        # after the short at 84: drop to 50 (the peak), then bounce: the 65% trail exits it
        return bars + [Bar(start + 60 * (16 + i), *r, v=100) for i, r in enumerate(
            [(84, 84.5, 70, 71), (71, 72, 50, 52), (52, 70, 52, 69)])]

    def test_paper_trade_opens_trails_and_closes(self):
        bars = self.trail_bars()
        b, client = self.make_bot(bars, bot.PaperBroker(2.0, 0.74, 0.25))
        self.feed(b, client, bars)
        ev = [e["event"] for e in self.events()]
        self.assertIn("trade_open", ev)
        opened = [e for e in self.events() if e["event"] == "trade_open"][0]
        self.assertEqual((opened["entry"], opened["stop"]), (83.75, 103.75))   # 1 tick slippage, 20-pt stop
        self.assertIn("stop_moved", ev)
        closed = [e for e in self.events() if e["event"] == "trade_closed"][0]
        # peak 50 -> 33.75 pts in favour; 65% kept -> stop 61.81 -> 62.0 (tick, toward safety)
        self.assertAlmostEqual(closed["pnl"], (83.75 - 62.0) * 24 - 0.74 * 12, places=2)
        self.assertIsNone(b.trade)

    def test_entry_blocked_outside_window(self):
        bars = make_bars(SHORT_SETUP, ny(6, 10))           # 10am: not the Asia session
        b, client = self.make_bot(bars, bot.PaperBroker(2.0, 0.74, 0.25))
        self.feed(b, client, bars)
        blocked = [e for e in self.events() if e["event"] == "entry_blocked"]
        self.assertEqual(blocked[0]["reason"], "outside the entry window")
        self.assertIsNone(b.trade)

    def test_confirm_mode_waits_for_accept(self):
        bars = make_bars(SHORT_SETUP, ny(5, 20))
        broker = bot.PaperBroker(2.0, 0.74, 0.25)
        b, client = self.make_bot(bars, broker, mode="confirm")
        b.live = False                                     # paper fills, confirm flow
        self.feed(b, client, bars)
        self.assertIsNone(b.trade)                         # nothing without an answer
        sid, sig = b.awaiting
        self.assertTrue(b.desk.decide(sid, True))
        b.step(bars[-1].t + 65)
        self.assertEqual(b.trade["side"], "short")

    def test_confirm_mode_rejected_and_expired(self):
        bars = make_bars(SHORT_SETUP, ny(5, 20))
        b, client = self.make_bot(bars, bot.PaperBroker(2.0, 0.74, 0.25), mode="confirm")
        b.live = False
        self.feed(b, client, bars)
        sid, _ = b.awaiting
        b.desk.decide(sid, False)
        b.step(bars[-1].t + 65)
        self.assertIsNone(b.trade)
        self.assertIn("signal_reject", [e["event"] for e in self.events()])
        with open(b.desk.file) as f:
            kinds = [json.loads(l)["kind"] for l in f]
        self.assertEqual(kinds, ["signal", "answer"])

    def test_bracket_mode_rejection_halts_trading(self):
        px = FakePX()
        px.reject = "Brackets cannot be used with Position Brackets. You must enable Auto OCO Brackets."
        bars = make_bars(SHORT_SETUP, ny(5, 20))
        b, client = self.make_bot(bars, bot.LiveBroker(px, 9, {"id": "CON"}), mode="auto")
        b.c = client
        self.feed(b, client, bars)
        self.assertIn("Auto OCO", b.halted)
        self.assertIsNone(b.trade)

    def test_live_manage_flattens_mismatch_and_missing_stop(self):
        px = FakePX()
        b, client = self.make_bot([], bot.LiveBroker(px, 9, {"id": "CON"}), mode="auto")
        now = ny(5, 21)
        b.last_bar_t = now - 60
        b.trade = {"side": "short", "size": 12, "entry": 83.75, "stop": 103.75, "initial_stop": 103.75,
                   "target": 21.25, "peak": 83.75, "opened": now - 60}
        px.pos = [{"contractId": "CON", "type": 1, "size": 12, "averagePrice": 83.75}]   # long: mismatch
        b.step(now)
        self.assertEqual(px.closed, ["CON"])
        self.assertIsNone(b.trade)
        b.trade = {"side": "short", "size": 12, "entry": 83.75, "stop": 103.75, "initial_stop": 103.75,
                   "target": 21.25, "peak": 83.75, "opened": now - 60}
        px.pos = [{"contractId": "CON", "type": 2, "size": 12, "averagePrice": 83.75}]
        px.open = []                                                                  # no working stop
        b.step(now + 3)
        self.assertEqual(px.closed, ["CON", "CON"])
        self.assertIn("no working stop", " ".join(e.get("message", "") for e in self.events()))

    def test_unknown_position_is_left_alone(self):
        px = FakePX()
        bars = make_bars(SHORT_SETUP, ny(5, 20))
        b, client = self.make_bot(bars, bot.LiveBroker(px, 9, {"id": "CON"}), mode="auto")
        px.pos = [{"contractId": "CON", "type": 1, "size": 3, "averagePrice": 90.0}]
        self.feed(b, client, bars)
        self.assertEqual(px.orders, [])
        self.assertEqual(px.closed, [])
        self.assertIn("unknown_position", [e["event"] for e in self.events()])

    def test_open_profit_hits_day_cap(self):
        bars = make_bars(SHORT_SETUP, ny(5, 20))
        broker = bot.PaperBroker(2.0, 0.74, 0.25)
        b, client = self.make_bot(bars, broker)
        b.guard.pnl = 1200.0                               # already +$1,200 today (a paper day)
        b.guard.day = trading_day(ny(5, 20))
        self.feed(b, client, bars)
        self.assertIsNotNone(b.trade)
        b.last_close = 70.0                                # +13.75 pts * 24 = $330 open -> $1,530 day
        b.step(bars[-1].t + 65)
        self.assertIn("consistency", " ".join(e.get("reason", "") for e in self.events()))
        self.assertIsNone(broker.pos)


class PersistenceTests(unittest.TestCase):
    def test_one_loss_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            g = DayGuard(params(), path)
            g.record(ny(5, 20), -500)
            g2 = DayGuard(params(), path)                  # the bot restarted
            self.assertFalse(g2.can_enter(ny(5, 21))[0])
            self.assertTrue(g2.can_enter(ny(6, 20))[0])    # a new trading day

    def test_optional_one_trade_per_day(self):
        self.assertIsNone(config.DAILY_MAX_TRADES)          # off by default
        g = DayGuard(params(DAILY_MAX_TRADES=1))
        g.record(ny(5, 20), 900.0)                         # a win
        ok, why = g.can_enter(ny(5, 21))
        self.assertFalse(ok)
        self.assertIn("one trade per day", why)
        self.assertTrue(g.can_enter(ny(6, 20))[0])

    def test_live_record_does_not_double_count(self):
        g = DayGuard(params())
        g.set_day_pnl(ny(5, 20), 600.0)
        g.record(ny(5, 20), 600.0, add_pnl=False)
        self.assertEqual(g.pnl, 600.0)


class DeskAndRecorderTests(unittest.TestCase):
    def test_desk_answers_marks_and_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            desk = Desk(os.path.join(d, "dec.jsonl"), lambda *a, **k: None)
            sig = {"side": "long", "entry": 1.0, "stop": 0.5, "target": 2.0}
            sid = desk.ask(sig, "confirm", 30, {"price": 1.0})
            self.assertIsNone(desk.answer(sid))
            self.assertEqual(len(desk.view()["pending"]), 1)
            desk.decide(sid, True)
            self.assertEqual(desk.answer(sid), "accept")
            sid2 = desk.ask(sig, "confirm", 0, {})
            time.sleep(0.01)
            self.assertEqual(desk.answer(sid2), "expired")
            desk.update(price=1.5)
            desk.mark("short", "absorption at the London low")
            with open(desk.file) as f:
                recs = [json.loads(l) for l in f]
            self.assertEqual([r["kind"] for r in recs], ["signal", "answer", "signal", "answer", "my_setup"])
            self.assertEqual(recs[-1]["snapshot"]["price"], 1.5)

    def test_recorder_writes_tape_and_book(self):
        with tempfile.TemporaryDirectory() as d:
            rec = Recorder(d)
            s = MarketStream("wss://x", lambda: "t", "CON", Aggression(15), lambda *a, **k: None, rec)
            s.handle("GatewayTrade", ["CON", [{"type": 1, "volume": 3, "price": 100.25, "timestamp": "x"}]])
            s.handle("GatewayDepth", ["CON", {"type": 2, "price": 100.0, "volume": 40}])
            s.handle("GatewayQuote", ["CON", {"lastPrice": 100.25, "bestBid": 100.0, "bestAsk": 100.25}])
            rec.close()
            files = os.listdir(d)
            with gzip.open(os.path.join(d, files[0]), "rt") as f:
                rows = [json.loads(l) for l in f]
            self.assertEqual([r[0] for r in rows], ["T", "D", "Q"])
            self.assertEqual(rows[0][2:5], [100.25, 3, 1])


class BacktestTests(unittest.TestCase):
    def test_simulated_target(self):
        start = ny(5, 20)
        bars = make_bars(SHORT_SETUP, start) + [Bar(start + 60 * 16, 84, 84.5, 20, 21, v=100)]
        with mock.patch("backtest.Aggression.ratio", return_value=-0.5):
            trades, _ = backtest.simulate(bars, params(), 0.25, 2.0)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["reason"], "target")
        self.assertAlmostEqual(trades[0]["pnl"], (83.75 - 21.5) * 24 - 0.74 * 12, places=2)   # short fills 1 tick lower



class AsiaSweepTests(unittest.TestCase):
    def range_bars(self, day=5):
        # Monday 5 Oct 2026, 19:00-19:59 NY: a 60-point range 30000-30060
        return [Bar(ny(day, 19, m), 30030, 30060 if m == 10 else 30040, 30000 if m == 20 else 30020, 30030, v=100)
                for m in range(60)]

    def feed_session(self, extra, p=None, live=True):
        from strategy import make_strategy
        s = make_strategy(p or params(STRATEGY="asia_sweep"), 0.25, 2.0)
        out = None
        for b in self.range_bars() + extra:
            out = s.on_bar(b, 0.0, live=live) or out
        return s, out

    def test_long_fakeout_below_the_range(self):
        _, sig = self.feed_session([Bar(ny(5, 20, 15), 30010, 30012, 29990, 30005, v=100)])
        self.assertEqual((sig["strategy"], sig["side"], sig["entry"]), ("asia_sweep", "long", 30005))
        self.assertEqual(sig["stop"], 29975)                    # wick - 6 = 29984, but 30 pts minimum
        self.assertEqual(sig["size"], 8)                        # 500 // (30 * 2 + 0.74)
        self.assertEqual(sig["target"], 30005 + 1500 / 16)      # 3.33R = 99.9 pts, capped at $1,500
        self.assertEqual(sig["exit_by"], ny(6, 3, 0))

    def test_trend_session_and_one_per_session(self):
        _, sig = self.feed_session([Bar(ny(5, 20, 5), 30060, 30130, 30060, 30125, v=100),
                           Bar(ny(5, 20, 6), 30125, 30126, 29990, 30005, v=100)])
        self.assertIsNone(sig)
        s, sig = self.feed_session([Bar(ny(5, 20, 15), 30010, 30012, 29990, 30005, v=100),
                           Bar(ny(5, 20, 30), 30050, 30075, 30045, 30055, v=100)])
        self.assertEqual(sig["side"], "long")                   # the later short sweep is ignored
        self.assertTrue(s.done)

    def test_no_late_entries_and_warm_up(self):
        _, sig = self.feed_session([Bar(ny(6, 1, 5), 30010, 30012, 29990, 30005, v=100)])
        self.assertIsNone(sig)                                  # 1:05am: after the entry window
        s, sig = self.feed_session([Bar(ny(5, 20, 15), 30010, 30012, 29990, 30005, v=100)], live=False)
        self.assertIsNone(sig)
        self.assertIn("before the bot started", s.note)
        s.on_bar(Bar(ny(6, 18, 10), 30010, 30012, 30000, 30005, v=100), 0.0, live=False)   # next evening, pre-session
        self.assertIn("waiting for the next Asia session", s.note)


def _asia_trail_test(self):
    """With ASIA_TRAIL on, the Asia sweep trade moves to breakeven and trails."""
    with mock.patch.object(config, "STRATEGY", "asia_sweep"), mock.patch.object(config, "ASIA_TRAIL", True):
        bars = AsiaSweepTests.range_bars(self) + [Bar(ny(5, 20, 15), 30010, 30012, 29990, 30005, v=100)]
        broker = bot.PaperBroker(2.0, 0.74, 0.25)
        b, client = self.make_bot(bars, broker)
        self.feed(b, client, bars)
        t = b.trade
        self.assertTrue(t["trail"])
        # 8 MNQ = $16/pt: +$785 needs ~49 pts; at +60 pts the stop keeps 65% of it
        b.last_bar_t = ny(5, 20, 20)
        b.last_close = t["entry"] + 60
        b.step(ny(5, 20, 21))
        self.assertEqual(b.trade["stop"], t["entry"] + 39.0)


def _asia_time_exit_test(self):
    with mock.patch.object(config, "STRATEGY", "asia_sweep"), mock.patch.object(config, "ASIA_TRAIL", False):
        bars = AsiaSweepTests.range_bars(self) + [Bar(ny(5, 20, 15), 30010, 30012, 29990, 30005, v=100)]
        broker = bot.PaperBroker(2.0, 0.74, 0.25)
        b, client = self.make_bot(bars, broker)
        self.feed(b, client, bars)
        self.assertEqual(b.trade["side"], "long")
        self.assertFalse(b.trade["trail"])
        b.last_bar_t = ny(6, 2, 58)
        b.last_close = 30060.0                              # +55 pts: the trail would have moved the stop
        b.step(ny(6, 2, 59))
        self.assertEqual(b.trade["stop"], b.trade["initial_stop"])
        b.last_bar_t = ny(6, 2, 59)
        b.step(ny(6, 3, 0))
        self.assertIn("time exit", " ".join(e.get("reason", "") for e in self.events()))
        self.assertIsNone(broker.pos)


BotTests.test_paper_asia_sweep_time_exit_without_trail = _asia_time_exit_test
BotTests.test_paper_asia_sweep_trails = _asia_trail_test


if __name__ == "__main__":
    unittest.main()
