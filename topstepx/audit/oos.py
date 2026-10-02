"""K3: the frozen Asia bot, corrected fills, on 2018-01-01..2024-08-31 (never used for any decision)."""
import json, math, statistics as S, sys
from statistics import NormalDist
from engine import simulate, load_bars
from levels import et
bars = load_bars(sys.argv[1])
tr = simulate(bars, ["next_open", "stop2", "through", "lookahead", "calendar"])
R = [t["R"] for t in tr]; n = len(R); m = S.mean(R); sd = S.stdev(R); t_ = m / (sd / math.sqrt(n))
print(f"OOS 2018-01..2024-08: n={n} win={sum(r > 0 for r in R)/n:.1%} expR={m:+.3f} sd={sd:.2f} t={t_:.2f} "
      f"95%CI=[{m-1.96*sd/math.sqrt(n):+.3f},{m+1.96*sd/math.sqrt(n):+.3f}] one-sided p={1-NormalDist().cdf(t_):.4f} net=${sum(t['pnl'] for t in tr):,.0f}")
yrs = {}
for t in tr: yrs.setdefault(et(t["opened"]).year, []).append(t)
for y, ts in sorted(yrs.items()):
    r = [t["R"] for t in ts]
    print(f"  {y}: n={len(r)} win={sum(x > 0 for x in r)/len(r):.0%} expR={S.mean(r):+.3f} net=${sum(t['pnl'] for t in ts):,.0f}")
json.dump([{"R": t["R"], "pnl": t["pnl"], "day": t["day"], "side": t["side"], "reason": t["reason"]} for t in tr], open(sys.argv[2], "w"))
