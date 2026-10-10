"""Wilder RSI and ATR, updated one closed candle at a time (the same math TradingView uses)."""


class RSI:
    def __init__(self, n=14):
        self.n, self.prev, self.gain, self.loss, self.count = n, None, 0.0, 0.0, 0
        self.value = None

    def _step(self, close, gain, loss, count):
        if self.prev is None:
            return gain, loss, count, None
        d = close - self.prev
        up, dn = max(d, 0.0), max(-d, 0.0)
        count += 1
        if count <= self.n:                        # seed with a simple average of the first n moves
            gain += up / self.n
            loss += dn / self.n
            if count < self.n:
                return gain, loss, count, None
        else:
            gain = (gain * (self.n - 1) + up) / self.n
            loss = (loss * (self.n - 1) + dn) / self.n
        return gain, loss, count, (100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))

    def update(self, close):
        """A closed candle: advances the RSI."""
        self.gain, self.loss, self.count, v = self._step(close, self.gain, self.loss, self.count)
        self.prev = close
        if v is not None:
            self.value = v
        return self.value

    def peek(self, price):
        """RSI if the forming candle closed at `price` (live, does not advance)."""
        return self._step(price, self.gain, self.loss, self.count)[3]


class ATR:
    def __init__(self, n=14):
        self.n, self.prev_close, self.value, self.count, self.acc = n, None, None, 0, 0.0

    def update(self, h, l, c):
        tr = h - l if self.prev_close is None else max(h - l, abs(h - self.prev_close), abs(l - self.prev_close))
        self.prev_close = c
        self.count += 1
        if self.count <= self.n:
            self.acc += tr
            if self.count == self.n:
                self.value = self.acc / self.n
        else:
            self.value = (self.value * (self.n - 1) + tr) / self.n
        return self.value
