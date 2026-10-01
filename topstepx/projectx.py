"""Small ProjectX Gateway (TopstepX) REST client, standard library only.

Every endpoint is a POST with a JSON body and returns {"success", "errorCode", "errorMessage", ...}.
Tokens last 24 hours; the client refreshes them with /api/Auth/validate before they expire.
Rate limits: retrieveBars 50 per 30 s, everything else 200 per 60 s.
"""
import datetime as dt
import json
import time
import urllib.error
import urllib.request

from strategy import Bar

ORDER_LIMIT, ORDER_MARKET, ORDER_STOP = 1, 2, 4
SIDE_BUY, SIDE_SELL = 0, 1
POS_LONG, POS_SHORT = 1, 2          # position "type"
ORDER_OPEN, ORDER_FILLED = 1, 2     # order "status"


class PXError(Exception):
    def __init__(self, where, code, message):
        super().__init__(f"{where} failed (errorCode {code}): {message}")
        self.code = code
        self.message = message


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProjectX:
    def __init__(self, base_url, username, api_key, timeout=15):
        if not username or not api_key:
            raise ValueError("username and API key are required")
        self.base = base_url.rstrip("/")
        self.username = username
        self.api_key = api_key
        self.timeout = timeout
        self.token = None
        self.token_time = 0.0

    # -- transport ------------------------------------------------------------------
    def _post(self, path, body, auth=True, retries=3):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth:
            self._ensure_token()
            headers["Authorization"] = f"Bearer {self.token}"
        data = json.dumps(body).encode()
        for attempt in range(retries):
            req = urllib.request.Request(self.base + path, data=data, method="POST", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    out = json.loads(resp.read() or b"{}")
                break
            except urllib.error.HTTPError as e:
                if e.code == 401 and auth and attempt == 0 and retries > 1:
                    self.login()
                    headers["Authorization"] = f"Bearer {self.token}"
                    continue
                if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(2 * 2 ** attempt)
                    continue
                raise PXError(path, f"HTTP {e.code}", e.read().decode(errors="replace")[:300]) from None
            except urllib.error.URLError:
                if attempt < retries - 1:
                    time.sleep(2 * 2 ** attempt)
                    continue
                raise
        if not out.get("success", False):
            raise PXError(path, out.get("errorCode"), out.get("errorMessage"))
        return out

    def login(self):
        out = self._post("/api/Auth/loginKey", {"userName": self.username, "apiKey": self.api_key}, auth=False)
        self.token, self.token_time = out["token"], time.time()
        return self.token

    def _ensure_token(self):
        if not self.token:
            self.login()
        elif time.time() - self.token_time > 20 * 3600:   # refresh well before the 24-hour expiry
            req = urllib.request.Request(self.base + "/api/Auth/validate", data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {self.token}"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    out = json.loads(resp.read())
            except (urllib.error.URLError, ValueError):
                out = {}
            if out.get("success") and out.get("newToken"):
                self.token, self.token_time = out["newToken"], time.time()
            else:
                self.login()

    # -- accounts and contracts -----------------------------------------------------
    def accounts(self):
        return self._post("/api/Account/search", {"onlyActiveAccounts": True}).get("accounts", [])

    def find_contract(self, search, symbol_id, live=False):
        found = self._post("/api/Contract/search", {"searchText": search, "live": live}).get("contracts", [])
        matches = [c for c in found if c.get("symbolId") == symbol_id and c.get("activeContract")]
        return matches[0] if matches else None

    # -- market data ------------------------------------------------------------------
    def bars(self, contract_id, start, end, limit=20000, partial=False, live=False):
        """1-minute bars between two datetimes, oldest first."""
        out = self._post("/api/History/retrieveBars", {
            "contractId": contract_id, "live": live, "startTime": iso(start), "endTime": iso(end),
            "unit": 2, "unitNumber": 1, "limit": limit, "includePartialBar": partial})
        bars = [Bar(dt.datetime.fromisoformat(b["t"]).timestamp(), float(b["o"]), float(b["h"]),
                    float(b["l"]), float(b["c"]), float(b.get("v") or 0)) for b in out.get("bars", [])]
        return sorted(bars, key=lambda b: b.t)

    def bars_range(self, contract_id, start, end, live=False):
        """Any span of 1-minute bars, fetched in 10-day pieces (20,000 bars max per request)."""
        out, cur = {}, start
        while cur < end:
            nxt = min(end, cur + dt.timedelta(days=10))
            for b in self.bars(contract_id, cur, nxt, live=live):
                out[b.t] = b
            cur = nxt
            time.sleep(0.7)   # stays well inside 50 requests / 30 s
        return [out[t] for t in sorted(out)]

    # -- orders and positions -----------------------------------------------------------
    def place(self, account_id, contract_id, order_type, side, size, limit_price=None, stop_price=None, tag=None,
              stop_ticks=None, target_ticks=None):
        """Place an order. stop_ticks/target_ticks attach a linked (OCO) stop and target that the
        platform creates on the fill; the account must be in Auto OCO Brackets mode.
        Never retried automatically: a retry after a lost response could double the position."""
        body = {"accountId": account_id, "contractId": contract_id, "type": order_type, "side": side,
                "size": int(size), "limitPrice": limit_price, "stopPrice": stop_price, "trailPrice": None,
                "customTag": tag, "stopLossBracket": None, "takeProfitBracket": None}
        # ProjectX bracket ticks are signed relative to the fill: for a buy the stop is below
        # (negative ticks) and the target above (positive); for a sell it is the other way round.
        d = 1 if side == SIDE_BUY else -1
        if stop_ticks:
            body["stopLossBracket"] = {"ticks": -d * abs(int(stop_ticks)), "type": ORDER_STOP}
        if target_ticks:
            body["takeProfitBracket"] = {"ticks": d * abs(int(target_ticks)), "type": ORDER_LIMIT}
        return self._post("/api/Order/place", body, retries=1)["orderId"]

    def modify(self, account_id, order_id, stop_price=None, limit_price=None, size=None):
        self._post("/api/Order/modify", {"accountId": account_id, "orderId": order_id, "size": size,
                                         "limitPrice": limit_price, "stopPrice": stop_price, "trailPrice": None},
                   retries=1)

    def cancel(self, account_id, order_id):
        self._post("/api/Order/cancel", {"accountId": account_id, "orderId": order_id})

    def open_orders(self, account_id):
        return self._post("/api/Order/searchOpen", {"accountId": account_id}).get("orders", [])

    def positions(self, account_id):
        return self._post("/api/Position/searchOpen", {"accountId": account_id}).get("positions", [])

    def close_position(self, account_id, contract_id):
        self._post("/api/Position/closeContract", {"accountId": account_id, "contractId": contract_id}, retries=1)

    def trades(self, account_id, start, end=None):
        body = {"accountId": account_id, "startTimestamp": iso(start)}
        if end:
            body["endTimestamp"] = iso(end)
        return self._post("/api/Trade/search", body).get("trades", [])
