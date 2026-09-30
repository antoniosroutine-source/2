# TopstepX bot: level sweep

An automated MNQ bot for a **Topstep 50K Express** account through the **ProjectX API**. It trades the
way the trader described: follow the aggression, sell the manipulation high or buy the manipulation low,
and target the next liquidity level. **Paper mode is the default**: it reads the real market and logs what
it would do, without placing orders.

## Setup (Windows)
1. **ProjectX API access:** in TopstepX go to Settings → API. Subscribe on dashboard.projectx.com
   (promo code `topstep` gives 50% off), link it in TopstepX (Settings → API → ProjectX Linking → Add Link),
   then create an API key.
2. Install Python 3.9+ ("Add python.exe to PATH"), open Command Prompt in this `topstepx` folder, then:
   `pip install -r requirements.txt`
3. Set your credentials (they last until the window is closed; never paste them anywhere else):
   ```
   set PX_USERNAME=your_topstepx_username
   set PX_API_KEY=your_api_key
   ```
   If you have several accounts: `set PX_ACCOUNT_ID=the_id` (check.py lists them).
4. `python check.py`: read-only; shows accounts, levels, bias and aggression. Places no orders.
5. `python backtest.py --days 60`: tests the rules on 60 days of real MNQ history.
6. `python bot.py`: **paper mode**. Watch a few sessions in `px_log.jsonl` / the console.
7. `python bot.py --live`: real orders. Stop with Ctrl+C (an open trade keeps its stop and target),
   or create a file named `STOP` in this folder to flatten and exit.

In TopstepX, turn off any **position bracket preset** for the bot's account. The bot places its own stop
and target, and cancels any other working MNQ orders it finds so two sets of exits can never flip the position.

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
8. **Daily limits:** **one losing trade ends the day**; **+$1,500** on the day ends it. Flat by 4:05pm
   (Topstep's cutoff is 4:10pm ET).

## Backtest so far
On 68 scattered days of Nasdaq-100 data (Dukascopy, not MNQ futures) with the strict 20-point stop:
22 trades, 41% wins, +$2,009, average win $897, average loss $504, max drawdown $2,526. **22 trades
proves nothing**; run `backtest.py` on real MNQ history. A simulation of those results on a fresh Express
account (max loss $2,000, trailing until +$2,000) blew the account 44% of the time at $500 risk, and about
30% at $250–300. Risk matters most before the buffer is built.

## Files
| File | Purpose |
|---|---|
| `config.py` | Every setting |
| `strategy.py` | Setup detection, sizing, targets |
| `levels.py` | Session levels, bias, aggression meter |
| `manage.py` | Breakeven/trail and daily limits |
| `projectx.py` | ProjectX REST client |
| `stream.py` | Live tape and order book (SignalR) |
| `bot.py` | Main loop, paper and live brokers |
| `backtest.py` | Backtest on API history or a CSV |
| `check.py` | Read-only connection check |

Tests: `python -m unittest discover -s tests` (run in this folder).
