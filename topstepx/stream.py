"""ProjectX market hub (SignalR over WebSocket): live quotes, the tape and the order book.

The tape feeds the aggression meter (every trade is tagged buy or sell). The order book gives the
big resting orders ("walls") that targets front-run. If the connection drops, the bot falls back
to aggression from 1-minute bars until it reconnects.
Needs:  pip install websocket-client
"""
import datetime as dt
import gzip
import json
import os
import statistics
import threading
import time

try:
    import websocket  # websocket-client
except ImportError:   # the bot still runs, on bar-based aggression
    websocket = None

RS = "\x1e"                          # SignalR record separator
DOM_ASK, DOM_BID, DOM_RESET = 1, 2, 6
TRADE_BUY, TRADE_SELL = 0, 1


class Recorder:
    """Appends every quote, trade and order-book update to recordings/YYYY-MM-DD.jsonl.gz (UTC date).

    Lines are compact JSON arrays:
      ["Q", ts, last, bid, ask]         quote
      ["T", ts, price, volume, side]    trade: side 0 = buyer aggressor, 1 = seller aggressor
      ["D", ts, type, price, volume]    order book: type 1 ask, 2 bid, 6 reset (ProjectX DomType)
    ts is the local receive time; the exchange timestamp is kept as the last element when sent.
    """

    def __init__(self, folder):
        self.folder = folder
        self.day = None
        self.f = None
        self.lines = 0

    def write(self, row):
        day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        if day != self.day:
            self.close()
            os.makedirs(self.folder, exist_ok=True)
            self.f = gzip.open(os.path.join(self.folder, f"{day}.jsonl.gz"), "at")
            self.day = day
        self.f.write(json.dumps(row, separators=(",", ":")) + "\n")
        self.lines += 1
        if self.lines % 500 == 0:
            self.f.flush()

    def close(self):
        if self.f:
            self.f.close()
            self.f = None


class MarketStream:
    def __init__(self, hub_url, token_fn, contract_id, aggression, log, recorder=None):
        self.url = hub_url
        self.token_fn = token_fn          # returns a fresh token (refreshes as needed)
        self.contract_id = contract_id
        self.aggression = aggression
        self.log = log
        self.recorder = recorder
        self.last_price = None
        self.last_msg = 0.0
        self.asks, self.bids = {}, {}
        self.lock = threading.Lock()
        self.ws = None
        self.running = False

    @property
    def available(self):
        return websocket is not None

    def healthy(self, max_age=30):
        return time.time() - self.last_msg < max_age

    def start(self):
        if not self.available:
            self.log("stream_unavailable", reason="pip install websocket-client for the live tape and order book")
            return
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._ping, daemon=True).start()

    def stop(self):
        self.running = False
        if self.ws:
            self.ws.close()
        if self.recorder:
            self.recorder.close()

    def _run(self):
        delay = 2
        while self.running:
            try:
                self.ws = websocket.WebSocketApp(f"{self.url}?access_token={self.token_fn()}",
                                                 on_open=self._on_open, on_message=self._on_message,
                                                 on_error=lambda ws, e: self.log("stream_error", error=str(e)))
                # websocket-level pings detect a half-open connection so it reconnects
                self.ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:  # keep reconnecting whatever happens
                self.log("stream_error", error=str(e))
            if self.running:
                time.sleep(delay)
                delay = min(60, delay * 2)
            if self.healthy():
                delay = 2

    def _ping(self):
        while self.running:
            time.sleep(10)
            try:
                if self.ws and self.ws.sock and self.ws.sock.connected:
                    self.ws.send(json.dumps({"type": 6}) + RS)
            except Exception:
                pass

    def _send(self, target, *args):
        self.ws.send(json.dumps({"type": 1, "target": target, "arguments": list(args)}) + RS)

    def _on_open(self, ws):
        ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
        with self.lock:
            self.asks.clear()
            self.bids.clear()
        for target in ("SubscribeContractQuotes", "SubscribeContractTrades", "SubscribeContractMarketDepth"):
            self._send(target, self.contract_id)
        self.log("stream_connected", contract=self.contract_id)

    def _on_message(self, ws, message):
        self.last_msg = time.time()
        for part in message.split(RS):
            if not part:
                continue
            try:
                msg = json.loads(part)
            except ValueError:
                continue
            if msg.get("type") == 1:
                self.handle(msg.get("target"), msg.get("arguments") or [])
            elif msg.get("type") == 7:
                self.log("stream_closed", error=msg.get("error"))

    def handle(self, target, args):
        """Dispatch one hub invocation: arguments are [contractId, payload or list of payloads]."""
        if len(args) < 2:
            return
        items = args[1] if isinstance(args[1], list) else [args[1]]
        now = time.time()
        rec = self.recorder.write if self.recorder else None
        for d in items:
            if not isinstance(d, dict):
                continue
            if target == "GatewayQuote":
                if d.get("lastPrice") is not None:
                    self.last_price = float(d["lastPrice"])
                if rec:
                    rec(["Q", round(now, 3), d.get("lastPrice"), d.get("bestBid"), d.get("bestAsk")])
            elif target == "GatewayTrade" and d.get("volume"):
                if d.get("type") in (TRADE_BUY, TRADE_SELL):
                    self.aggression.add_trade(now, float(d["volume"]), d["type"] == TRADE_BUY)
                if d.get("price") is not None:
                    self.last_price = float(d["price"])
                if rec:
                    rec(["T", round(now, 3), d.get("price"), d.get("volume"), d.get("type"), d.get("timestamp")])
            elif target == "GatewayDepth":
                self._depth(d)
                if rec:
                    rec(["D", round(now, 3), d.get("type"), d.get("price"), d.get("volume"), d.get("timestamp")])

    def _depth(self, d):
        kind, price, vol = d.get("type"), d.get("price"), d.get("volume") or 0
        with self.lock:
            if kind == DOM_RESET:
                self.asks.clear()
                self.bids.clear()
                return
            book = self.asks if kind == DOM_ASK else self.bids if kind == DOM_BID else None
            if book is None or price is None:
                return
            if vol > 0:
                book[float(price)] = float(vol)
            else:
                book.pop(float(price), None)

    def top_walls(self, min_size, mult, price, n=4):
        """Big resting orders nearest to price, with sizes: {"above": [(price, size)], "below": [...]}."""
        with self.lock:
            asks, bids = dict(self.asks), dict(self.bids)
        out = {}
        for key, book, above in (("above", asks, True), ("below", bids, False)):
            if len(book) < 5 or price is None:
                out[key] = []
                continue
            floor = max(min_size, mult * statistics.median(book.values()))
            big = [(p, v) for p, v in book.items() if v >= floor and ((p > price) if above else (p < price))]
            out[key] = sorted(big, key=lambda x: abs(x[0] - price))[:n]
        return out

    def walls(self, side, min_size, mult):
        """Prices of big resting orders a trade is heading into: bids below for shorts, asks above for longs."""
        with self.lock:
            book = dict(self.bids if side == "short" else self.asks)
        if len(book) < 5:
            return []
        floor = max(min_size, mult * statistics.median(book.values()))
        return [p for p, v in book.items() if v >= floor]
