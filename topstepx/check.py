"""Read-only connection check. Places no orders.

    python check.py

Confirms the login works, lists your accounts, finds MNQ, loads recent bars and shows the levels,
bias and aggression the bot would use right now, plus any open positions and orders.
"""
import datetime as dt
import time

import bot
import config
from levels import Aggression, et
from strategy import LevelSweep


def main():
    client, contract = bot.connect()
    print(f"Logged in. Contract: {contract['name']} ({contract['id']}), tick {contract['tickSize']}, "
          f"${contract['tickValue']} per tick")
    accounts = client.accounts()
    print(f"\nActive accounts: {len(accounts)}")
    for a in accounts:
        print(f"  id={a['id']}  {a.get('name')}  balance={a.get('balance')}  canTrade={a.get('canTrade')}")
    account = bot.pick_account(client)
    print(f"\nThe bot would trade: {account.get('name')} (id {account['id']})")

    end = dt.datetime.now(dt.timezone.utc)
    bars = client.bars_range(contract["id"], end - dt.timedelta(days=3), end, live=config.LIVE_DATA)
    bars = [b for b in bars if b.t + 60 <= time.time()]
    print(f"\nLoaded {len(bars)} one-minute bars; last: {et(bars[-1].t):%Y-%m-%d %H:%M} NY, close {bars[-1].c}")
    strat = LevelSweep(config, config.TICK_SIZE, config.POINT_VALUE)
    agg = Aggression(config.AGG_WINDOW_MIN)
    for b in bars:
        agg.add_bar(b)
        strat.on_bar(b, 0.0, live=False)
    print("\nLevels:")
    for lv in sorted(strat.levels, key=lambda x: -x["price"]):
        print(f"  {lv['price']:>10.2f}  {lv['label']}")
    print(f"\nBias: {strat.bias}")
    ratio = agg.ratio(time.time())
    side = "sellers" if ratio <= -config.AGG_MIN_RATIO else "buyers" if ratio >= config.AGG_MIN_RATIO else "neither"
    print(f"Aggression (last {config.AGG_WINDOW_MIN} min, from bars): {ratio:+.3f} -> {side}")
    size = int(config.MAX_RISK_USD // (config.FIXED_STOP_PTS * config.POINT_VALUE + config.FEE_PER_CONTRACT_RT)) \
        if config.FIXED_STOP_PTS else None
    if size:
        print(f"Sizing: {config.FIXED_STOP_PTS:.0f}-point stop -> {min(size, config.MAX_CONTRACTS)} MNQ "
              f"(${min(size, config.MAX_CONTRACTS) * (config.FIXED_STOP_PTS * config.POINT_VALUE + config.FEE_PER_CONTRACT_RT):.0f} at the stop)")

    print(f"\nOpen positions: {client.positions(account['id']) or 'none'}")
    print(f"Open orders: {client.open_orders(account['id']) or 'none'}")
    print("\nAll checks passed. No orders were placed.")


if __name__ == "__main__":
    main()
