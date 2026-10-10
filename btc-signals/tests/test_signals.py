import os, sys, tempfile, types, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from indicators import ATR, RSI
from signals import Candle, SignalEngine


def params(**over):
    p = types.SimpleNamespace(**{k: getattr(config, k) for k in dir(config) if k.isupper()})
    p.MODE = "touch"
    for k, v in over.items():
        setattr(p, k, v)
    return p


def candles(closes, t0=1_700_000_000, tf=300):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Candle(t0 + i * tf, prev, max(prev, c) + 5, min(prev, c) - 5, c))
        prev = c
    return out


class RSITests(unittest.TestCase):
    def test_wilder_rsi_known_values(self):
        r = RSI(14)
        for c in range(100, 116):                     # only gains
            r.update(c)
        self.assertEqual(r.value, 100.0)
        r2 = RSI(14)
        for i in range(60):                           # equal gains and losses
            r2.update(100 + (i % 2))
        self.assertAlmostEqual(r2.value, 50.0, delta=4)

    def test_peek_does_not_advance(self):
        r = RSI(14)
        for i in range(30):
            r.update(100 + (i % 3))
        v = r.value
        r.peek(150)
        self.assertEqual(r.value, v)
        self.assertGreater(r.peek(150), v)

    def test_atr(self):
        a = ATR(3)
        for h, l, c in ((10, 8, 9), (11, 9, 10), (12, 10, 11), (13, 11, 12)):
            a.update(h, l, c)
        self.assertAlmostEqual(a.value, 2.0)


class EngineTests(unittest.TestCase):
    def feed(self, eng, closes):
        ev = []
        for c in candles(closes):
            ev += eng.on_candle(c)
        return ev

    def chop_then_rally(self):
        return [100 + (i % 2) for i in range(40)] + [101 + 3 * i for i in range(1, 12)]

    def test_touch_mode_one_short_per_run_with_levels(self):
        eng = SignalEngine(params())
        ev = self.feed(eng, self.chop_then_rally())
        shorts = [e for e in ev if e["kind"] == "short"]
        self.assertEqual(len(shorts), 1)                  # the rally keeps RSI >= 70 but only one signal
        s = shorts[0]
        self.assertGreaterEqual(s["rsi"], 70)
        self.assertGreater(s["stop"], s["price"])
        self.assertAlmostEqual(s["price"] - s["target"], 1.5 * (s["stop"] - s["price"]), places=1)
        self.assertFalse(eng.armed)

    def test_rearms_after_rsi_cools_below_50(self):
        eng = SignalEngine(params())
        self.feed(eng, self.chop_then_rally())
        ev = self.feed(eng, [134 - 3 * i for i in range(1, 15)])
        self.assertIn("rearmed", [e["kind"] for e in ev])
        self.assertTrue(eng.armed)

    def test_turn_mode_waits_for_rsi_back_below_70(self):
        eng = SignalEngine(params(MODE="turn"))
        ev = self.feed(eng, self.chop_then_rally())
        self.assertIn("overbought", [e["kind"] for e in ev])
        self.assertNotIn("short", [e["kind"] for e in ev])
        ev = self.feed(eng, [131, 126, 121])
        self.assertEqual([e["kind"] for e in ev if e["kind"] == "short"], ["short"])
        self.assertLess([e for e in ev if e["kind"] == "short"][0]["rsi"], 70)

    def test_watch_fires_once_as_live_rsi_nears_overbought(self):
        eng = SignalEngine(params())
        self.feed(eng, [100 + (i % 2) for i in range(40)])
        r, ev = eng.live(101.2)
        self.assertIsNone(ev)
        r, ev = eng.live(110)
        self.assertEqual(ev["kind"], "watch")
        self.assertIsNone(eng.live(111)[1])               # only once per run


class BotTests(unittest.TestCase):
    def test_live_loop_signals_once_and_tracks_the_result(self):
        import bot
        tmp = tempfile.mkdtemp()
        p = params(SIGNAL_LOG=os.path.join(tmp, "s.jsonl"), NTFY_TOPIC=None, TIMEFRAME_MIN=5)
        b = bot.Bot(p)
        closes = [100 + (i % 2) for i in range(40)]
        hist = candles(closes)
        now = hist[-1].t + 300 + 1
        with mock.patch("bot.feed.candles", return_value=hist), mock.patch("bot.time.time", return_value=now), \
             mock.patch("builtins.print"):
            b.warm_up()
        full = candles(closes + [101 + 3 * i for i in range(1, 12)])
        probe = SignalEngine(p)
        cut = next(i for i, c in enumerate(full) if any(e["kind"] == "short" for e in probe.on_candle(c)))
        rally = full[:cut + 1]                             # the feed ends on the signal candle
        last = rally[-1].c
        with mock.patch("bot.feed.candles", return_value=rally), mock.patch("bot.feed.price", return_value=last), \
             mock.patch("bot.time.time", return_value=rally[-1].t + 301), mock.patch("builtins.print"):
            b.step(); b.step()                             # the second poll must not repeat the signal
        shorts = [e for e in b.events if e["kind"] == "short"]
        self.assertEqual(len(shorts), 1)
        self.assertEqual(b.active["status"], "live")
        stop = b.active["stop"]
        spike = rally + [Candle(rally[-1].t + 300, last, stop + 1, last - 1, last)]
        with mock.patch("bot.feed.candles", return_value=spike), mock.patch("bot.feed.price", return_value=last), \
             mock.patch("bot.time.time", return_value=spike[-1].t + 301), mock.patch("builtins.print"):
            b.step()
        self.assertEqual(b.active["status"], "stopped")
        self.assertEqual(b.history[0]["r"], -1.0)
        self.assertTrue(os.path.exists(p.SIGNAL_LOG))


if __name__ == "__main__":
    unittest.main()
