import math, statistics as S, sys, json
from engine import simulate, load_bars, Checked
BARS = sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude-0/-home-user-2/d0bd11b9-f841-533e-963d-b8a3ecf3e482/scratchpad/nq_1m.csv"
bars = load_bars(BARS)
def row(name, tr, prev=None):
    R = [t["R"] for t in tr]; n = len(R)
    m = S.mean(R); sd = S.stdev(R); tt = m / (sd / math.sqrt(n))
    net = sum(t["pnl"] for t in tr)
    win = sum(t["pnl"] > 0 for t in tr) / n
    ch = f"{net - prev:+,.0f}" if prev is not None else ""
    print(f"| {name} | {n} | {win:.1%} | {m:+.3f} | {tt:.2f} | {net:+,.0f} | {ch} |", flush=True)
    return net
print("| fix | trades | win% | exp R | t | net $ | change |\n|---|---|---|---|---|---|---|")
steps, prev = [], None
for name, s in (("baseline (as published)", None), ("1.1 exit scan from the bar after the signal (already true)", None),
                ("1.2 entry at next open + 1 tick", "next_open"), ("1.3 stops +2 ticks, gaps open +1 tick", "stop2"),
                ("1.4 targets need 1 tick through", "through"), ("1.5 look-ahead assertions", "lookahead"),
                ("1.7 market calendar", "calendar")):
    if s: steps.append(s)
    tr = simulate(bars, steps)
    prev = row(name, tr, prev)
    if s == "next_open":
        gaps = sorted((t["entry"] - 0.25 * t["d"] - t["signal_close"]) * t["d"] for t in tr)
        q = lambda f: gaps[int(f * (len(gaps) - 1))]
        print(f"|  next open - signal close (in trade direction, pts): median {q(.5):+.2f}, 10% {q(.1):+.2f}, 90% {q(.9):+.2f}, min {gaps[0]:+.2f}, max {gaps[-1]:+.2f} |||||||")
    if s == "through":
        base = simulate(bars, [x for x in steps if x != "through"])
        tg = [t for t in base if t["reason"] == "target"]
        print(f"|  targets in previous step: {len(tg)}; exact touches (would not fill): {len(tg) - sum(1 for t in tr if t['reason']=='target')} approx |||||||")
print(f"look-ahead assertions passed on {Checked.checks} signals")
json.dump([{k: v for k, v in t.items() if k != "sig"} | {"w": t["sig"]["w"], "ny_move": t["sig"]["ny_move"], "t": t["sig"]["t"]} for t in tr],
          open("/tmp/claude-0/-home-user-2/d0bd11b9-f841-533e-963d-b8a3ecf3e482/scratchpad/audit_corrected.json", "w"))
