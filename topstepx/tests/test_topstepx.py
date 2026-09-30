import datetime as dt
import io
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest  # noqa: E402
import bot  # noqa: E402
import config  # noqa: E402
from levels import ET, Aggression, SessionTracker, in_window, trading_day  # noqa: E402
from manage import DayGuard, next_stop  # noqa: E402
from projectx import PXError, ProjectX  # noqa: E402
from strategy import Bar, LevelSweep  # noqa: E402
from stream import MarketStream  # noqa: E402


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
        g = DayGuard(params())
        t = ny(5, 20)
        self.assertTrue(g.can_enter(t)[0])
        g.record(t, -30)                                   # a scratch
        self.assertTrue(g.can_enter(t)[0])
        g.record(t, -500)
        self.assertFalse(g.can_enter(t + 60)[0])
        self.assertTrue(g.can_enter(ny(6, 20))[0])         # next trading day

    def test_profit_stop_and_window(self):
        g = DayGuard(params())
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
    def __init__(self):
        self.orders, self.cancelled, self.modified = [], [], []
        self.open = []
        self.fills = []

    def place(self, acct, cid, typ, side, size, limit_price=None, stop_price=None, tag=None):
        self.orders.append((typ, side, size, limit_price, stop_price, tag))
        return len(self.orders)

    def open_orders(self, acct):
        return self.open

    def cancel(self, acct, oid):
        self.cancelled.append(oid)

    def modify(self, acct, oid, stop_price=None, **k):
        self.modified.append((oid, stop_price))

    def trades(self, acct, start):
        return self.fills


class LiveBrokerTests(unittest.TestCase):
    def test_exits_and_foreign_bracket_cancelled(self):
        px = FakePX()
        b = bot.LiveBroker(px, 9, {"id": "CON"})
        px.open = [{"id": 1, "contractId": "CON"}, {"id": 2, "contractId": "CON"}, {"id": 99, "contractId": "CON"}]
        with mock.patch.object(bot, "log"):
            b.set_exits("short", 12, 104.0, 21.5, "ls-1")
        self.assertEqual(px.orders[0][:5], (4, 0, 12, None, 104.0))     # buy stop
        self.assertEqual(px.orders[1][:5], (1, 0, 12, 21.5, None))      # buy limit target
        self.assertEqual(px.cancelled, [99])

    def test_realized_includes_fees(self):
        px = FakePX()
        px.fills = [{"contractId": "CON", "profitAndLoss": None, "fees": 4.44},
                    {"contractId": "CON", "profitAndLoss": 600.0, "fees": 4.44},
                    {"contractId": "OTHER", "profitAndLoss": 100.0, "fees": 1.0},
                    {"contractId": "CON", "profitAndLoss": 50.0, "fees": 1.0, "voided": True}]
        b = bot.LiveBroker(px, 9, {"id": "CON"})
        self.assertAlmostEqual(b._realized(dt.datetime.now(dt.timezone.utc)), 591.12)
        self.assertAlmostEqual(b.day_pnl(dt.datetime.now(dt.timezone.utc)), 690.12)


class FakeClient:
    """Enough of the REST client to drive Bot.step() in paper mode."""

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

    def test_paper_trade_opens_trails_and_closes(self):
        start = ny(5, 20)
        bars = make_bars(SHORT_SETUP, start)
        # after entry at 84: drop to 50 (peak), then bounce: the 65% trail should exit it
        bars += [Bar(start + 60 * (16 + i), *r, v=100) for i, r in enumerate(
            [(84, 84.5, 70, 71), (71, 72, 50, 52), (52, 70, 52, 69)])]
        client = FakeClient(bars)
        broker = bot.PaperBroker(2.0, 0.74)
        b = bot.Bot(client, {"name": "paper"}, {"id": "CON", "name": "MNQZ26"}, broker, live=False)
        b.aggression = lambda now: (-0.5, "test")
        for i in range(1, len(bars) + 1):
            client.visible = i
            b.step(bars[i - 1].t + 61)
        ev = [e["event"] for e in self.events()]
        self.assertIn("trade_open", ev)
        self.assertIn("stop_moved", ev)
        closed = [e for e in self.events() if e["event"] == "trade_closed"][0]
        # peak 50 -> 34 pts in favour; 65% kept -> stop 84 - 22.1 = 61.9 -> 62.0; exit at 62
        self.assertAlmostEqual(closed["pnl"], (84 - 62.0) * 12 * 2 - 0.74 * 12, places=2)
        self.assertIsNone(b.trade)

    def test_entry_blocked_outside_window(self):
        start = ny(6, 10)                                  # 10am: not the Asia session
        bars = make_bars(SHORT_SETUP, start)
        client = FakeClient(bars)
        b = bot.Bot(client, {"name": "paper"}, {"id": "CON", "name": "MNQZ26"},
                    bot.PaperBroker(2.0, 0.74), live=False)
        b.aggression = lambda now: (-0.5, "test")
        for i in range(1, len(bars) + 1):
            client.visible = i
            b.step(bars[i - 1].t + 61)
        blocked = [e for e in self.events() if e["event"] == "entry_blocked"]
        self.assertEqual(blocked[0]["reason"], "outside the entry window")
        self.assertIsNone(b.trade)


class BacktestTests(unittest.TestCase):
    def test_simulated_target(self):
        start = ny(5, 20)
        bars = make_bars(SHORT_SETUP, start) + [Bar(start + 60 * 16, 84, 84.5, 20, 21, v=100)]
        with mock.patch("backtest.Aggression.ratio", return_value=-0.5):
            trades, _ = backtest.simulate(bars, params(), 0.25, 2.0)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["reason"], "target")
        self.assertAlmostEqual(trades[0]["pnl"], (83.75 - 21.5) * 24 - 0.74 * 12, places=2)   # short fills 1 tick lower


if __name__ == "__main__":
    unittest.main()
