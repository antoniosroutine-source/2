"""Daily Nasdaq gamma levels (gamma flip, call wall, put wall) from the free haus-edge/gex-levels feed.

The feed is computed each trading day from QQQ options and published in Nasdaq-100 *cash* prices.
MNQ futures trade at a premium to cash, so each level is scaled by futures / cash at the moment
the snapshot was computed (the snapshot's UNDERLYING vs. the MNQ close at its TIMESTAMP).

Every new snapshot is saved to GAMMA_DIR/<date>.txt, so the bot builds its own history.
Backtest (Apr-Sep 2026): Asia fades aimed *toward* the gamma flip won 47%; fades aimed away won 0%.
"""
import datetime as dt
import os
import urllib.request


def parse(text):
    d = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    ts = dt.datetime.fromisoformat(d["TIMESTAMP"].replace("Z", "+00:00")).timestamp()
    return {"ts": ts, "regime": d.get("REGIME"), "flip": float(d["GAMMA_FLIP"]),
            "call": float(d["CALL_WALL"]), "put": float(d["PUT_WALL"]), "cash": float(d["UNDERLYING"])}


def fetch_url(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "topstepx-bot"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


class Gamma:
    """price_at(ts) -> MNQ price near ts (or None); fetch(url) -> text (injectable for tests)."""

    def __init__(self, p, price_at, log, fetch=fetch_url):
        self.p, self.price_at, self.log, self.fetch = p, price_at, log, fetch
        self.snap = None          # latest snapshot, levels converted to futures prices
        self.last_try = 0.0

    def refresh(self, now, force=False):
        if not force and now - self.last_try < self.p.GAMMA_REFRESH_SEC:
            return
        self.last_try = now
        try:
            text = self.fetch(self.p.GAMMA_URL)
            s = parse(text)
        except (OSError, ValueError, KeyError) as e:
            self.log("gamma_error", error=f"could not read the gamma levels: {e}")
            return
        if self.snap and s["ts"] <= self.snap["ts"]:
            return
        fut = self.price_at(s["ts"])
        if not fut:
            self.log("gamma_error", error="no MNQ price at the snapshot time; levels not converted")
            return
        ratio = fut / s["cash"]
        s.update(ratio=round(ratio, 6), flip_fut=self._tick(s["flip"] * ratio),
                 call_fut=self._tick(s["call"] * ratio), put_fut=self._tick(s["put"] * ratio))
        self.snap = s
        os.makedirs(self.p.GAMMA_DIR, exist_ok=True)
        day = dt.datetime.fromtimestamp(s["ts"], dt.timezone.utc).date()
        with open(os.path.join(self.p.GAMMA_DIR, f"{day}.txt"), "w") as f:
            f.write(text)
        self.log("gamma", **self.levels())

    def _tick(self, x):
        return round(round(x / self.p.TICK_SIZE) * self.p.TICK_SIZE, 2)

    def levels(self):
        s = self.snap
        if not s:
            return {}
        return {"regime": s["regime"], "flip": s["flip_fut"], "call_wall": s["call_fut"], "put_wall": s["put_fut"],
                "basis_ratio": s["ratio"], "computed": dt.datetime.fromtimestamp(s["ts"], dt.timezone.utc).isoformat()}

    def check(self, sig, now):
        """{'toward_flip': bool, ...} for a signal, or None when there is no fresh snapshot."""
        s = self.snap
        if not s or now - s["ts"] > self.p.GAMMA_MAX_AGE_HOURS * 3600:
            return None
        d = 1 if sig["side"] == "long" else -1
        return {**self.levels(), "toward_flip": (s["flip_fut"] - sig["entry"]) * d > 0}
