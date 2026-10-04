"""Asia sweep: wait 5-10 minutes after the 7-8pm range before entering. Usage: python delay.py bars.csv"""
import math, statistics as S, sys
import engine, strategy
STEPS = ["next_open", "stop2", "through", "lookahead", "calendar"]

class Delay(engine.Checked):
    delay = 60          # minutes from 19:00 before a sweep may trigger
    def on_bar(self, b, agg, walls=None, live=True):
        _, mos = strategy.asia_clock(b.t)
        early = self.p.ASIA_RANGE_MIN <= mos < self.delay
        was_done = self.done
        r = super().on_bar(b, agg, walls, live)
        if early and not was_done and self.done and "trend" not in self.note and "narrow" not in self.note \
                and "incomplete" not in self.note and "weekend" not in self.note:
            self.done = False          # a sweep inside the waiting time is ignored, not taken
            self.note = "waiting before entries"
            return None
        return r

def cls(delay):
    return type(f"Delay{delay}", (Delay,), {"delay": delay})

bars = engine.load_bars(sys.argv[1])
print("| version | trades | win | exp R | t | net $ |\n|---|---|---|---|---|---|")
for lab, c, over in (("current (range 7:00-8:00, enter from 8:00)", engine.Checked, {}),
                     ("A: range 7:00-8:05", engine.Checked, {"ASIA_RANGE_MIN": 65}),
                     ("A: range 7:00-8:10", engine.Checked, {"ASIA_RANGE_MIN": 70}),
                     ("B: range 7-8, no entry before 8:05", cls(65), {}),
                     ("B: range 7-8, no entry before 8:10", cls(70), {})):
    tr = engine.simulate(bars, STEPS, p=engine.params(**over), strat_cls=c)
    R = [t["R"] for t in tr]; n = len(R); m = S.mean(R); sd = S.stdev(R)
    print(f"| {lab} | {n} | {sum(r > 0 for r in R)/n:.0%} | {m:+.3f} | {m/(sd/math.sqrt(n)):.2f} | {sum(t['pnl'] for t in tr):+,.0f} |", flush=True)
