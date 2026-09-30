"""ProjectX market hub (SignalR over WebSocket): live quotes, the tape and the order book.

The tape feeds the aggression meter (every trade is tagged buy or sell). The order book gives the
big resting orders ("walls") that targets front-run. If the connection drops, the bot falls back
to aggression from 1-minute bars until it reconnects.
Needs:  pip install websocket-client
"""
import json
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


class MarketStream:
    def __init__(self, hub_url, token_fn, contract_id, aggression, log):
        self.url = hub_url
        self.token_fn = token_fn          # returns a fresh token (refreshes as needed)
        self.contract_id = contract_id
        self.aggression = aggression
        self.log = log
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

    def _run(self):
        delay = 2
        while self.running:
            try:
                self.ws = websocket.WebSocketApp(f"{self.url}?access_token={self.token_fn()}",
                                                 on_open=self._on_open, on_message=self._on_message,
                                                 on_error=lambda ws, e: self.log("stream_error", error=str(e)))
                self.ws.run_forever()
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
        for d in items:
            if not isinstance(d, dict):
                continue
            if target == "GatewayQuote" and d.get("lastPrice") is not None:
                self.last_price = float(d["lastPrice"])
            elif target == "GatewayTrade" and d.get("volume"):
                if d.get("type") in (TRADE_BUY, TRADE_SELL):
                    self.aggression.add_trade(now, float(d["volume"]), d["type"] == TRADE_BUY)
                if d.get("price") is not None:
                    self.last_price = float(d["price"])
            elif target == "GatewayDepth":
                self._depth(d)

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

    def walls(self, side, min_size, mult):
        """Prices of big resting orders a trade is heading into: bids below for shorts, asks above for longs."""
        with self.lock:
            book = dict(self.bids if side == "short" else self.asks)
        if len(book) < 5:
            return []
        floor = max(min_size, mult * statistics.median(book.values()))
        return [p for p, v in book.items() if v >= floor]
