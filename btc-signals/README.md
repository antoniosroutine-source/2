# BTC RSI short signals

Watches BTC-USD (Coinbase public prices, no account or key) and alerts you when RSI nears and reaches
overbought, with a suggested stop and target. **It never trades.** You place trades yourself (e.g. on
trench.io, whose terms forbid bots controlling your account).

## Run (Windows)
```
cd "C:\Users\you\Downloads\btc-signals\btc-signals"
python bot.py
```
Open http://127.0.0.1:8768/ and click the page once so alerts can beep. No `pip install` needed.

Options (type before `python bot.py`):
- `set BTC_TF=15`: candle size in minutes (1, 5, 15, 60; default 5)
- `set BTC_MODE=turn`: wait for RSI to turn back below 70 instead of shorting at 70
- `set BTC_NTFY_TOPIC=some-secret-name`: phone alerts through the free ntfy app

## Signals
- **WATCH**: the live RSI(14) of the forming candle rises through **65**: overbought is near.
- **SHORT**: a candle **closes** with RSI **>= 70** (mode `touch`, default). Signals use closed candles only,
  so they never disappear afterwards.
- One signal per overbought run: it re-arms after RSI closes below **50**.
- Each SHORT shows: entry (the close), **stop** = highest high of the last 10 candles + 0.25 ATR,
  **target** = 1.5x the stop distance below entry, an expiry (24 candles), and the **max multiplier**:
  with a payout multiplier M your stake is gone after a 1/M move, so above that number price can never
  reach the stop before the stake is wiped out.
- The page follows each signal to its stop, target or expiry and logs everything to `signals.jsonl`.

## Backtest (BTCUSDT 1-minute history Oct 2024 - Sep 2026; `python backtest.py DATA_DIR`)
Entry at the next candle's open with the alerted stop and target; stop checked first; R = profit / stop distance.

| version | signals | win | R before costs | R after 0.04% costs | median stop |
|---|---|---|---|---|---|
| 1m touch | 74/wk | 42% | +0.05 | -2.62 | 0.03% |
| **5m touch (default)** | 13/wk | 43% | **+0.06** (t 1.8) | -0.55 | 0.09% |
| 5m turn | 13/wk | 40% | -0.04 | -0.27 | 0.25% |
| 15m touch | 4.5/wk | 39% | -0.03 | -0.32 | 0.18% |
| 15m turn | 4.5/wk | 44% | -0.03 | -0.13 | 0.47% |
| 60m touch | 1.3/wk | 38% | -0.10 | -0.22 | 0.38% |
| 60m turn | 1.2/wk | 46% | -0.03 | -0.08 | 0.90% |

Placebo (random shorts with the same stop/target rules): the 5m touch signal beat 92% of them; none of
the others beat random. **Conclusion: shorting overbought RSI on BTC had no reliable edge in 2024-2026.**
On short candles the stop is so close (0.03-0.09%) that trading costs decide the result. Treat the
alerts as a heads-up for your own read, not as trades to take blindly.
