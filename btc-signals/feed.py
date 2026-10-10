"""Public BTC prices: Coinbase Exchange (no key, works in the US); Kraken as a fallback."""
import json
import urllib.request

from signals import Candle

UA = {"User-Agent": "btc-rsi-signals/1.0"}


def _get(url, timeout=10):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode())


def candles(tf_min, product="BTC-USD"):
    """Up to 300 most recent candles, oldest first (the last one is usually still forming)."""
    try:
        rows = _get(f"https://api.exchange.coinbase.com/products/{product}/candles?granularity={tf_min * 60}")
        out = [Candle(float(r[0]), float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5])) for r in rows]
    except (OSError, ValueError):
        data = _get(f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval={tf_min}")["result"]
        rows = next(v for k, v in data.items() if k != "last")
        out = [Candle(float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6])) for r in rows]
    return sorted(out, key=lambda c: c.t)


def price(product="BTC-USD"):
    try:
        return float(_get(f"https://api.exchange.coinbase.com/products/{product}/ticker")["price"])
    except (OSError, ValueError, KeyError):
        data = _get("https://api.kraken.com/0/public/Ticker?pair=XBTUSD")["result"]
        return float(next(iter(data.values()))["c"][0])
