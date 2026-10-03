# Pre-registration: Asia sweep, LONGS ONLY

Date: 2026-10-03. Written and committed before the tests below are run; not edited afterwards.
Strategy: the frozen Asia sweep (commit 9fbb41d rules; current bot 84ae97e trades the same rules) with
one change: short signals are not taken (the first sweep still decides the session, so a short sweep
ends the night with no trade). Same corrected fill model as AUDIT-REPORT.md.

Disclosure: the idea came from looking at the 2018-2024 out-of-sample results of the full strategy
(longs +0.33R, shorts -0.21R) and the in-sample split (longs +0.27R). Those periods are therefore no
longer clean for this variant. The only untouched data is the lockbox, 2016-01-01..2017-12-31.

Kill criteria (verdict NO EDGE if ANY is true):
K1. Corrected in-sample (2024-09..2026-09) long-only expectancy <= +0.10R. (Already known: +0.265R.)
K2. Futures-data t-stat < 1.0. (No futures data here: UNRESOLVED; the verdict cannot be EDGE SURVIVES.)
K3. LOCKBOX 2016-01-01..2017-12-31 long-only expectancy <= 0 or t < 1.0. If the lockbox has fewer than
    20 long trades, K3 is reported as INCONCLUSIVE, which also blocks EDGE SURVIVES.
K4. In-sample long-only result below the 95th percentile of a random-entry placebo (random entry bar,
    long only, same eligible nights, window, stop distances and sizing; 1,000 runs).
K5. The 11 offset-clock variants (range 18:45/19:15/19:30; entry cutoff 00:30/00:45/01:15/01:30; time
    exit 02:30/02:45/03:15/03:30), long-only, in-sample, average <= 0R.
K6. Multiple-testing: Bonferroni p over N = 441 variants > 0.05, or Deflated Sharpe (N = 441) < 0.95.
EDGE SURVIVES only if all six pass.
