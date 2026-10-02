"""2.7 Combine statistics and 2.8 power on corrected trades. Usage: python combine.py corrected.json"""
import json, math, random, statistics as S, sys
tr = json.load(open(sys.argv[1])); sm = json.load(open(sys.argv[1] + ".summary"))
rnd = random.Random(7)
RISK = S.mean(t["risk"] for t in tr)
winR = [t["R"] for t in tr if t["pnl"] > 0]; lossR = [t["R"] for t in tr if t["pnl"] <= 0]
k, n = sm["k"], sm["n"]
def combine(seq, target=3000, mll=2000):
    bal = hw = 0.0
    for i, x in enumerate(seq):
        bal += x; hw = max(hw, bal)
        if bal <= min(hw, mll) - mll: return "blown", i + 1
        if bal >= target: return "passed", i + 1
    return "open", len(seq)
# a) independent Combines, back to back on the real sequence
days = {}
for t in tr: days[t["day"]] = days.get(t["day"], 0) + t["pnl"]
seq = [v for _, v in sorted(days.items())]
i, res = 0, []
while i < len(seq):
    r, used = combine(seq[i:])
    if r == "open": break
    res.append(r); i += used
ps = res.count("passed"); m_ = len(res)
z = 1.96; ph = ps / m_; c = (ph + z*z/(2*m_)) / (1 + z*z/m_); h = z*math.sqrt(ph*(1-ph)/m_ + z*z/(4*m_*m_)) / (1 + z*z/m_)
print(f"## 2.7a independent back-to-back Combines on the real sequence: {ps} passed of {m_} ({ph:.0%}), Wilson 95% CI [{c-h:.0%}, {c+h:.0%}]")
def draw_combine(p):
    s = []
    while True:
        x = (rnd.choice(winR) if rnd.random() < p else rnd.choice(lossR)) * RISK
        s.append(x)
        r, u = combine(s)
        if r != "open": return r, u
# b) parameter uncertainty
probs = []
for _ in range(2000):
    p = rnd.betavariate(k + 1, n - k + 1)
    probs.append(sum(draw_combine(p)[0] == "passed" for _ in range(100)) / 100)
probs.sort()
print(f"## 2.7b Monte Carlo, win rate ~ Beta({k+1},{n-k+1}), payoffs bootstrapped: pass rate mean {S.mean(probs):.1%}, 95% interval [{probs[50]:.0%}, {probs[1950]:.0%}]")
be = sm["al"] / (sm["aw"] + sm["al"])
z0 = [draw_combine(be)[0] == "passed" for _ in range(20000)]
print(f"## 2.7c zero-edge benchmark (win rate {be:.1%}, same payoffs): pass rate {sum(z0)/len(z0):.1%}")
# d) dollars per Combine fee: Standard path $49/month, $149 activation on pass; Express: payout after
# 5 winning days of $150+, up to 50% of balance capped at $2,000, 90% to the trader; $2,000 MLL; funded phase up to 52 weeks
TPW = len(tr) / 104.0
def funded(p, weeks=52):
    bal = hw = 0.0; floor = -2000.0; wd = 0; paid = 0.0; locked = False
    for _ in range(int(weeks * TPW)):
        x = (rnd.choice(winR) if rnd.random() < p else rnd.choice(lossR)) * RISK
        bal += x; hw = max(hw, bal)
        if not locked: floor = min(0.0, hw - 2000)
        if bal <= floor: return paid
        if x >= 150: wd += 1
        if wd >= 5 and bal > 0:
            amt = min(0.5 * bal, 2000.0); paid += 0.9 * amt; bal -= amt; wd = 0; locked = True; floor = 0.0   # after a payout the MLL sits at the starting balance
    return paid
def ev(p, runs=4000):
    tot = []
    for _ in range(runs):
        r, used = draw_combine(p)
        months = max(1, math.ceil(used / TPW / 4.33))
        cost = 49 * months + (149 if r == "passed" else 0)
        tot.append((funded(p) if r == "passed" else 0.0) - cost)
    return S.mean(tot)
evs = sorted(ev(rnd.betavariate(k + 1, n - k + 1), 200) for _ in range(300))
print(f"## 2.7d expected $ per Combine attempt (net of $49/mo fees, $149 activation, 1 year funded, 90% split): "
      f"point {ev(k/n):+,.0f}; with win-rate uncertainty mean {S.mean(evs):+,.0f}, 95% interval [{evs[7]:+,.0f}, {evs[292]:+,.0f}]; zero edge {ev(be):+,.0f}")
need = (2 * sm["sd"] / sm["m"]) ** 2
print(f"## 2.8 power: trades to tell {sm['m']:+.3f}R (sd {sm['sd']:.2f}R) from zero at 2 sigma: {need:.0f} trades = {need/TPW:.0f} weeks at {TPW:.2f} trades/week")
