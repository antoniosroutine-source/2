"""Part 3.2 placebos and a fast core engine. Usage: python placebo.py bars.csv corrected.json unfiltered.json"""
import json, math, random, statistics as S, sys
import numpy as np, pandas as pd
sys.path.insert(0, ".")
from engine import bad_sessions
TICK, PV, FEE = 0.25, 2.0, 0.74
df = pd.read_csv(sys.argv[1])
ts = pd.to_datetime(df["time"], utc=True) if not np.issubdtype(df["time"].dtype, np.number) else pd.to_datetime(df["time"], unit="s", utc=True)
df.index = ts.dt.tz_convert("America/New_York")
O, H, L, C = (df[c].to_numpy() for c in ("open", "high", "low", "close"))
idx = df.index
mins = (idx.hour * 60 + idx.minute).to_numpy()
dates = np.array(idx.date)
bad = bad_sessions(range(2015, 2027))
# per-night slices: session date d = evening of d; bars 19:00 (d) .. 03:00 (d+1)
sess = (idx - pd.Timedelta(hours=19)).date
sess = np.array(sess)
nights = {}
order = np.argsort(idx.asi8, kind="stable")
starts = {}
for i, d in enumerate(sess):
    m = mins[i]
    if (m >= 19 * 60) or (m < 3 * 60):
        nights.setdefault(d, []).append(i)
ny = {}
for d, grp in df[(mins >= 570) & (mins < 960)].groupby(dates[(mins >= 570) & (mins < 960)]):
    ny[d] = grp["close"].iloc[-1] - grp["open"].iloc[0]
eligible = []
for d, ii in nights.items():
    if d.weekday() >= 4 or d in bad or d not in ny or ny[d] == 0: continue
    ii = np.array(ii); mm = (mins[ii] - 19 * 60) % 1440
    rng = ii[mm < 60]
    if len(rng) < 42: continue
    hi, lo = H[rng].max(), L[rng].min()
    eligible.append(dict(d=d, ii=ii, mm=mm, hi=hi, lo=lo, w=hi - lo, bias=1 if ny[d] < 0 else -1))
def trade(n, k, d, stop_pts, rr=3.33):
    """enter at bar k+1 open (+1 tick), scan exits from bar k+1; returns R or None"""
    ii, mm = n["ii"], n["mm"]
    if k + 1 >= len(ii): return None
    size = min(20, int(500 // (stop_pts * PV + FEE)))
    if size < 1: return None
    e = O[ii[k + 1]] + d * TICK
    tp_pts = min(rr * stop_pts, 1500 / (size * PV))
    st, tp = e - d * stop_pts, e + d * tp_pts
    risk = size * (stop_pts * PV + FEE)
    for j in range(k + 1, len(ii)):
        b = ii[j]
        if mm[j] >= 480: x = O[b] - d * TICK; break
        if (d > 0 and L[b] <= st) or (d < 0 and H[b] >= st):
            x = (O[b] - d * TICK) if (O[b] - st) * d < 0 else st - d * 2 * TICK; break
        if (d > 0 and H[b] >= tp + TICK) or (d < 0 and L[b] <= tp - TICK): x = tp; break
    else:
        x = C[ii[-1]]
    return ((x - e) * d * size * PV - FEE * size) / risk
def sweep(n, shift=0.0, sgn=0, use_bias=True, wmin=33.0):
    """core strategy: first sweep of the (possibly shifted) range that closes back inside, faded."""
    if n["w"] < wmin: return None
    hi, lo, w = n["hi"] + sgn * shift * n["w"], n["lo"] + sgn * shift * n["w"], n["w"]
    ii, mm = n["ii"], n["mm"]
    for k in range(len(ii)):
        if mm[k] < 60: continue
        if mm[k] >= 360: return None
        b = ii[k]
        if C[b] > hi + w or C[b] < lo - w: return None
        side = -1 if (H[b] > hi and C[b] < hi) else 1 if (L[b] < lo and C[b] > lo) else 0
        if not side: continue
        if use_bias and side != n["bias"]: return None
        wick = H[b] if side < 0 else L[b]
        stop = wick - side * 0.1 * w
        sp = max((C[b] - stop) * side, 30.0)
        sp = math.ceil(sp / TICK) * TICK
        return trade(n, k, side, sp)
    return None
rnd = random.Random(11)
real = json.load(open(sys.argv[2]))
real_mean = S.mean(t["R"] for t in real)
stops = [t["stop_pts"] for t in real]
el = [n for n in eligible if n["w"] >= 33]
print(f"eligible nights (Mon-Thu, NY bias defined, range >= 33, calendar ok): {len(el)}; real strategy: {len(real)} trades, mean {real_mean:+.3f}R")
# 3.2a random entry bar in the bias direction, stop distance drawn from the real trades
pl = []
for _ in range(1000):
    rs = []
    for n in rnd.sample(el, len(real)):
        ks = [k for k in range(len(n["ii"])) if 60 <= n["mm"][k] < 360]
        if not ks: continue
        r = trade(n, rnd.choice(ks), n["bias"], rnd.choice(stops))
        if r is not None: rs.append(r)
    pl.append(S.mean(rs))
pl.sort()
print(f"3.2a random-entry placebo (1,000 runs x {len(real)} trades): mean {S.mean(pl):+.3f}R, 95th pct {pl[949]:+.3f}R; real {real_mean:+.3f}R at percentile {sum(x < real_mean for x in pl)/10:.1f}")
# core engine reference and 3.2b shifted levels
core = [r for r in (sweep(n) for n in el) if r is not None]
cm = S.mean(core)
print(f"core engine (range sweep, bias, 33-pt, no liquidity-wait): {len(core)} trades, mean {cm:+.3f}R")
fk = []
for _ in range(1000):
    rs = [r for r in (sweep(n, rnd.uniform(0.25, 1.0), rnd.choice((-1, 1))) for n in el) if r is not None]
    fk.append(S.mean(rs) if rs else 0)
fk.sort()
print(f"3.2b fake levels (range shifted 0.25-1.0 widths, 1,000 runs): mean {S.mean(fk):+.3f}R, 95th pct {fk[949]:+.3f}R; core real {cm:+.3f}R at percentile {sum(x < cm for x in fk)/10:.1f}")
un = json.load(open(sys.argv[3]))
rev = [t for t in un if t["ny_move"] and t["w"] >= 33 and t["ny_move"] * (1 if t["side"] == "long" else -1) > 0]
print(f"3.2c NY bias reversed: {len(rev)} trades, mean {S.mean(t['R'] for t in rev):+.3f}R, net ${sum(t['pnl'] for t in rev):,.0f}")
