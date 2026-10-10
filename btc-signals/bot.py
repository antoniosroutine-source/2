"""BTC RSI short-signal bot. Watches BTC, alerts you; it never places trades.

    python bot.py                       5-minute candles (default)
    set BTC_TF=15 & python bot.py       15-minute candles
    set BTC_MODE=turn & python bot.py   wait for RSI to turn back down from overbought

Desk page: http://127.0.0.1:8768/   Signals are logged to signals.jsonl.
"""
import datetime as dt
import json
import threading
import time
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import feed
from signals import SignalEngine


def now_str(ts=None):
    return dt.datetime.fromtimestamp(ts or time.time()).strftime("%a %H:%M:%S")


class Bot:
    def __init__(self, p=config):
        self.p = p
        self.eng = SignalEngine(p)
        self.tf = p.TIMEFRAME_MIN * 60
        self.last_t = 0.0
        self.price = None
        self.live_rsi = None
        self.rsi_hist = deque(maxlen=72)
        self.events = deque(maxlen=60)
        self.active = None            # the latest SHORT idea while it is live
        self.history = deque(maxlen=20)
        self.lock = threading.Lock()
        self.feed_ok = True

    # -- alerts ------------------------------------------------------------------------------
    def alert(self, ev):
        text = ev.get("text") or self._text(ev)
        ev = {**ev, "text": text, "at": time.time()}
        with self.lock:
            self.events.appendleft(ev)
        print(f"[{now_str()}] {text}", flush=True)
        with open(self.p.SIGNAL_LOG, "a") as f:
            f.write(json.dumps(ev) + "\n")
        if self.p.NTFY_TOPIC and ev["kind"] in ("watch", "short", "overbought", "result"):
            threading.Thread(target=self._push, args=(text, ev["kind"] == "short"), daemon=True).start()

    def _text(self, ev):
        if ev["kind"] == "short":
            return (f"SHORT BTC @ {ev['price']:,.2f} | stop {ev['stop']:,.2f} ({ev['risk_pct']:.2f}%) | "
                    f"target {ev['target']:,.2f} | multiplier <= {ev['max_mult']}x | {ev['why']}")
        if ev["kind"] == "rearmed":
            return f"RSI cooled to {ev['rsi']} - watching for the next overbought run"
        return json.dumps(ev)

    def _push(self, text, urgent):
        try:
            req = urllib.request.Request(f"https://ntfy.sh/{self.p.NTFY_TOPIC}", data=text.encode(),
                                         headers={"Title": "BTC RSI signal", "Priority": "high" if urgent else "default"})
            urllib.request.urlopen(req, timeout=10).read()
        except OSError as e:
            print(f"phone alert failed: {e}", flush=True)

    # -- market data -------------------------------------------------------------------------
    def closed(self, cs, now):
        return [c for c in cs if c.t + self.tf <= now]

    def warm_up(self):
        cs = self.closed(feed.candles(self.p.TIMEFRAME_MIN), time.time())
        for c in cs:
            self.eng.on_candle(c)               # history: no alerts
            if self.eng.rsi.value is not None:
                self.rsi_hist.append(round(self.eng.rsi.value, 1))
        self.last_t = cs[-1].t if cs else 0.0
        self.eng.armed = self.eng.rsi.value is None or self.eng.rsi.value < self.p.OVERBOUGHT
        print(f"[{now_str()}] warmed up on {len(cs)} {self.p.TIMEFRAME_MIN}-minute candles; RSI {self.eng.rsi.value:.1f}"
              if self.eng.rsi.value is not None else "warmed up", flush=True)

    def step(self):
        now = time.time()
        try:
            cs = self.closed(feed.candles(self.p.TIMEFRAME_MIN), now)
            self.price = feed.price()
            self.feed_ok = True
        except (OSError, ValueError, KeyError, StopIteration) as e:
            self.feed_ok = False
            print(f"[{now_str()}] price feed error: {e}", flush=True)
            return
        for c in cs:
            if c.t <= self.last_t:
                continue
            self.last_t = c.t
            self._track(c)
            for ev in self.eng.on_candle(c):
                if ev["kind"] == "short":
                    self.active = {**ev, "bars": 0, "status": "live"}
                self.alert(ev)
            if self.eng.rsi.value is not None:
                self.rsi_hist.append(round(self.eng.rsi.value, 1))
        r, ev = self.eng.live(self.price)
        self.live_rsi = r
        if ev:
            self.alert(ev)

    def _track(self, c):
        """Follow the latest SHORT idea: stop, target or expiry."""
        a = self.active
        if not a or a["status"] != "live" or c.t <= a["t"]:
            return
        a["bars"] += 1
        risk = a["stop"] - a["price"]
        res = None
        if c.h >= a["stop"]:
            res, x = "stopped", a["stop"]
        elif c.l <= a["target"]:
            res, x = "target hit", a["target"]
        elif a["bars"] >= a["expires_bars"]:
            res, x = "expired", c.c
        if res:
            a["status"], a["r"] = res, round((a["price"] - x) / risk, 2)
            self.history.appendleft({k: a[k] for k in ("t", "price", "stop", "target", "status", "r", "rsi")})
            self.alert({"kind": "result", "text": f"Short from {a['price']:,.2f}: {res} at {x:,.2f} ({a['r']:+.2f}R)"})

    def view(self):
        with self.lock:
            e = self.eng
            return {"price": self.price, "live_rsi": None if self.live_rsi is None else round(self.live_rsi, 1),
                    "rsi": None if e.rsi.value is None else round(e.rsi.value, 1), "armed": e.armed,
                    "overbought": e.overbought, "tf": self.p.TIMEFRAME_MIN, "mode": self.p.MODE,
                    "levels": {"watch": self.p.WATCH_LEVEL, "ob": self.p.OVERBOUGHT, "rearm": self.p.REARM_BELOW},
                    "rsi_hist": list(self.rsi_hist), "events": list(self.events), "active": self.active,
                    "history": list(self.history), "feed_ok": self.feed_ok, "clock": now_str()}

    def run(self):
        print(f"BTC RSI signal bot: {self.p.TIMEFRAME_MIN}-minute candles, mode {self.p.MODE}. "
              f"Desk page: http://{self.p.UI_HOST}:{self.p.UI_PORT}/", flush=True)
        self.warm_up()
        while True:
            self.step()
            time.sleep(self.p.POLL_SEC)


def serve(bot, host, port):
    from page import PAGE

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body, ctype = (PAGE.encode(), "text/html; charset=utf-8") if self.path == "/" else \
                          (json.dumps(bot.view()).encode(), "application/json") if self.path == "/state" else (b"", "")
            self.send_response(200 if ctype else 404)
            self.send_header("Content-Type", ctype or "text/plain")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    b = Bot()
    serve(b, config.UI_HOST, config.UI_PORT)
    try:
        b.run()
    except KeyboardInterrupt:
        print("stopped")
