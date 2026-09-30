"""Read-only connection check. Places no orders.

Run it before turning the bot on:  python check.py
It confirms the API key works, shows the accounts the key can see, the chosen account's risk
snapshot, the market, a live quote, and what one trade would look like.
"""
import sys

import agent
import config
from mfp_client import MFPClient, MFPError, pick


def main():
    api_key = agent.os.environ.get(config.API_KEY_ENV, "").strip()
    agent.check_key(api_key, config.BASE_URL)
    env = "LIVE" if config.BASE_URL == config.LIVE_URL else "SANDBOX"
    print(f"Server: {config.BASE_URL} ({env})")
    client = MFPClient(api_key, config.BASE_URL)

    try:
        accounts = client.list_accounts()
    except MFPError as e:
        sys.exit(f"The API key was refused ({e.status}). Check it was copied in full. Details: {e.body}")
    print(f"\nAccounts this key can see: {len(accounts)}")
    for a in accounts:
        print(f"  id={a.get('id')}  name={a.get('name')}  stage={a.get('stage')}  status={a.get('status')}"
              f"  starting_balance={a.get('starting_balance')}  balance={a.get('balance')}")
    if not accounts:
        sys.exit("No accounts. Check the key's account access in MFP's API Key Settings.")

    account_id = config.ACCOUNT_ID or (pick(accounts[0], "id") if len(accounts) == 1 else None)
    if not account_id:
        sys.exit("\nMore than one account: set FPERP_ACCOUNT_ID to the id you want the bot to trade, "
                 "then run this again.")
    account = client.get_account(account_id)
    risk = account.get("risk") or {}
    print(f"\nChosen account: {account.get('name')} ({account_id}), status {account.get('status')}")
    for k in ("equity", "daily_loss_floor", "daily_loss_room", "max_drawdown_floor",
              "max_drawdown_room", "remaining_profit", "marks_complete"):
        print(f"  {k}: {risk.get(k)}")
    print(f"  rules: {risk.get('requirements')}")

    try:
        policy = client.get_trading_policy(account_id)
        print(f"\nTrading policy: manual_trading_blocked={policy.get('manual_trading_blocked')}"
              f"  halt={policy.get('trading_halt')}")
        print(f"  limits: {policy.get('limits')}")
    except (MFPError, OSError) as e:
        print(f"\nCould not read trading policy: {e}")

    market, info = agent.resolve_market(client, config.MARKET)
    step = agent.size_step(info)
    print(f"\nMarket: {market}  size step {step}  max leverage {info.get('max_leverage')}")
    quote = client.get_quote(market, "buy", config.QUOTE_SIZE)
    mid = float(quote["mid"])
    print(f"  bid {quote.get('bid')}  ask {quote.get('ask')}  mid {mid}")

    equity = float(risk.get("equity") or account.get("balance") or config.STARTING_BALANCE)
    stop_dist = mid * config.MIN_STOP_PCT * 2
    size = min(config.RISK_PER_TRADE_USD / stop_dist, equity * config.MAX_NOTIONAL_MULT / mid)
    size = int(size / step) * step
    print(f"\nExample trade with a {stop_dist:.0f}-point stop: size {size:.4f}, notional ${size * mid:,.0f},"
          f" loss at stop ${size * stop_dist:,.2f}, target ${size * stop_dist * config.RR:,.2f}")
    print("\nAll checks passed. No orders were placed.")


if __name__ == "__main__":
    main()
