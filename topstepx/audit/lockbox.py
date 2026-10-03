"""K3 for the long-only pre-registration: the lockbox 2016-01-01..2017-12-31, run once."""
import json, math, statistics as S, sys
from statistics import NormalDist
from engine import simulate, load_bars
from levels import et
bars = load_bars(sys.argv[1])
tr = simulate(bars, ["next_open", "stop2", "through", "lookahead", "calendar"], accept=lambda s: s["side"] == "long")
R = [t["R"] for t in tr]; n = len(R)
if n < 3:
    print(f"LOCKBOX long-only: n={n} (too few)"); sys.exit()
m = S.mean(R); sd = S.stdev(R); t_ = m / (sd / math.sqrt(n))
print(f"LOCKBOX 2016-2017 long-only: n={n} win={sum(r > 0 for r in R)/n:.1%} expR={m:+.3f} sd={sd:.2f} t={t_:.2f} "
      f"95%CI=[{m-1.96*sd/math.sqrt(n):+.3f},{m+1.96*sd/math.sqrt(n):+.3f}] p={1-NormalDist().cdf(t_):.4f} net=${sum(t['pnl'] for t in tr):,.0f}")
for y in (2016, 2017):
    ys = [t for t in tr if et(t["opened"]).year == y]
    if ys: print(f"  {y}: n={len(ys)} expR={S.mean(t['R'] for t in ys):+.3f} net=${sum(t['pnl'] for t in ys):,.0f}")
for t in tr: print(f"    {et(t['opened']):%Y-%m-%d %H:%M} {t['side']} {t['reason']} R={t['R']:+.2f}")
