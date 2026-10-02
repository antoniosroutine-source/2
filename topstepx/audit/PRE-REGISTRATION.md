# Pre-registration: Asia sweep backtest audit

Date: 2026-10-02
Frozen strategy commit: 9fbb41d59bb3c7560d5e16d75774ea2ff2322d29 (branch claude/clever-bell-a5rm17)
Strategy under test: topstepx `asia_sweep` with the config at that commit (ASIA_NY_BIAS on,
ASIA_MIN_RANGE_PTS 33, ASIA_LIQ_REACH 0.5, ASIA_RR 3.33, ASIA_MIN_STOP_PTS 30, ASIA_BUF_FRAC 0.1,
no trail, $500 max risk, one trade per session, one loss ends the day).
This file is not edited after it is committed.

## Rules of the audit
- Fixes in Parts 1-2 change measurement only (fills, look-ahead, timestamps, data, statistics),
  never entry/exit rules, parameters or filters. A fix that hurts the result is kept.
- R for a trade = P&L / planned risk (contracts x stop distance x $2 + fees), stop distance as
  placed by the bracket from the fill.
- Corrected fill model: exits are scanned from the bar after the fill bar; entry at the next
  bar's open + 1 tick; the bracket stop and target keep the signal's distances from the fill;
  stops fill at stop + 2 ticks, gaps at the bar open + 1 tick adverse; targets fill only when
  price trades 1 tick through.

## Data
- In-sample (used for rule selection, years 1 and 2): Dukascopy USATECHIDXUSD 1-minute BID
  candles, 2024-09-01 to 2026-09-29.
- Lockbox (Part 5; not opened during this audit): 2016-01-01 to 2017-12-31.
- Out-of-sample for Part 3.1: 2018-01-01 to 2024-08-31, same source. Not used for any decision so far.
- Real NQ/MNQ futures trade bars (1.8) are not available in this environment (no Databento key;
  the ProjectX key is only on the trader's machine). If they cannot be obtained, K2 is
  UNRESOLVED and the verdict cannot be EDGE SURVIVES.
- Live parity (1.10) needs the bot's px_log.jsonl; without it, 1.10 is reported as not done.

## Kill criteria (verdict NO EDGE if ANY is true)
K1. Corrected in-sample expectancy (end of Part 1) is <= +0.10R.
K2. Futures-data t-stat is < 1.0.
K3. Pre-2024 out-of-sample expectancy is <= 0, or its t-stat is < 1.0.
K4. The real result is below the 95th percentile of random-entry placebo 3.2a.
K5. The offset-clock variants average <= 0R.
K6. The multiple-testing-adjusted p is > 0.05, or the Deflated Sharpe is < 0.95.
EDGE SURVIVES only if all six pass.
