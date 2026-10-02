"""Part 2 statistics on the corrected trades. Usage: python stats.py corrected.json unfiltered.json"""
import json, math, random, statistics as S, sys
from statistics import NormalDist
N01 = NormalDist()
tr = json.load(open(sys.argv[1])); un = json.load(open(sys.argv[2]))
R = [t["R"] for t in tr]; n = len(R); m = S.mean(R); sd = S.stdev(R); se = sd / math.sqrt(n)
rnd = random.Random(42)
def boot(xs, B=10000, blk=1):
    out = []
    for _ in range(B):
        s = []
        while len(s) < len(xs):
            i = rnd.randrange(len(xs)); s += xs[i:i + blk] if i + blk <= len(xs) else (xs[i:] + xs[:blk - (len(xs) - i)])
        out.append(S.mean(s[:len(xs)]))
    out.sort(); return out[int(.025 * B)], out[int(.975 * B)], sum(x <= 0 for x in out) / B
print("## 2.1 Expectancy (R per trade)")
print(f"n={n} mean={m:+.3f}R sd={sd:.2f}R SE={se:.3f} t={m/se:.2f} analytic 95% CI [{m-1.96*se:+.3f}, {m+1.96*se:+.3f}] one-sided p={1-N01.cdf(m/se):.4f}")
for blk in (1, 5, 10):
    lo, hi, p0 = boot(R, blk=blk)
    print(f"bootstrap blocks of {blk}: 95% CI [{lo:+.3f}, {hi:+.3f}], share of resamples <= 0: {p0:.3f}")
wins = [t for t in tr if t["pnl"] > 0]; k = len(wins)
z = 1.96; ph = k / n
c = (ph + z*z/(2*n)) / (1 + z*z/n); h = z * math.sqrt(ph*(1-ph)/n + z*z/(4*n*n)) / (1 + z*z/n)
aw = S.mean(t["R"] for t in wins); al = -S.mean(t["R"] for t in tr if t["pnl"] <= 0)
print(f"\n## 2.2 Win rate {k}/{n} = {ph:.1%}, Wilson 95% CI [{c-h:.1%}, {c+h:.1%}]; avg win {aw:+.2f}R, avg loss {-al:+.2f}R; breakeven win rate {al/(aw+al):.1%}")
res = [t for t in tr if t["reason"] in ("target", "stop")]
hits = sum(t["reason"] == "target" for t in res)
p0s = [t["stop_pts"] / (t["stop_pts"] + abs(t["target"] - t["entry"])) for t in res]
mu = sum(p0s); var = sum(p * (1 - p) for p in p0s)
zb = (hits - mu) / math.sqrt(var)
te = sum(t["pnl"] for t in tr if t["reason"] not in ("target", "stop")); net = sum(t["pnl"] for t in tr)
tg = [t["pnl"] for t in tr if t["reason"] == "target"]; st = [t["pnl"] for t in tr if t["reason"] == "stop"]
swing = S.mean(tg) - S.mean(st)
print(f"\n## 2.3 Random-walk benchmark: {hits} targets of {len(res)} resolved = {hits/len(res):.1%}; driftless expectation {mu/len(res):.1%} (z={zb:.2f}, one-sided p={1-N01.cdf(zb):.4f})")
print(f"time/other exits: {n-len(res)} trades, ${te:,.0f} of ${net:,.0f} net ({te/net:.0%}); target->stop flips to reach zero P&L: {net/swing:.1f}")
# 2.5 multiple testing
NT = 440
p1 = 1 - N01.cdf(m / se)
print(f"\n## 2.5 Multiple testing: documented variants on this data >= {NT}. Holm/Bonferroni-adjusted p for the selected variant = min(1, {p1:.4f} x {NT}) = {min(1, p1*NT):.3f}")
sr = m / sd; g3 = sum(((x - m) / sd) ** 3 for x in R) / n; g4 = sum(((x - m) / sd) ** 4 for x in R) / n
emc = 0.5772156649
for N in (20, 100, NT):
    sr0 = math.sqrt(1 / (n - 1)) * ((1 - emc) * N01.inv_cdf(1 - 1 / N) + emc * N01.inv_cdf(1 - 1 / (N * math.e)))
    dsr = N01.cdf((sr - sr0) * math.sqrt(n - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr * sr))
    print(f"Deflated Sharpe (per-trade SR {sr:.3f}, skew {g3:.2f}, kurt {g4:.2f}) with N={N} trials: SR0={sr0:.3f}, DSR={dsr:.3f}")
# 2.6 filters vs random removal and permutation
def keep_rule(t, bias=True, wmin=33.0, sign=None):
    mv = t["ny_move"] if sign is None else sign
    if mv is None: return False
    s = 1 if t["side"] == "long" else -1
    return (not bias or mv * s < 0) and t["w"] >= wmin
base = un; kept = [t for t in base if keep_rule(t)]
def stat(ts): return S.mean(t["R"] for t in ts) if ts else -9
real = stat(kept)
print(f"\n## 2.6 Filters (unfiltered corrected run: {len(base)} trades; kept by NY bias + 33-pt range: {len(kept)}, mean {real:+.3f}R; check vs 2.1 n={n})")
for name, rule in (("NY bias only", lambda t: keep_rule(t, True, 0)), ("33-pt range only", lambda t: keep_rule(t, False, 33)), ("both", keep_rule)):
    ks = [t for t in base if rule(t)]; r0 = stat(ks)
    sims = sorted(stat(rnd.sample(base, len(ks))) for _ in range(1000))
    pct = sum(x < r0 for x in sims) / 10
    print(f"{name}: kept {len(ks)}, mean {r0:+.3f}R, percentile vs 1,000 random removals: {pct:.1f}")
nights = [t["ny_move"] for t in base]
perm = []
for _ in range(1000):
    sh = nights[:]; rnd.shuffle(sh)
    perm.append(stat([t for t, mv in zip(base, sh) if keep_rule(t, True, 33, sign=mv)]))
perm.sort()
print(f"bias-label permutation (1,000 shuffles, range filter kept): real {real:+.3f}R at percentile {sum(x < real for x in perm)/10:.1f}")
json.dump({"m": m, "sd": sd, "n": n, "aw": aw, "al": al, "k": k, "R": R}, open(sys.argv[1] + ".summary", "w"))
