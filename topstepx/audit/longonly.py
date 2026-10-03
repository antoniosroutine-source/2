"""Long-only K4/K5/K6 on in-sample data. Usage: python longonly.py bars.csv corrected.json"""
import json, math, random, statistics as S, sys, datetime as dt
from statistics import NormalDist
from multiprocessing import Pool
N01 = NormalDist()
CSV, CORR = sys.argv[1], sys.argv[2]
LONG = lambda s: s["side"] == "long"

def k5job(job):
    import engine, strategy
    from levels import ET, minutes
    name, start, over = job
    bars = engine.load_bars(CSV)
    cls = engine.Checked
    if start:
        def clock(ts, s="19:00", _st=start):
            t = dt.datetime.fromtimestamp(ts, ET) - dt.timedelta(minutes=minutes(_st))
            return t.date(), t.hour * 60 + t.minute
        strategy.asia_clock = clock
        class Off(engine.Checked):
            def __init__(s, p, tk, pv):
                super().__init__(p, tk, pv)
                off = minutes(start) - minutes("19:00"); s.until -= off; s.exit_min -= off
        cls = Off
    tr = engine.simulate(bars, ["next_open", "stop2", "through", "lookahead", "calendar"], p=engine.params(**over),
                         strat_cls=cls, accept=LONG)
    R = [t["R"] for t in tr]
    return name, len(R), S.mean(R) if R else 0.0

if __name__ == "__main__":
    real = [t for t in json.load(open(CORR)) if t["side"] == "long"]
    R = [t["R"] for t in real]; n = len(R); m = S.mean(R); sd = S.stdev(R); t_ = m / (sd / math.sqrt(n))
    print(f"K1 in-sample long-only: n={n} expR={m:+.3f} t={t_:.2f} net=${sum(t['pnl'] for t in real):,.0f}")
    # K6
    p1 = 1 - N01.cdf(t_); NT = 441
    sr = m / sd; g3 = sum(((x - m) / sd) ** 3 for x in R) / n; g4 = sum(((x - m) / sd) ** 4 for x in R) / n
    emc = 0.5772156649
    sr0 = math.sqrt(1 / (n - 1)) * ((1 - emc) * N01.inv_cdf(1 - 1 / NT) + emc * N01.inv_cdf(1 - 1 / (NT * math.e)))
    dsr = N01.cdf((sr - sr0) * math.sqrt(n - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr * sr))
    print(f"K6 one-sided p={p1:.4f}, Bonferroni x{NT} = {min(1, p1*NT):.3f}; Deflated Sharpe (N={NT}) = {dsr:.3f}")
    # K4: random long entries on the same eligible nights (bias long, range >= 33)
    sys.argv = [sys.argv[0], CSV, CORR, CORR.replace("audit_corrected", "audit_unfiltered")]
    import placebo as P            # builds eligible nights; its own prints are for the full strategy
    el = [x for x in P.el if x["bias"] == 1]
    stops = [t["stop_pts"] for t in real]
    rnd = random.Random(23); pl = []
    for _ in range(1000):
        rs = []
        for nt in rnd.sample(el, min(n, len(el))):
            ks = [k for k in range(len(nt["ii"])) if 60 <= nt["mm"][k] < 360]
            if ks:
                r = P.trade(nt, rnd.choice(ks), 1, rnd.choice(stops))
                if r is not None: rs.append(r)
        pl.append(S.mean(rs))
    pl.sort()
    print(f"K4 random long entries ({len(el)} eligible long nights, 1,000 runs x {n}): mean {S.mean(pl):+.3f}R, "
          f"95th pct {pl[949]:+.3f}R; real {m:+.3f}R at percentile {sum(x < m for x in pl)/10:.1f}")
    # K5
    jobs = [("range 18:45-19:45", "18:45", {}), ("range 19:15-20:15", "19:15", {}), ("range 19:30-20:30", "19:30", {})]
    jobs += [(f"entry cutoff {u}", None, {"ASIA_TRADE_UNTIL": u}) for u in ("00:30", "00:45", "01:15", "01:30")]
    jobs += [(f"time exit {e}", None, {"ASIA_EXIT_AT": e}) for e in ("02:30", "02:45", "03:15", "03:30")]
    with Pool(3, maxtasksperchild=1) as pool:
        out = pool.map(k5job, jobs)
    for o in out: print(f"  K5 {o[0]}: n={o[1]} expR={o[2]:+.3f}")
    print(f"K5 average over {len(out)} offset-clock variants: {S.mean(o[2] for o in out):+.3f}R")
