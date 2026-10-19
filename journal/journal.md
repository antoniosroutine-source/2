# Trading Journal

Every trade gets a row in `trades.csv` and an entry here. Bot trades and manual trades are kept apart
(see the `source` column).

## Running totals (closed trades)

| Trades | Wins | Losses | Win rate | Net | Total R | Avg R |
|-------:|-----:|-------:|---------:|----:|--------:|------:|
| 1 | 1 | 0 | 100% | +$151.56 | +2.75R | +2.75R |

**By setup**

| Setup | Trades | Wins | Net | Total R |
|---|---:|---:|---:|---:|
| HTF rejection block + 15s bearish IFVG | 1 | 1 | +$151.56 | +2.75R |

---

## #2 · Sun Oct 18, 2026 · MCL short · OPEN ⏳

| | |
|---|---|
| Account | $50K Express (funded) |
| Contract | MCLZ26 (Micro Crude Oil, Dec '26), 15-minute chart |
| Source | Manual (not bot P&L) |
| Session | Sunday evening reopen. This counts toward Topstep's **Mon Oct 19** trading day |
| Side / size | **Short 6** contracts |
| Entry | **98.17**, taken between the 98.27 and 98.10 lines after the Sunday open dropped from Friday's ~99 highs |
| Stop | Moved to 98.16, one tick past entry (about +$6 before fees, about −$4 after ~$10 of round-trip fees). Original stop: not recorded yet |
| Target | **95.32** (2.85 pts) → **+$1,709** gross, back to where Friday's rally started |
| Status at 8:14 PM ET Sun | Price 97.62, **+$329 open**, through the 98.10 and 97.84 lines. Next line down: 96.97 |
| Odds (no-edge baseline) | About **19%** target before stop from 97.62; about 13–20% once Topstep's 3:10 PM CT Monday flat time is counted |
| Context | Topstep's Tilt showed 81% of traders short crude (a crowded side) |

**Result:** *(pending; send the closing screenshot)*

---

## #1 · Mon Oct 12, 2026 · MNQ short · full take profit ✅

| | |
|---|---|
| Account | $50K Express (funded) |
| Contract | MNQZ26 (Micro Nasdaq, Dec '26) |
| Source | Manual (not bot P&L) |
| Session | Evening reopen. This counts toward Topstep's **Tue Oct 13** trading day (the day rolls at 5:00 PM CT) |
| Time | Entry ~6:25 PM ET, exit ~7:00 PM ET (read off the charts), about 35 minutes in the trade |
| Side / size | **Short 2** contracts (two 1-lot fills at 30,897.50) |
| Entry | **30,897.50** |
| Stop | 30,911.50 (14 pts above entry) → risk **$56** |
| Target | **30,859.00** (38.5 pts below entry) |
| Exit | 30,859.00, target filled |
| Result | **+38.5 pts · +$154.00 gross · −$2.44 fees · +$151.56 net · +2.75R** |

### Setup: HTF rejection block + 15s bearish IFVG

In my words:
> When coming into the market for the trading session I marked out a five minute / HTF rejection block
> that I saw and set my top at SSL / Resistance shortly after a 15 sec bearish IFVG formed and trade went
> straight to take profit

Step by step:
1. **Before the session:** marked a 5-minute / HTF rejection block, roughly 30,904.75 to 30,911. At the
   time only the blue line at 30,904.75 (the bottom of the block) was on the chart; the purple box was
   drawn afterwards to show it.
2. **Trigger:** a 15-second bearish IFVG formed as price rejected the block.
3. **Entry:** short 2 at 30,897.50, 7.25 pts under the blue line.
4. **Stop:** 30,911.50, just above the top of the block. Back above the block means the idea is wrong.
5. **Target:** 30,859.00, below the lower purple zone (about 30,870 to 30,874) and the BE mark near 30,876.
6. **Result:** price went straight to the target.

### What went right
- The level was marked before the session. The trade was planned, not chased.
- Waited for a trigger (the 15s bearish IFVG) instead of shorting the level blind.
- The stop sat where the idea is proven wrong (above the block), not at an arbitrary distance.
- 2.75 : 1 reward-to-risk. At that ratio a setup only has to win about 27% of the time to break even.
- Bracket on, hands off until the target filled.
- Personal lockout was on after the win, so the day ended green with nothing given back.

### My notes *(fill in, or send them to Claude to add)*
- How I felt during the trade:
- What I'd repeat / change:
