# Pre-registration: NY-open first 5-minute candle vs 12 EMA ("Quant Lab" reel)

Date: 2026-10-03. Written and committed before any backtest of this rule; not edited afterwards.

Source: an Instagram reel (quant_labde): "At the New York open the algo watches the first five-minute
candle. If it closes above the 12 EMA it goes long; if it closes below, it goes short. The algo manages
the position, trails the stop as momentum develops, and stays in the move as long as the momentum
continues." The reel shows the 12 EMA on the 5-minute chart, an initial stop at the first candle's far
end, and a stepped trailing stop that follows the EMA.

## Frozen rules (primary)
- 5-minute bars built from 1-minute bars (New York time); 12-period EMA of 5-minute closes, carried
  across days (seeded from history).
- Signal: the 09:30-09:34 five-minute candle closes at 09:35. Close > EMA(12) at that close -> long;
  close < EMA -> short; equal -> no trade. One trade per day, weekdays, NYSE trading days only.
- Entry: next 1-minute bar's open (09:35) + 1 tick.
- Initial stop: beyond the first candle's far end (long: its low - 1 tick; short: its high + 1 tick).
  Size: whole MNQ so the loss at the stop incl. fees <= $500 (max 20 MNQ); skip if fewer than 1.
- Trailing stop: after each completed 5-minute bar, the stop moves to that bar's EMA(12) value
  -1 tick (long) / +1 tick (short) if that is tighter; it never loosens.
- Exits: stop (fills at stop + 2 ticks, gaps at the open + 1 tick); profit capped at $1,500 per trade
  (Topstep daily cap / consistency); otherwise flat at 15:55 ET.
- Fills as in AUDIT-REPORT.md (exits scanned from the fill bar on; stop checked first).

## Alternates (ambiguity only, reported, not selected)
- A1: trail = previous completed 5-minute bar's low (long) / high (short) instead of the EMA.
- A2: no trail, exit 15:55 (initial stop only).

## Data
Dukascopy USATECHIDXUSD 1-minute BID candles 2018-01-01..2026-09-29 (all available years with a
complete NY session). This exact rule has never been backtested here. Disclosure: the council tested a
related idea on 2021-04..2024-08 (direction of the 09:30-09:44 candle, ATR stop, exit 16:00: +0.25R,
not validated); this rule differs (5-min candle vs EMA, EMA trail).

## Kill criteria (NO EDGE if ANY is true)
K1. Pooled 2018-2026 expectancy <= +0.10R.
K2. Real futures data: UNRESOLVED (none available); the verdict cannot be EDGE SURVIVES without it.
K3. Any of the three segments (2018-01..2021-03, 2021-04..2024-08, 2024-09..2026-09) has expectancy <= 0,
    or the pooled t-stat < 2.0.
K4. The real result is below the 95th percentile of a placebo that takes a RANDOM direction at 09:35
    with the same stop, trail and exits (1,000 runs).
K5. EMA length 9 and 21 and signal candle 09:35-09:39 (second candle): the average of these 3 variants <= 0R.
K6. Bonferroni over the 3 pre-registered versions (primary, A1, A2): p x 3 > 0.05.
