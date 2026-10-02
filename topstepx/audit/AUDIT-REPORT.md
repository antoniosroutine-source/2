# Asia sweep audit: report

Pre-registration: PRE-REGISTRATION.md (commit f4609f5). Frozen strategy: commit 9fbb41d.
Data: Dukascopy USATECHIDXUSD 1-minute BID candles (Nasdaq-100 CFD). Scripts: this folder.

## 1. Bias-correction ledger (in-sample 2024-09..2026-09)

| fix | trades | win% | exp R | t | net $ | change |
|---|---|---|---|---|---|---|
| baseline (as published) | 118 | 38.1% | +0.354 | 2.13 | +20,362 | |
| 1.1 exit scan from the bar after the signal (already true) | 118 | 38.1% | +0.354 | 2.13 | +20,362 | +0 |
| 1.2 entry at next open + 1 tick | 118 | 38.1% | +0.355 | 2.13 | +20,401 | +39 |
| 1.3 stops +2 ticks, gaps open +1 tick | 118 | 38.1% | +0.350 | 2.10 | +20,125 | -276 |
| 1.4 targets need 1 tick through (0 of 31 were exact touches) | 118 | 38.1% | +0.350 | 2.10 | +20,125 | +0 |
| 1.5 look-ahead assertions (944 signals, none failed) | 118 | 38.1% | +0.350 | 2.10 | +20,125 | +0 |
| 1.6 timezones: 19:00 ET = 23:00Z (summer) / 00:00Z (winter) at every US and EU change; OK | | | | | | |
| 1.7 market calendar (NYSE holidays, early closes, CME closure eves) | 113 | 37.2% | +0.331 | 1.94 | +18,405 | -1,720 |
| 1.8 real NQ futures bars | not available in this environment | | | | | UNRESOLVED |
| 1.9 gamma timing: snapshots computed ~12:30 ET from prior-day OI, used after 19:00; no shift needed | | | | | | |
| 1.10 live parity | needs the bot's px_log.jsonl | | | | | NOT DONE |
| 1.11 Topstep rules: consistency target is 55% of the profit target ($1,650 on 50K); a $1,500 best day passes | | | | | | |

Next open - signal close (trade direction): median -0.08 pts, 10%-90% -0.67..+0.60 (CFD; futures will differ).

## 2. Statistics (corrected in-sample, n = 113)

- Expectancy +0.331R, sd 1.81R, t 1.94, 95% CI [-0.003, +0.665]; bootstrap CIs (blocks 1/5/10): [+0.002, +0.664] / [+0.043, +0.627] / [+0.078, +0.586].
- Win rate 37.2%, Wilson CI [28.8%, 46.4%]; avg win +2.58R, avg loss -1.00R; breakeven win rate 27.9%.
- Random-walk benchmark: 30 targets of 99 resolved (30.3%) vs 24.3% driftless (z 1.40, p 0.081). Time exits = 41% of net. 9.3 target->stop flips erase the P&L.
- Year 2 used for selection: everything in-sample. Multiple testing: >= 440 documented variants; Bonferroni/Holm p = 1.0; Deflated Sharpe 0.12 (N=440), 0.26 (N=100), 0.51 (N=20).
- Filters vs 1,000 random removals: NY bias 89.7th pct, 33-pt range 98.3th, both 97.4th; bias-label permutation 84.5th pct.
- Combine: independent back-to-back Combines 7/10 passed (Wilson [40%, 89%]); Monte Carlo with Beta(43,72) win rate 57.8% [32%, 82%]; zero-edge benchmark 30.7%.
- Expected $ per Combine attempt (Standard path $49/mo, $149 activation, 1 year funded, 90% split): +$1,456 point, +$1,846 mean [+$119, +$4,962] with win-rate uncertainty; zero edge +$92.
- Power: 120 trades (about 111 weeks) to separate +0.33R from zero at 2 sigma.

## 3. Falsification

| test | result | kill criterion | |
|---|---|---|---|
| corrected in-sample expectancy | +0.331R | K1 > +0.10R | PASS |
| futures-data t-stat | no futures data | K2 t >= 1.0 | UNRESOLVED |
| out-of-sample 2018-01..2024-08 | n=97, +0.077R, t=0.49, CI [-0.23, +0.38], net +$3,488; 2018 +0.10 (3), 2019 -0.52 (2), 2020 +0.28 (27), 2021 -0.23 (15), 2022 +0.36 (31), 2023 -0.23 (7), 2024 -0.46 (12) | K3 exp > 0 and t >= 1.0 | **FAIL** |
| random-entry placebo (3.2a) | real at 99.3th percentile (placebo mean +0.011R, 95th +0.240R) | K4 >= 95th | PASS |
| fake levels (3.2b, core engine) | real +0.282R at 77.5th percentile | info | |
| NY bias reversed (3.2c) | +0.089R (106 trades) | info | |
| offset clock (11 variants) | average +0.307R; range window 18:45 +0.17, 19:15 +0.19, 19:30 +0.45 | K5 > 0R | PASS |
| multiple testing | adjusted p 1.0, DSR 0.12 | K6 p <= 0.05 and DSR >= 0.95 | **FAIL** |
| parameter surface | min range 20-50 pts all +0.19..+0.33R (peak at 33); target 2.0-4.5x +0.20..+0.35R; buffer 0-25% +0.33..+0.42R | info | plateau |

Note on K3: the fixed 33-point minimum range admits few sessions when the index was at 6,000-12,000 (2018-2019: 5 trades), as the reviewer predicted. Per the pre-registration this is reported, not fixed.

## 4. Verdict

**NO EDGE** - deciding criteria K3 (out-of-sample t 0.49) and K6 (multiple testing). K2 unresolved.

## 5. Realistic Combine expectation

In-sample with parameter uncertainty: about +$1,800 per attempt [+$119, +$4,962]; but the out-of-sample
expectancy (+0.08R, CI including zero) is close to the zero-edge case, where the estimate is about +$90 per
attempt (Combine geometry plus funded payouts, not edge). Treat the expected value as roughly break-even.
