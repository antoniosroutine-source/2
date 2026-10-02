# Pre-registration: council 2 finalists, held-out validation

Date: 2026-10-02. Written before any finalist is run on held-out data. Not edited afterwards.

Design data used by the council: Nasdaq-100 CFD 1-minute bars 2021-04-01 .. 2024-08-31.
Held-out data (each finalist runs ONCE, rules frozen as in the files below):
  A. 2018-01-01 .. 2021-03-31   (Dukascopy, same source)
  B. 2024-09-01 .. 2026-09-29   (Dukascopy; used for the old Asia bot, never for these strategies)
Lockbox, not opened: 2016-01-01 .. 2017-12-31.
Same audited fills as the design harness (next-bar open +1 tick, stop +2 ticks, targets 1 tick through).

Finalists (one primary per member, plus the quant's second as it is a different mechanism):
  1. trend/finalist_1.py   noise-area momentum, 0.10 ATR stop, 0.5 ATR chandelier trail, exit 16:00
  2. quant/finalist_1.py   failed 15-min opening-range breakout, held to 16:05
  3. quant/finalist_2.py   30-min opening-range breakout, stop at midpoint, held to 16:05
  4. trader/finalist_1.py  London sweep of the Asia range + structure shift, target opposite extreme

PASS for a finalist (all required):
  - pooled held-out (A+B) expectancy >= +0.10R and t >= 1.5
  - expectancy > 0 in both A and B
  - Bonferroni across the 4 finalists: one-sided p x 4 <= 0.10
The user's goal of +1.0R per trade is recorded; the council expects +0.05 to +0.3R. A finalist that
fails is dropped, not tweaked.
