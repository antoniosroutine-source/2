"""Backtest of the pre-registered NY-open strategy (NYOPEN-PREREG.md). Usage: python nyopen.py a.csv [b.csv ...]"""
import datetime as dt, math, random, statistics as S, sys
import holidays
sys.path.insert(0, "..")
import backtest
from levels import et
TICK, PV, FEE, RISK, MAXC, CAP = 0.25, 2.0, 0.74, 500.0, 20, 1500.0

bars = sorted({b.t: b for f in sys.argv[1:] for b in backtest.load_csv(f)}.values(), key=lambda b: b.t)
ET = [et(b.t) for b in bars]
BK = [int(b.t // 300) for b in bars]
# 5-minute buckets: OHLC per bucket id
buck = {}
for b, k in zip(bars, BK):
    x = buck.get(k)
    if x is None: buck[k] = [b.o, b.h, b.l, b.c]
    else: x[1] = max(x[1], b.h); x[2] = min(x[2], b.l); x[3] = b.c
keys = sorted(buck)
def ema_map(n):
    a = 2 / (n + 1); e = None; out = {}
    for k in keys:
        c = buck[k][3]; e = c if e is None else e + a * (c - e); out[k] = e
    return out
NYSE = set()
for y in range(2017, 2027): NYSE |= set(holidays.financial_holidays("NYSE", years=y))
# index of the first 1-minute bar of each date at or after a given time
day_first = {}
for i, t in enumerate(ET):
    d = t.date()
    if d.weekday() < 5 and d not in NYSE and t.hour * 60 + t.minute >= 570:
        day_first.setdefault(d, i)

def run(ema_n=12, trail="ema", signal_min=570, rnd=None):
    em = ema_map(ema_n); out = []
    for d, i0 in sorted(day_first.items()):
        t0 = dt.datetime(d.year, d.month, d.day, signal_min // 60, signal_min % 60, tzinfo=ET[i0].tzinfo).timestamp()
        k = int(t0 // 300)
        if k not in buck or k not in em: continue
        cnt = sum(1 for j in range(i0, min(i0 + 15, len(bars))) if BK[j] == k)
        if cnt < 4: continue
        o, h, l, c = buck[k]
        side = (1 if c > em[k] else -1 if c < em[k] else 0) if rnd is None else rnd.choice((1, -1))
        if not side: continue
        j = next((j for j in range(i0, min(i0 + 60, len(bars))) if bars[j].t >= t0 + 300), None)
        if j is None or ET[j].date() != d: continue
        e = bars[j].o + side * TICK
        stop = (l - TICK) if side > 0 else (h + TICK)
        sp = (e - stop) * side
        if sp <= 0: continue
        size = min(MAXC, int(RISK // (sp * PV + FEE)))
        if size < 1: continue
        tgt = e + side * CAP / (size * PV)
        risk = size * (sp * PV + FEE)
        cur = BK[j]; x = None
        for m in range(j, len(bars)):
            b = bars[m]; tm = ET[m]
            if tm.date() != d or tm.hour * 60 + tm.minute >= 955:
                x = b.o - side * TICK; why = "time"; break
            if BK[m] != cur:                       # the previous 5-minute bucket is complete
                prev = cur; cur = BK[m]
                if trail == "ema" and prev in em:
                    new = em[prev] - side * TICK
                elif trail == "bar" and prev in buck:
                    new = (buck[prev][2] - TICK) if side > 0 else (buck[prev][1] + TICK)
                else:
                    new = None
                if new is not None and (new - stop) * side > 0: stop = new
            if (side > 0 and b.l <= stop) or (side < 0 and b.h >= stop):
                x = (b.o - side * TICK) if (b.o - stop) * side < 0 else stop - side * 2 * TICK; why = "stop"; break
            if (side > 0 and b.h >= tgt + TICK) or (side < 0 and b.l <= tgt - TICK):
                x = tgt; why = "cap"; break
        if x is None: continue
        pnl = (x - e) * side * size * PV - FEE * size
        out.append({"d": d, "side": side, "R": pnl / risk, "pnl": pnl, "why": why, "sp": sp, "size": size})
    return out

def stat(tr):
    R = [t["R"] for t in tr]; n = len(R); m = S.mean(R); sd = S.stdev(R)
    return n, m, m / (sd / math.sqrt(n)), sum(t["pnl"] for t in tr)

def line(label, tr):
    n, m, t_, net = stat(tr)
    return f"| {label} | {n} | {sum(x['R'] > 0 for x in tr)/n:.0%} | {m:+.3f} | {t_:.2f} | {net:+,.0f} |"

if __name__ == "__main__":
    from statistics import NormalDist
    prim = run()
    print("| version | trades | win | exp R | t | net $ |\n|---|---|---|---|---|---|")
    print(line("PRIMARY (12 EMA trail)", prim))
    a1, a2 = run(trail="bar"), run(trail="none")
    print(line("A1 prior-bar trail", a1)); print(line("A2 no trail", a2))
    segs = (("2018-01..2021-03", dt.date(2018, 1, 1), dt.date(2021, 3, 31)), ("2021-04..2024-08", dt.date(2021, 4, 1), dt.date(2024, 8, 31)),
            ("2024-09..2026-09", dt.date(2024, 9, 1), dt.date(2026, 9, 30)))
    for lab, a, b in segs: print(line("primary " + lab, [t for t in prim if a <= t["d"] <= b]))
    for y in sorted({t["d"].year for t in prim}):
        print(line(f"primary {y}", [t for t in prim if t["d"].year == y]))
    print(line("primary longs", [t for t in prim if t["side"] > 0])); print(line("primary shorts", [t for t in prim if t["side"] < 0]))
    print("exits:", {w: sum(t["why"] == w for t in prim) for w in ("stop", "cap", "time")},
          "median stop pts", round(S.median(t["sp"] for t in prim), 1), "median size", S.median(t["size"] for t in prim))
    k5 = [run(ema_n=9), run(ema_n=21), run(signal_min=575)]
    for lab, tr in zip(("K5 EMA 9", "K5 EMA 21", "K5 second candle 09:35-09:39"), k5): print(line(lab, tr))
    print(f"K5 average: {S.mean(stat(t)[1] for t in k5):+.3f}R")
    n, m, t_, net = stat(prim)
    p = 1 - NormalDist().cdf(t_)
    print(f"K6 one-sided p {p:.4f} x3 = {min(1, 3*p):.4f}")
    r = random.Random(5); pl = sorted(stat(run(rnd=r))[1] for _ in range(int(sys.argv[0] and 200)))
    print(f"K4 random-direction placebo ({len(pl)} runs): mean {S.mean(pl):+.3f}R, 95th {pl[int(.95*len(pl))]:+.3f}R; real {m:+.3f}R at percentile {sum(x < m for x in pl)/len(pl)*100:.1f}")
