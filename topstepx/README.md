# TopstepX bot: level sweep

An automated MNQ bot for a **Topstep 50K Express** account through the **ProjectX API**. It trades the
way the trader described: follow the aggression, sell the manipulation high or buy the manipulation low,
and target the next liquidity level. **Paper mode is the default**: it reads the real market and logs what
it would do, without placing orders.

## Modes
| Command | What happens |
|---|---|
| `python bot.py` | **Paper** (default). Real market data, no orders. Each signal asks "would you take it?" on the desk page and the answer is logged. |
| `python bot.py --live` | **Live, semi-automatic.** Each trade waits up to 30 s for **Accept** on the desk page. The bot then places it and manages the stop, breakeven and trail. |
| `python bot.py --live --auto` | Live, fully automatic. Only after the checks in "Rollout" below. |

**The desk page** is at http://127.0.0.1:8766/ while the bot runs. It shows the signal, stop, target, risk and a countdown, with Accept/Reject. **My long / My short** buttons log *your* setups with a market snapshot, including the ones the bot misses. Everything goes to `decisions.jsonl`. For phone alerts, install the free **ntfy** app, subscribe to a private topic name, and `set PX_NTFY_TOPIC=that-name`.

**Recording:** while connected, every trade and order-book update is saved to `recordings/YYYY-MM-DD.jsonl.gz`. This builds the real order-flow data needed to test and improve the strategy.

## Setup (Windows)
1. **ProjectX API access:** in TopstepX go to Settings → API. Subscribe on dashboard.projectx.com
   (promo code `topstep` gives 50% off), link it in TopstepX (Settings → API → ProjectX Linking → Add Link),
   then create an API key.
2. **Auto OCO Brackets:** in TopstepX, Settings → Risk Settings, set the bot's account to **Auto OCO Brackets**.
   Each entry then carries a linked stop and target: one filling cancels the other, even if the bot is offline.
3. Install Python 3.9+ ("Add python.exe to PATH"), open Command Prompt in this `topstepx` folder, then:
   `pip install -r requirements.txt`
4. Set your credentials (they last until the window is closed; never paste them anywhere else):
   ```
   set PX_USERNAME=your_topstepx_username
   set PX_API_KEY=your_api_key
   ```
   If you have several accounts: `set PX_ACCOUNT_ID=the_id` (check.py lists them).
5. `python check.py`: read-only; shows accounts, levels, bias and aggression. Places no orders.
6. `python bot.py`: paper mode. Open the desk page and answer the signals.

Stop with Ctrl+C (an open trade keeps its linked stop and target), or create a file named `STOP`
in this folder to flatten and exit. Keep the computer awake (Settings → Power → Sleep: Never).

## Safety built in
- **Linked brackets:** the stop and target are an OCO pair created with the fill, so a fast move can
  never fill both and flip the position. Breakeven/trail moves modify the bracket's stop order.
- **No blind retries on orders:** a lost response never sends a second entry. If an entry's reply is lost,
  the bot watches for the position and adopts it.
- **Reconciliation every loop:** if the position's side or size doesn't match the bot's trade, or no stop is
  working on it, the bot flattens and logs `critical`. A position the bot didn't open is left alone, and the
  bot stands aside. Live start-up refuses to run if an MNQ position or order already exists.
- **Daily rules survive restarts** (`px_state.json`); a loss is counted even if the closing fill is slow to appear.
- **Consistency:** open profit counts toward the +$1,500 daily cap, and the trade is closed when the day reaches it.
- **News:** any open trade is closed at **8:25am ET**, before the 8:30 releases.
- **Stale data:** no entries if the last bar is over 3 minutes old; in confirm mode no entry if price moved
  more than 5 points from the signal.

## Council review (October 2026)
Four independent reviews (quant, prop-firm risk, order-flow trader, systems engineer) agreed:
- **No proven edge yet.** The two-year backtest is +$37/trade with t = 0.7. The five best trades exceed the net.
  The backtest's "aggression" is bar momentum, not the tape.
- **Account risk:** $500 per trade against a $2,000 max loss leaves room for 4 losses; the backtest had 7 in a row.
- **The bot copies the shape of the setup, not the order-flow read at the sweep.** That read is the edge.

## Rollout
1. **Paper + recording, 4–8 weeks.** Answer every signal on the desk and mark your own setups.
2. Rebuild aggression from the recorded tape: a trigger in the seconds around the sweep (selling that fails
   to extend, bids refilling, delta flipping) instead of a 15-minute average. Test it on the recordings.
3. **Go-live bar, set in advance:** out of sample ≥ 150 trades, t ≥ 2 with 2 ticks of slippage, positive in
   ≥ 3 of 4 quarters, ≥ 80% simulated account survival.
4. Then **a separate Combine/Express account, never the main one**, `--live` (confirm mode), **$150–250 risk**
   (`MAX_RISK_USD`; note `MIN_CONTRACTS` must allow the smaller size).
   **Kill rules:** 6 losses in a row, −$1,250 from the peak, win rate under 30% after 30 trades, or any rule email.

## Strategies
Pick with `set PX_STRATEGY=...` before `python bot.py`:
- `level_sweep` (default): the trader's levels + aggression, described below.
- `asia_sweep`: the MFP bot's **Asia range fakeout**. Range 7–8pm NY; fade a sweep of the range that closes
  back inside until 1am; stop beyond the wick (+10% of the range, at least 30 points); target 3.33× the stop,
  capped at $1,500; time exit at 3am. Sized to ≤ $500 at the stop (about 8 MNQ on a 30-point stop).
  **Waits for liquidity** (`ASIA_LIQ_REACH = 0.5`): if an untaken session high/low (NY AM/PM, London,
  previous day) or a big resting order sits within half a range width beyond the range, it fades the sweep
  of that level instead of the range. **No trail** (`ASIA_TRAIL = False`): pure stop and target.
  **Council filters:**
  - `ASIA_NY_BIAS = True`: NY dumps, Asia recovers. If that day's NY session (9:30am-4pm) closed below its
    open, Asia takes longs only; above, shorts only. No NY session (Sunday evening) means no trade. The
    session's first sweep decides: a sweep against the bias ends the session. The bias is logged
    (`"event": "bias"`) and shown on the desk page each evening.
  - `ASIA_MIN_RANGE_PTS = 33`: skip sessions whose 7-8pm range is narrower than 33 points.
  - `COMBINE_TARGET_USD = 3000`: close to passing, the take profit shrinks to what is still needed
    (+$50 and fees), read from the account balance. **Set it to `None` once the account is funded.**
  Two-year backtest (Sep 2024 - Sep 2026): 118 trades, 38% wins, +$20,242 (+$10,722 / +$9,520 by year),
  max drawdown $4,429; 71% of Combines started on a random day pass before hitting the $2,000 MLL
  (expect 55-65% live: small sample). About one trade a week; a Combine takes about 7-8 weeks.

Both strategies: one loss ends the day and the day locks at +$1,500. The Asia sweep takes one trade per
session by design; the level sweep can trade again after a win. (`DAILY_MAX_TRADES = 1` would cap both at one.)

## The rules (`strategy.py`, `manage.py`, `config.py`)
Times are New York time. Entries only in the **Asia session, 7pm–2am**.
1. **Bias (logged):** the NY session's reaction (dump → recovery longs, rally → shorts), the 1-hour trend,
   the last two days, and the liquidity levels: NY AM, NY, London and Asia highs/lows, and the previous day.
2. **Aggression decides direction:** buy minus sell volume over the last 15 minutes. Sellers → shorts only,
   buyers → longs only, neither → no trade. Live, this comes from the real tape (ProjectX tags every trade
   buy or sell). In backtests, and if the live feed drops, it is estimated from 1-minute bars.
3. **Setup (short):** a push to a new low, a consolidation (a swing high after it), then a
   **manipulation**: a bar trades above the consolidation high and closes back below it. The pullback
   must not retrace past where the push started. Longs are the mirror image. Never on a breakout.
4. **Entry:** within 3 bars, a bar closes past the manipulation bar's other end while aggression still agrees.
5. **Stop:** a strict **20 points** (`FIXED_STOP_PTS`). **Size:** as many MNQ as keep the loss at the stop,
   fees included, **≤ $500**: 12 MNQ at 20 points. Minimum 10, maximum 20.
6. **Target:** just past the push low (the new low), or the next liquidity level beyond it: the first that pays
   **≥ $800**, capped at **$1,500** (Topstep consistency). Live, it front-runs big resting orders on the DOM.
7. **Management:** **+$650** → stop to breakeven (+1 tick). **+$785** → trail, keeping **65%** of the best open profit.
8. **Daily limits:** **one losing trade ends the day**; **+$1,500** on the day (open profit included) ends it.
   Flat by **8:25am ET**, before the 8:30 news.

## Backtest so far
Two years of Nasdaq-100 1-minute data (Dukascopy CFD, not MNQ futures; aggression estimated from bars),
Sep 2024 – Sep 2026: 158 trades, 39% wins, **+$5,971**, average win $792, average loss $495, longest losing
streak 7, max drawdown $8,829, best day $1,627. Year 1 lost about $2,100 and year 2 made about $7,900.
Replayed as fresh 50K Express accounts at $500 risk: 38% blown before any payout, 40% paid out then blown,
22% never blown (all of them started in the good second year).

## Files
| File | Purpose |
|---|---|
| `config.py` | Every setting |
| `strategy.py` | Setup detection, sizing, targets |
| `levels.py` | Session levels, bias, aggression meter |
| `manage.py` | Breakeven/trail and daily limits |
| `projectx.py` | ProjectX REST client |
| `stream.py` | Live tape and order book (SignalR), recording |
| `bot.py` | Main loop, paper and live brokers, reconciliation |
| `ui.py` | Desk page: alerts, Accept/Reject, "my setup" marks |
| `backtest.py` | Backtest on API history or a CSV |
| `check.py` | Read-only connection check |
| `test_trade.py` | One-MNQ live execution test: linked stop/target, stop move, close (max ~$40 risk) |

Tests: `python -m unittest discover -s tests` (run in this folder).
