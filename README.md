# MFP Trading Bot: ICT Silver Bullet

This is an automated trading bot for a **MyFundedPerps Prime ($25K)** account. It trades the **Nasdaq-100 perpetual (NAS100)** using the ICT Silver Bullet strategy. A local dashboard gives you a master kill switch, a toggle for each strategy, risk bars and a trade log.

The API details come from the First Tick Trading build guide. Nothing here has been checked against MFP's official docs or tested on the live API yet. **Test on the sandbox first.**

## Quick start
1. Install Python 3.9+ (on Windows, tick "Add to PATH"). On Windows, also run `pip install -r requirements.txt`.
2. Create a **sandbox** key (`fp_test_...`) in your MFP dashboard, then set it:
   - Windows: `set FPERP_API_KEY=fp_test_...`
   - Mac/Linux: `export FPERP_API_KEY=fp_test_...`
3. Run `python serve_log.py` and open http://127.0.0.1:8765/
4. Turn on the **Master switch** to start the bot. Turn it off to stop it.

Optional environment variables:
- `FPERP_ACCOUNT_ID`: which account to trade. Defaults to the first account.
- `FPERP_BASE_URL`: the API URL. Defaults to the sandbox.
- `FPERP_MARKET`: the market. Defaults to `NAS100`, which the bot looks up in MFP's market list at startup. If more than one market matches, it stops and lists the Nasdaq-like ids so you can set the exact one (`venue|SYMBOL`).

**Never paste an API key into chat or commit it to the repo.** Keep it only in the environment variable. If a key is ever exposed, revoke it in the MFP dashboard and create a new one.

To go live, set `FPERP_BASE_URL=https://developers.myfundedperpetuals.com/v1` and use a live `fp_live_` key. The bot won't start if the key type and the URL don't match.

## Strategy (`strategy.py`)
The bot builds 1-minute candles by sampling the mid price every 5 s. It only trades during Silver Bullet windows on weekdays (New York time 03–04, 10–11 and 14–15):
1. **Liquidity:** the high and low of the previous 60 candles.
2. **Sweep:** price trades through one of those levels.
3. **Displacement + FVG:** a move against the sweep that leaves a fair value gap.
4. **Entry:** when price retraces into the FVG.
   - The stop goes 0.02% beyond the sweep extreme (about 6 points), and never closer than 0.15% from entry (about 45 points).
   - The take-profit is 3.33× the stop distance (risk $100 to make about $333).
5. There is at most one trade per window. At most one position is open at a time.

After a start, the bot needs about 60 minutes of candles before it can find liquidity levels.

## Risk (`risk.py`, `config.py`)
| Setting | Value |
|---|---|
| Max loss limit | 2% static ($500) |
| Daily loss cap | 2% ($500). Set `MAX_DAILY_LOSS_PCT` lower for a tighter cap. |
| Bot halts at | 75% of each limit ($375) |
| Risk per trade | $100 if the stop is hit |
| Target | 3.33× the stop distance (the $450 : $1,500 ratio), about +$333 |
| Losses before halt | 3 full stop-outs |
| Position cap | 3x equity notional ($75K), 5x isolated leverage |

- Before each trade, the bot checks whether that trade's full stop-out would cross a halt level. If it would, the trade is skipped.
- If a limit is hit while a position is open, the bot closes the position.
- TP/SL go on within about 1 s of the fill, with 5 tries. If they can't be placed, the position is closed.
- The daily loss resets at 00:00 UTC. Confirm MFP's reset time and adjust if needed.

## Files
| File | Purpose |
|---|---|
| `config.py` | All settings |
| `mfp_client.py` | API wrapper: unwraps `{"data": ...}`, adds Idempotency-Key on POST/PUT, retries |
| `strategy.py` | Candle builder + Silver Bullet |
| `risk.py` | Limits and position sizing |
| `agent.py` | Main loop |
| `serve_log.py` | Dashboard server on 127.0.0.1:8765; starts and stops `agent.py` |
| `dashboard.html` | Dashboard |
| `bot_state.json` | Master switch and strategy toggles |
| `bot_log.jsonl` | Event log (created at runtime) |

## Tests
`python -m unittest discover -s tests`. These run against a mocked API.

## Known assumptions to verify against the MFP OpenAPI spec
- **NAS100 market id, contract size, size step and tick size.** Size step and tick come from the market list if MFP provides them. The defaults are 0.01 and 0.1.
- **Order body fields:** `account_id`, `market_id`, `side` (`buy`/`sell`), `type: "market"`, `margin_mode`.
- **Response field names:** order `status`, and position `id` / `size` / `entry_price`. Several alternatives are accepted.
- **Automation policy:** MFP's policy bans HFT-like behaviour. This bot polls every 5 s and trades at most 3 times a day, but confirm this is acceptable.
