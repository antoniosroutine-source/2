"""Thin wrapper around the MyFundedPerps v1 REST API (standard library only).

API quirks handled here:
- every response is wrapped in {"data": ...} -> _unwrap()
- every POST/PUT carries an Idempotency-Key (reused across retries of the same call)
- numeric order fields are sent as numbers, not strings
- account equity lives at account["risk"]["equity"]
- market ids use pipe format ("binance|BTCUSDT") and are URL-encoded in paths
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

RETRY_STATUSES = {429, 500, 502, 503, 504}


class MFPError(Exception):
    def __init__(self, status, body):
        super().__init__(f"MFP API error {status}: {body}")
        self.status = status
        self.body = body


def _unwrap(payload):
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def pick(d, *keys, default=None):
    """First present, non-None value among keys (tolerates field-name variations)."""
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


class MFPClient:
    def __init__(self, api_key, base_url, timeout=10, retries=3):
        if not api_key:
            raise ValueError("API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries

    # -- transport ------------------------------------------------------------
    def _request(self, method, path, params=None, body=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if method in ("POST", "PUT"):
            # Same key on every retry, so a retried order can never be placed twice.
            headers["Idempotency-Key"] = str(uuid.uuid4())

        for attempt in range(self.retries):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                return _unwrap(json.loads(raw)) if raw else None
            except urllib.error.HTTPError as e:
                err_body = e.read().decode(errors="replace")
                if e.code in RETRY_STATUSES and attempt < self.retries - 1:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                raise MFPError(e.code, err_body) from None
            except urllib.error.URLError:
                if attempt < self.retries - 1:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                raise

    @staticmethod
    def _seg(value):
        return urllib.parse.quote(str(value), safe="")

    # -- accounts -------------------------------------------------------------
    def list_accounts(self):
        return self._request("GET", "/accounts")

    def get_account(self, account_id):
        return self._request("GET", f"/accounts/{self._seg(account_id)}")

    def get_equity(self, account_id):
        return float(self.get_account(account_id)["risk"]["equity"])

    # -- market data ----------------------------------------------------------
    def list_markets(self):
        result = self._request("GET", "/markets")
        return result if isinstance(result, list) else pick(result, "markets", "items", default=[])

    def get_quote(self, market_id, side="buy", size=0.001):
        return self._request("GET", f"/markets/{self._seg(market_id)}/quote",
                             params={"side": side, "size": size})

    def get_mid(self, market_id, size=0.001):
        return float(self.get_quote(market_id, "buy", size)["mid"])

    # -- orders ---------------------------------------------------------------
    def place_market_order(self, account_id, market_id, side, size, expected_price,
                           leverage, margin_mode):
        body = {
            "account_id": account_id,
            "market_id": market_id,
            "side": side,
            "type": "market",
            "size": float(size),
            "expected_price": float(expected_price),
            "leverage": leverage,
            "margin_mode": margin_mode,
        }
        return self._request("POST", "/orders", body=body)

    def get_order(self, order_id):
        return self._request("GET", f"/orders/{self._seg(order_id)}")

    def wait_for_order(self, order_id, timeout=10.0, interval=0.2):
        """Poll until the order is filled/rejected/cancelled; returns the last order seen."""
        deadline = time.monotonic() + timeout
        order = self.get_order(order_id)
        while str(order.get("status", "")).lower() not in ("filled", "rejected", "cancelled", "canceled"):
            if time.monotonic() >= deadline:
                break
            time.sleep(interval)
            order = self.get_order(order_id)
        return order

    # -- positions ------------------------------------------------------------
    def list_positions(self, account_id):
        result = self._request("GET", "/positions", params={"account_id": account_id})
        return result if isinstance(result, list) else pick(result, "positions", "items", default=[])

    def set_exit_orders(self, position_id, size, tp_price, sl_price):
        body = {
            "expected_position_size": float(size),
            "expected_orders": [],
            "operations": [
                {"kind": "place", "group": "tp", "size": float(size), "price": float(tp_price)},
                {"kind": "place", "group": "sl", "size": float(size), "price": float(sl_price)},
            ],
        }
        return self._request("PUT", f"/positions/{self._seg(position_id)}/exit-orders", body=body)

    def close_position(self, position_id):
        return self._request("POST", f"/positions/{self._seg(position_id)}/close", body={})
