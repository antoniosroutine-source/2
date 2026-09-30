# MFP Trading Bot: ICT Silver Bullet

This is an automated trading bot for a **MyFundedPerps Prime ($25K)** account. It trades MFP's **Nasdaq-100 perpetual, XYZ100** (`hyperliquid|xyz:XYZ100`), using the ICT Silver Bullet strategy. A local dashboard gives you a master kill switch, a toggle for each strategy, risk bars and a trade log.

The API calls follow MFP's official docs and OpenAPI spec (https://docs.myfundedperpetuals.com). The API is in beta and the bot has not placed a real order yet. **Test on the sandbox first.**

## Quick start
1. Install Python 3.9+ (on Windows, tick "Add to PATH"). On Windows, also run `pip install -r requirements.txt`.
2. In [API Key Settings](https://myfundedperpetuals.com/settings?section=api-keys), create a key with **Read And Trade** access and the **Test (Sandbox)** environment. It starts `fp_test_` and trades a free $100K sandbox account. Then set it:
   - Windows: `set FPERP_API_KEY=fp_test_...`
   - Mac/Linux: `export FPERP_API_KEY=fp_test_...`
3. Run `python serve_log.py` and open http://127.0.0.1:8765/
4. Turn on the **Master switch** to start the bot. Turn it off to stop it.

Optional environment variables:
- `FPERP_ACCOUNT_ID`: which account to trade. Defaults to the first account.
- `FPERP_BASE_URL`: the API URL. Defaults to the sandbox.
- `FPERP_MARKET`: the market. Defaults to `hyperliquid|xyz:XYZ100`. A plain symbol (`XYZ100`, or the aliases `NAS100`/`NDX`/`US100`/`NQ`) is looked up in MFP's market list at startup.

**Never paste an API key into chat or commit it to the repo.** Keep it only in the environment variable. If a key is ever exposed, revoke it in the MFP dashboard and create a new one.

To go live, set `FPERP_BASE_URL=https://developers.myfundedperpetuals.com/v1` and use a live `fp_live_` key. Test keys only work on the sandbox host and live keys only on the live host, so the bot won't start if they don't match.

## Strategy (`strategy.py`)
The bot builds 1-minute candles by sampling the mid price every 5 s. It only trades during Silver Bullet windows on weekdays (New York time 03–04, 10–11 and 14–15):
1. **Liquidity:** the high and low of the previous 60 candles.
2. **Sweep:** price trades through one of those levels.
3. **Displacement + FVG:** a move against the sweep that leaves a fair value gap.
4. **Entry:** when price retraces into the FVG.
   - The stop goes 0.02% beyond the sweep extreme (about 6 points), and never closer than 0.15% from entry (about 45 points).
   - The take-profit is 3.33× the stop distance (risk $100 to make about $333).
   - The TP and SL are attached to the entry order, so MFP creates them with the fill.
5. There is at most one trade per window. At most one position is open at a time.

After a start, the bot needs about 60 minutes of candles before it can find liquidity levels.

## Risk (`risk.py`, `config.py`)
MFP's 1-Step Prime rules: **3% daily loss ($750)**, **5% static max drawdown ($1,250)** and a **12% profit target ($3,000)**, all on the starting balance and measured on equity including open P&L. The daily loss resets at **midnight New York time**.

The bot uses tighter limits of its own:

| Setting | Value |
|---|---|
| Bot max loss | 2% static ($500) |
| Bot daily loss cap | 2% ($500), resetting at midnight New York time like MFP's |
| Bot halts at | 75% of each of its limits ($375) |
| MFP room check | Also halts if MFP reports less than $150 left above either firm floor (`SERVER_ROOM_RESERVE_USD`). This covers losses the bot didn't see, such as manual trades or a restart mid-day. |
| Risk per trade | $100 if the stop is hit |
| Target | 3.33× the stop distance (the $450 : $1,500 ratio), about +$333 |
| Losses before halt | 3 full stop-outs |
| Position cap | 3x equity notional ($75K), 5x isolated leverage (XYZ100 allows up to 12x) |

- Before each trade, the bot checks whether that trade's full stop-out would cross a halt level. If it would, the trade is skipped.
- If a limit is hit while a position is open, the bot closes the position.
- TP/SL are attached to the entry order. After the fill the bot checks they exist. If they don't, it places them as an OCO pair (5 tries), and closes the position if that fails.
- Every entry carries a `client_order_id`. If the response to an order is lost, the bot looks the order up by that id instead of placing it again.
- If MFP can't mark an open position (equity comes back `null`), the bot waits for the next loop and takes no action.
- At startup the bot reads the account's real starting balance and MFP's trading policy. It stops if the account isn't active or trading is blocked.

## Files
| File | Purpose |
|---|---|
| `config.py` | All settings |
| `mfp_client.py` | API wrapper: unwraps `{"data": ...}`, adds Idempotency-Key on POST/PUT, retries (honouring `Retry-After`) |
| `strategy.py` | Candle builder + Silver Bullet |
| `risk.py` | Limits and position sizing |
| `agent.py` | Main loop |
| `serve_log.py` | Dashboard server on 127.0.0.1:8765; starts and stops `agent.py` |
| `dashboard.html` | Dashboard |
| `bot_state.json` | Master switch and strategy toggles |
| `bot_log.jsonl` | Event log (created at runtime) |

## Tests
`python -m unittest discover -s tests`. These run against a mocked API.

## Still to confirm on the sandbox
- **XYZ100 price precision.** MFP's market list gives no tick size. TP/SL are rounded to 1 point (`PRICE_TICK`), which matches Hyperliquid's 5 significant figures at ~25,000.
- **Automation policy.** MFP limits requests to about 300 a minute per IP address and warns against HFT-like behaviour. This bot makes about 36 requests a minute and trades at most 3 times a day.
