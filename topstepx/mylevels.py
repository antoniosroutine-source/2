"""Levels you type in on the desk page (from a stream, your chart, anywhere): the bot alerts you as
price approaches each one and scores every test as HELD or BROKE.

A test starts when price reaches the level. It is HELD if price then moves MY_LEVEL_HOLD_PTS away
(back the way it came) before trading MY_LEVEL_BREAK_PTS through, and BROKE if it trades through first.
After each result the level re-arms, so one level can be tested many times. Levels are saved to
MY_LEVELS_FILE (they survive restarts) and every test to MY_LEVELS_LOG (one JSON line each).
"""
import itertools
import json
import os
import threading
import time


def parse_price(text):
    return float(str(text).replace(",", "").replace("$", "").strip())


class MyLevels:
    def __init__(self, p, alert, log):
        self.p, self.alert, self.log = p, alert, log
        self.lock = threading.Lock()
        self.levels = []
        if os.path.exists(p.MY_LEVELS_FILE):
            try:
                with open(p.MY_LEVELS_FILE) as f:
                    self.levels = json.load(f)
            except (OSError, ValueError):
                self.levels = []
        self.ids = itertools.count(max([lv["id"] for lv in self.levels], default=0) + 1)
        self.last = None      # last known price

    # -- desk page --------------------------------------------------------------------
    def add(self, price, note=""):
        with self.lock:
            lv = {"id": next(self.ids), "price": round(parse_price(price), 2), "note": str(note)[:120],
                  "added": time.time(), "side": None, "armed": False, "near_sent": False, "test": None,
                  "held": 0, "broke": 0, "results": []}
            if self.last is not None:
                self._arm(lv, self.last)
            self.levels.append(lv)
            self._save()
        self.log("my_level_added", price=lv["price"], note=lv["note"])
        return lv

    def remove(self, lid):
        with self.lock:
            n = len(self.levels)
            self.levels = [lv for lv in self.levels if lv["id"] != lid]
            self._save()
            return len(self.levels) < n

    def view(self):
        with self.lock:
            out = []
            for lv in sorted(self.levels, key=lambda x: -x["price"]):
                state = "testing" if lv["test"] else ("waiting" if lv["armed"] else "arming")
                out.append({"id": lv["id"], "price": lv["price"], "note": lv["note"], "side": lv["side"],
                            "state": state, "held": lv["held"], "broke": lv["broke"],
                            "last": lv["results"][-1] if lv["results"] else None,
                            "dist": None if self.last is None else round(lv["price"] - self.last, 2)})
            return out

    # -- bot loop ---------------------------------------------------------------------
    def update(self, lo, hi, ts):
        """Feed a bar's low/high (or lo = hi = the latest price)."""
        p = self.p
        msgs = []
        with self.lock:
            for lv in self.levels:
                L = lv["price"]
                if lv["side"] is None:
                    self._arm(lv, (lo + hi) / 2)
                    continue
                d = 1 if lv["side"] == "below" else -1        # +1: support (price above it)
                if lv["test"]:
                    t = lv["test"]
                    t["through"] = max(t["through"], (L - lo) * d if d > 0 else (hi - L))
                    t["away"] = max(t["away"], (hi - L) if d > 0 else (L - lo))
                    res = "BROKE" if t["through"] >= p.MY_LEVEL_BREAK_PTS else \
                          "HELD" if t["away"] >= p.MY_LEVEL_HOLD_PTS else None
                    if res:
                        rec = {"id": lv["id"], "price": L, "note": lv["note"], "role": "support" if d > 0 else "resistance",
                               "result": res, "touched": t["ts"], "decided": ts,
                               "max_through": round(t["through"], 2), "max_away": round(t["away"], 2)}
                        lv["held" if res == "HELD" else "broke"] += 1
                        lv["results"].append(res)
                        lv["test"] = None
                        self._arm(lv, hi if res == "BROKE" and d < 0 else lo if res == "BROKE" else (lo + hi) / 2)
                        self._write(rec)
                        msgs.append(f"{L:,.2f} {lv['note'] or 'my level'} {res} "
                                    f"(through {rec['max_through']:.2f}, away {rec['max_away']:.2f} pts)")
                    continue
                gap = (lo - L) if d > 0 else (L - hi)              # distance still to go (> 0 before touch)
                if not lv["armed"]:
                    if gap >= p.MY_LEVEL_NEAR_PTS:
                        lv["armed"], lv["near_sent"] = True, False
                    continue
                if gap <= 0:
                    lv["test"] = {"ts": ts, "through": max(0.0, -gap), "away": 0.0}
                    msgs.append(f"price at {L:,.2f} {lv['note'] or 'my level'} ({'support' if d > 0 else 'resistance'})")
                elif gap <= p.MY_LEVEL_NEAR_PTS and not lv["near_sent"]:
                    lv["near_sent"] = True
                    msgs.append(f"approaching {L:,.2f} {lv['note'] or 'my level'} ({gap:.2f} pts away)")
            self.last = (lo + hi) / 2
            self._save()
        for m in msgs:
            self.alert(m)

    # -- helpers ------------------------------------------------------------------------
    def _arm(self, lv, price):
        lv["side"] = "below" if lv["price"] < price else "above"
        lv["armed"] = abs(price - lv["price"]) >= self.p.MY_LEVEL_NEAR_PTS
        lv["near_sent"] = False

    def _save(self):
        tmp = self.p.MY_LEVELS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.levels, f)
        os.replace(tmp, self.p.MY_LEVELS_FILE)

    def _write(self, rec):
        with open(self.p.MY_LEVELS_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
