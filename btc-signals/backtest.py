"""Backtest the RSI SHORT signals on BTC 1-minute history (Binance public archive zips or a CSV).

    python backtest.py DATA_DIR_OR_CSV [--tf 5] [--mode touch]

A signal is shorted at the next candle's open with the alerted stop and target; the stop is checked first
inside each candle (pessimistic); exit at the close after MAX_HOLD_BARS candles. Costs: 0.04% round trip.
Also runs a placebo: random shorts with the same stop/target rules.
"""
import argparse, csv, glob, io, math, os, random, statistics as S, types, zipfile
import config
from signals import Candle, SignalEngine

COST = 0.0004


def load_1m(path):
    rows = []
    files = sorted(glob.glob(os.path.join(path, "*.zip"))) if os.path.isdir(path) else [path]
    for f in files:
        if f.endswith(".zip"):
            with zipfile.ZipFile(f) as z:
                text = z.read(z.namelist()[0]).decode()
        else:
            text = open(f).read()
        for r in csv.reader(io.StringIO(text)):
            if not r or not r[0][0].isdigit():
                continue
            t = int(r[0])
            t = t / 1e6 if t > 1e14 else t / 1e3 if t > 1e11 else t
            rows.append(Candle(t, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
    rows.sort(key=lambda c: c.t)
    return rows


def aggregate(m1, tf):
    out, cur, key = [], None, None
    for c in m1:
        k = int(c.t // (tf * 60))
        if k != key:
            if cur: out.append(cur)
            key, cur = k, Candle(k * tf * 60, c.o, c.h, c.l, c.c, c.v)
        else:
            cur.h, cur.l, cur.c, cur.v = max(cur.h, c.h), min(cur.l, c.l), c.c, cur.v + c.v
    if cur: out.append(cur)
    return out


def trade(cs, i, stop, target, hold, cost=None):
    """Short at candle i's open. Returns R after costs (or before costs with cost=0), or None."""
    if i >= len(cs): return None
    e = cs[i].o
    risk = stop - e
    if risk <= 0: return None
    x = None
    for j in range(i, min(i + hold, len(cs))):
        c = cs[j]
        if c.h >= stop: x = stop; break
        if c.l <= target: x = target; break
    if x is None: x = cs[min(i + hold, len(cs)) - 1].c
    return (e - x) / risk - (COST if cost is None else cost) * e / risk


def run(cs, p):
    eng = SignalEngine(p)
    out = []
    for i, c in enumerate(cs):
        for ev in eng.on_candle(c):
            if ev["kind"] == "short":
                r = trade(cs, i + 1, ev["stop"], ev["target"], p.MAX_HOLD_BARS)
                if r is not None:
                    g = trade(cs, i + 1, ev["stop"], ev["target"], p.MAX_HOLD_BARS, cost=0.0)
                    out.append({"t": c.t, "R": r, "G": g, "rsi": ev["rsi"], "risk_pct": ev["risk_pct"]})
    return out


def placebo(cs, p, n, runs=300, seed=3, cost=0.0):
    """Random shorts: same stop rule (swing high + buffer, >= 0.5 ATR) and target."""
    from indicators import ATR
    atr, highs, lv = ATR(p.ATR_LEN), [], []
    for c in cs:
        a = atr.update(c.h, c.l, c.c); highs.append(c.h)
        if a is None or len(highs) < p.SWING_BARS: lv.append(None); continue
        stop = max(max(highs[-p.SWING_BARS:]) + p.STOP_ATR_BUFFER * a, c.c + 0.5 * a)
        lv.append((stop, c.c - p.TARGET_R * (stop - c.c)))
    idx = [i for i in range(len(cs) - 1) if lv[i]]
    rnd = random.Random(seed); means = []
    for _ in range(runs):
        rs = [r for r in (trade(cs, i + 1, *lv[i], p.MAX_HOLD_BARS, cost=cost) for i in rnd.sample(idx, n)) if r is not None]
        means.append(S.mean(rs))
    return sorted(means)


def summary(label, tr, weeks):
    R = [t["R"] for t in tr]; G = [t["G"] for t in tr]; n = len(R)
    if n < 3: return f"| {label} | {n} | | | | |"
    m, sd, mg, sg = S.mean(R), S.stdev(R), S.mean(G), S.stdev(G)
    risk = S.median(t["risk_pct"] for t in tr)
    return (f"| {label} | {n} ({n/weeks:.1f}/wk) | {sum(g > 0 for g in G)/n:.0%} | {mg:+.3f} (t {mg/(sg/math.sqrt(n)):.1f}) | "
            f"{m:+.3f} (t {m/(sd/math.sqrt(n)):.1f}) | {risk:.2f}% |")


def params(**over):
    p = {k: getattr(config, k) for k in dir(config) if k.isupper()}
    p.update(over)
    return types.SimpleNamespace(**p)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("data"); ap.add_argument("--tf", type=int, nargs="*", default=[1, 5, 15, 60])
    ap.add_argument("--mode", nargs="*", default=["touch", "turn"])
    a = ap.parse_args()
    m1 = load_1m(a.data)
    print(f"{len(m1):,} one-minute candles, {m1[0].t:.0f}..{m1[-1].t:.0f}")
    print("| version | signals | win | exp R before costs | exp R after 0.04% costs | median stop distance |\n|---|---|---|---|---|---|")
    half = m1[len(m1) // 2].t
    for tf in a.tf:
        cs = aggregate(m1, tf)
        weeks = (cs[-1].t - cs[0].t) / 604800
        for mode in a.mode:
            p = params(MODE=mode, MAX_HOLD_BARS=max(4, 120 // tf))
            tr = run(cs, p)
            print(summary(f"{tf}m {mode} (all)", tr, weeks))
            print(summary(f"{tf}m {mode} year 1", [t for t in tr if t["t"] < half], weeks / 2))
            print(summary(f"{tf}m {mode} year 2", [t for t in tr if t["t"] >= half], weeks / 2))
            if tr:
                pl = placebo(cs, p, min(len(tr), 2000))
                m = S.mean(t["G"] for t in tr)
                print(f"|  placebo random shorts (before costs): mean {S.mean(pl):+.3f}R, 95th {pl[int(.95*len(pl))]:+.3f}R; signal at percentile {sum(x < m for x in pl)/len(pl)*100:.0f} |||||", flush=True)
