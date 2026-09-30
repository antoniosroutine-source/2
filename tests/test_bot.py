import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: E402
import config  # noqa: E402
import mfp_client  # noqa: E402
from mfp_client import MFPClient, MFPError  # noqa: E402
from risk import RiskManager  # noqa: E402
from strategy import NY, Candle, CandleBuilder, SilverBullet, window_key  # noqa: E402


def ny_ts(hour, minute=0, day=1):
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=NY).timestamp()


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ClientTests(unittest.TestCase):
    def test_unwraps_data_and_reads_equity(self):
        c = MFPClient("fp_test_x", "https://x/v1")
        body = json.dumps({"data": {"risk": {"equity": "25010.5"}}}).encode()
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(body)) as m:
            self.assertEqual(c.get_equity("acc 1"), 25010.5)
        req = m.call_args[0][0]
        self.assertEqual(req.full_url, "https://x/v1/accounts/acc%201")
        self.assertEqual(req.get_header("Authorization"), "Bearer fp_test_x")

    def test_post_has_idempotency_key_reused_on_retry_and_numeric_fields(self):
        c = MFPClient("fp_test_x", "https://x/v1")
        err = urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO(b"busy"))
        ok = FakeResp(json.dumps({"data": {"id": "o1"}}).encode())
        with mock.patch("urllib.request.urlopen", side_effect=[err, ok]) as m, mock.patch("time.sleep"):
            out = c.place_market_order("a", "binance|BTCUSDT", "buy", 0.01, 60000, 5, "isolated")
        self.assertEqual(out, {"id": "o1"})
        keys = {call[0][0].get_header("Idempotency-key") for call in m.call_args_list}
        self.assertEqual(len(keys), 1)
        self.assertIsNotNone(next(iter(keys)))
        sent = json.loads(m.call_args[0][0].data)
        self.assertIsInstance(sent["size"], float)
        self.assertIsInstance(sent["expected_price"], float)

    def test_quote_encodes_pipe_market(self):
        c = MFPClient("fp_test_x", "https://x/v1")
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(b'{"data":{"mid":60000}}')) as m:
            self.assertEqual(c.get_mid("binance|BTCUSDT"), 60000.0)
        self.assertIn("/markets/binance%7CBTCUSDT/quote?", m.call_args[0][0].full_url)

    def test_client_error_not_retried(self):
        c = MFPClient("fp_test_x", "https://x/v1")
        err = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"bad size"))
        with mock.patch("urllib.request.urlopen", side_effect=[err]):
            with self.assertRaises(MFPError) as ctx:
                c.get_order("o1")
        self.assertEqual(ctx.exception.status, 400)


class RiskTests(unittest.TestCase):
    def make(self):
        return RiskManager(25_000, 0.02, 0.02, 0.75, 0.0025, 2.0, 0.001)

    def test_limits_for_prime_account(self):
        r = self.make()
        self.assertAlmostEqual(r.drawdown_limit, 375)
        self.assertTrue(r.check_risk(25_000)[0])
        self.assertFalse(r.check_risk(24_620)[0])          # $380 down
        self.assertFalse(r.check_risk(24_700, projected_loss=80)[0])  # 300 + 80 >= 375

    def test_daily_roll(self):
        r = self.make()
        d1 = dt.datetime(2026, 10, 1, 12, tzinfo=dt.timezone.utc)
        r.check_risk(25_000, now=d1)
        self.assertEqual(r.snapshot(24_900, now=d1)["daily_loss"], 100)
        self.assertEqual(r.snapshot(24_900, now=d1 + dt.timedelta(days=1))["daily_loss"], 0)

    def test_position_size_by_risk_and_cap(self):
        r = self.make()
        # $62.50 risk / $200 stop distance = 0.3125 -> 0.312
        self.assertEqual(r.position_size(25_000, 60_000, 59_800), 0.312)
        # tiny stop would be huge; capped at 2x equity notional = 50k / 60k = 0.833
        self.assertEqual(r.position_size(25_000, 60_000, 59_999), 0.833)


class StrategyTests(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(window_key(ny_ts(10, 30)), ("2026-10-01", 10))
        self.assertEqual(window_key(ny_ts(3, 0)), ("2026-10-01", 3))
        self.assertIsNone(window_key(ny_ts(11, 0)))
        self.assertIsNone(window_key(ny_ts(9, 59)))

    def test_candle_builder(self):
        b = CandleBuilder(60)
        self.assertIsNone(b.update(0, 10))
        self.assertIsNone(b.update(30, 12))
        self.assertIsNone(b.update(55, 9))
        c = b.update(60, 11)
        self.assertEqual((c.open, c.high, c.low, c.close), (10, 12, 9, 9))

    def feed_range(self, s, start_ts, n, base=100.0):
        for i in range(n):
            s.on_candle(Candle(start_ts + 60 * i, base, base + 1, base - 1, base))

    def test_bearish_silver_bullet(self):
        s = SilverBullet(lookback=10, rr=2.0, stop_buffer_pct=0, min_stop_pct=0.0, setup_expiry_bars=15)
        t = ny_ts(9, 45)
        self.feed_range(s, t, 12)                       # range 99-101 before the window
        w = ny_ts(10, 0)
        s.on_candle(Candle(w, 100, 103, 100, 102))      # sweeps buy-side 101 (extreme 103)
        self.assertEqual(s.sweep["dir"], "short")
        s.on_candle(Candle(w + 60, 102, 102.5, 98, 98.5))   # displacement down
        s.on_candle(Candle(w + 120, 98.5, 99, 97, 97.5))    # leaves gap 99..100 (a.low=100 > c.high=99)
        self.assertEqual(s.setup["side"], "short")
        self.assertEqual((s.setup["zone_low"], s.setup["zone_high"]), (99, 100))
        self.assertIsNone(s.on_price(w + 150, 98.0))        # not retraced yet
        sig = s.on_price(w + 170, 99.5)
        self.assertEqual(sig["side"], "short")
        self.assertEqual(sig["stop"], 103)
        self.assertAlmostEqual(sig["target"], 99.5 - 2 * 3.5)
        self.assertIsNone(s.on_price(w + 180, 99.5))        # one trade per window

    def test_bullish_silver_bullet(self):
        s = SilverBullet(lookback=10, rr=2.0, stop_buffer_pct=0, min_stop_pct=0.0)
        self.feed_range(s, ny_ts(13, 45), 12)
        w = ny_ts(14, 0)
        s.on_candle(Candle(w, 100, 100, 97, 98))            # sweeps sell-side 99 (extreme 97)
        s.on_candle(Candle(w + 60, 98, 101.5, 97.5, 101.5))  # displacement up (breaks 101 too)
        s.on_candle(Candle(w + 120, 101.5, 103, 101, 102.5)) # gap 100..101
        self.assertEqual((s.setup["side"], s.setup["zone_low"], s.setup["zone_high"]), ("long", 100, 101))
        sig = s.on_price(w + 150, 100.5)
        self.assertEqual((sig["side"], sig["stop"], sig["target"]), ("long", 97, 100.5 + 2 * 3.5))

    def test_no_signal_outside_window(self):
        s = SilverBullet(lookback=10, stop_buffer_pct=0, min_stop_pct=0.0)
        t = ny_ts(11, 0)
        self.feed_range(s, t, 12)
        s.on_candle(Candle(t + 720, 100, 103, 100, 102))
        self.assertIsNone(s.sweep)

    def test_setup_invalidated_by_stop_before_entry(self):
        s = SilverBullet(lookback=10, stop_buffer_pct=0, min_stop_pct=0.0)
        s.setup = {"side": "long", "zone_low": 100, "zone_high": 101, "stop": 98, "bar": 0}
        self.assertIsNone(s.on_price(ny_ts(10, 5), 97.5))
        self.assertIsNone(s.setup)


class FakeClient:
    def __init__(self, fail_exits=0):
        self.positions = []
        self.fail_exits = fail_exits
        self.exit_calls = []
        self.closed = []
        self.orders = []

    def place_market_order(self, *args):
        self.orders.append(args)
        return {"id": "o1", "status": "pending"}

    def wait_for_order(self, order_id):
        side = self.orders[-1][2]
        self.positions = [{"id": "p1", "market_id": config.MARKET, "size": self.orders[-1][3],
                           "side": side, "entry_price": 60000.0, "status": "open"}]
        return {"id": order_id, "status": "filled"}

    def list_positions(self, account_id):
        return self.positions

    def set_exit_orders(self, pos_id, size, tp, sl):
        self.exit_calls.append((pos_id, size, tp, sl))
        if len(self.exit_calls) <= self.fail_exits:
            raise MFPError(409, "position not ready")

    def close_position(self, pos_id):
        self.closed.append(pos_id)
        self.positions = []


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_patch = mock.patch.object(config, "LOG_FILE", os.path.join(self.tmp.name, "log.jsonl"))
        self.log_patch.start()
        self.print_patch = mock.patch("builtins.print")
        self.print_patch.start()

    def tearDown(self):
        self.log_patch.stop()
        self.print_patch.stop()
        self.tmp.cleanup()

    def bot(self, client):
        risk = RiskManager(25_000, 0.02, 0.02, 0.75, 0.0025, 2.0, 0.001)
        return agent.Bot(client, "acc", risk, SilverBullet(), sleep=lambda s: None)

    def events(self):
        with open(config.LOG_FILE) as f:
            return [json.loads(line)["event"] for line in f]

    def test_short_trade_sets_tp_sl_after_retry(self):
        c = FakeClient(fail_exits=2)
        b = self.bot(c)
        b.execute({"side": "short", "entry": 60000.0, "stop": 60200.0, "target": 59600.0}, 25_000)
        self.assertEqual(c.orders[0][2], "sell")
        self.assertEqual(c.orders[0][3], 0.312)
        self.assertEqual(len(c.exit_calls), 3)
        _, size, tp, sl = c.exit_calls[-1]
        self.assertEqual((size, tp, sl), (0.312, 59600.0, 60200.0))
        self.assertIn("trade_open", self.events())

    def test_unprotected_position_is_closed(self):
        c = FakeClient(fail_exits=99)
        b = self.bot(c)
        b.execute({"side": "long", "entry": 60000.0, "stop": 59800.0, "target": 60400.0}, 25_000)
        self.assertEqual(len(c.exit_calls), 5)
        self.assertEqual(c.closed, ["p1"])
        self.assertIsNone(b.open_trade)

    def test_risk_block_places_no_order(self):
        c = FakeClient()
        b = self.bot(c)
        b.execute({"side": "long", "entry": 60000.0, "stop": 59800.0, "target": 60400.0}, 24_650)
        self.assertEqual(c.orders, [])
        self.assertIn("risk_block", self.events())

    def test_step_logs_trade_closed_when_position_gone(self):
        c = FakeClient()
        c.get_mid = lambda m, size: 60000.0
        c.get_equity = lambda a: 25_050.0
        b = self.bot(c)
        b.open_trade = {"position_id": "p1", "equity_at_entry": 25_000.0, "side": "long"}
        b.step(now=ny_ts(12, 0))
        self.assertIsNone(b.open_trade)
        with open(config.LOG_FILE) as f:
            closed = [json.loads(l) for l in f if '"trade_closed"' in l]
        self.assertEqual(closed[0]["pnl"], 50.0)

    def test_step_closes_position_when_limit_hit(self):
        c = FakeClient()
        c.positions = [{"id": "p9", "market_id": config.MARKET, "size": 0.1, "status": "open"}]
        c.get_mid = lambda m, size: 60000.0
        c.get_equity = lambda a: 24_600.0
        self.bot(c).step(now=ny_ts(12, 0))
        self.assertEqual(c.closed, ["p9"])

    def test_key_guard(self):
        with self.assertRaises(SystemExit):
            agent.check_key("fp_live_abc", config.SANDBOX_URL)
        with self.assertRaises(SystemExit):
            agent.check_key("fp_test_abc", config.LIVE_URL)
        with self.assertRaises(SystemExit):
            agent.check_key("", config.SANDBOX_URL)
        agent.check_key("fp_test_abc", config.SANDBOX_URL)


if __name__ == "__main__":
    unittest.main()
