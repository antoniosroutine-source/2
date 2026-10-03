# Long-only Asia sweep: results (pre-registration LONGONLY-PREREG.md, commit 3053f7a)

| criterion | result | status |
|---|---|---|
| K1 in-sample expectancy > +0.10R | n=54, +0.265R, t=1.05, +$7,116 | PASS |
| K2 futures-data t >= 1.0 | no futures data | UNRESOLVED |
| K3 lockbox 2016-2017 | 0 trades: the Dukascopy CFD has no quotes between 17:00 and 02:00 ET in 2016-2017 (bars only 02:00-16:59 ET), so the Asia session cannot be tested there | INCONCLUSIVE (< 20 trades) |
| K4 random long-entry placebo | real +0.265R at the 96.6th percentile (placebo mean -0.073R, 95th +0.219R) | PASS |
| K5 offset clocks (11 variants) | average +0.248R; range 18:45 +0.08, 19:15 -0.02, 19:30 +0.40; cutoffs +0.27..+0.29; exits +0.25..+0.33 | PASS |
| K6 multiple testing (N=441) | one-sided p 0.148, Bonferroni 1.0, Deflated Sharpe 0.018 | FAIL |

Verdict: **NO EDGE** (deciding criterion K6; K2 unresolved; K3 inconclusive because the lockbox has no
overnight data). Clean evidence for or against the long-only variant now needs either real NQ futures
history (which also covers K2) or forward trading results.
