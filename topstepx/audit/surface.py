"""3.3 offset clock and 3.4 parameter surface on the corrected engine. Usage: python surface.py bars.csv unfiltered.json"""
import json, math, statistics as S, sys, datetime as dt
from multiprocessing import Pool
import engine, strategy
ORIG_CLOCK = strategy.asia_clock
from levels import ET, minutes
STEPS = ["next_open", "stop2", "through", "lookahead", "calendar"]
BARS = None
def init(path):
    global BARS
    BARS = engine.load_bars(path)
def run(job):
    name, start, over = job
    if start:
        def clock(ts, s="19:00", _st=start):
            t = dt.datetime.fromtimestamp(ts, ET) - dt.timedelta(minutes=minutes(_st))
            return t.date(), t.hour * 60 + t.minute
        strategy.asia_clock = clock
        class Off(engine.Checked):
            def __init__(s, p, tk, pv):
                super().__init__(p, tk, pv)
                off = minutes(start) - minutes("19:00")
                s.until -= off; s.exit_min -= off
        cls = Off
    else:
        strategy.asia_clock = ORIG_CLOCK      # worker processes are reused: undo an earlier job's clock
        cls = engine.Checked
    tr = engine.simulate(BARS, STEPS, p=engine.params(**over), strat_cls=cls)
    R = [t["R"] for t in tr]
    m = S.mean(R) if R else 0; t_ = m / (S.stdev(R) / math.sqrt(len(R))) if len(R) > 2 else 0
    return name, len(R), m, t_, sum(t["pnl"] for t in tr)
if __name__ == "__main__":
    jobs = [("range 18:45-19:45", "18:45", {}), ("range 19:15-20:15", "19:15", {}), ("range 19:30-20:30", "19:30", {})]
    for u in ("00:30", "00:45", "01:15", "01:30"): jobs.append((f"entry cutoff {u}", None, {"ASIA_TRADE_UNTIL": u}))
    for e in ("02:30", "02:45", "03:15", "03:30"): jobs.append((f"time exit {e}", None, {"ASIA_EXIT_AT": e}))
    nclock = len(jobs)
    for rr in [2.0 + 0.25 * i for i in range(11)]: jobs.append((f"target {rr:.2f}x", None, {"ASIA_RR": rr}))
    for bf in (0.0, 0.05, 0.10, 0.15, 0.20, 0.25): jobs.append((f"stop buffer {bf:.0%}", None, {"ASIA_BUF_FRAC": bf}))
    with Pool(3, maxtasksperchild=1, initializer=init, initargs=(sys.argv[1],)) as pool:
        out = pool.map(run, jobs)
    print("| variant | trades | exp R | t | net $ |\n|---|---|---|---|---|")
    for o in out: print(f"| {o[0]} | {o[1]} | {o[2]:+.3f} | {o[3]:.2f} | {o[4]:+,.0f} |")
    print(f"offset-clock average ({nclock} variants): {S.mean(o[2] for o in out[:nclock]):+.3f}R")
    un = json.load(open(sys.argv[2]))
    def ok(t): return t["ny_move"] and t["ny_move"] * (1 if t["side"] == "long" else -1) < 0
    print("\nminimum range (points): " + "  ".join(
        f"{w}:{S.mean(t['R'] for t in un if ok(t) and t['w'] >= w):+.2f}" for w in range(20, 51)))
    print("minimum range (% of price): " + "  ".join(
        f"{p:.2f}%:{S.mean(t['R'] for t in un if ok(t) and t['w'] / t['c'] * 100 >= p):+.2f}({sum(1 for t in un if ok(t) and t['w'] / t['c'] * 100 >= p)})"
        for p in (0.06, 0.08, 0.10, 0.11, 0.12, 0.13, 0.14, 0.16, 0.18, 0.20)))
