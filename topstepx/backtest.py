"""Backtest the level sweep strategy on 1-minute bars, with the same code the bot trades with.

    python backtest.py --days 60                 # download MNQ history through your ProjectX key
    python backtest.py --csv bars.csv            # or use a CSV: time,open,high,low,close,volume

Fills: entry at the signal bar's close plus 1 tick of slippage; the stop sits FIXED_STOP_PTS from the
fill (as the live bracket does); the stop is checked before the target inside each bar (the pessimistic
assumption); stops that gap fill at the bar's open. Open trades close before the 8:30am ET news and
when the day reaches +$1,500 including the open trade.
Aggression comes from 1-minute bars here (the live bot reads the real tape), so treat results
as an approximation of live behaviour.
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys

import config
from levels import Aggression, et, trading_day
from manage import DayGuard, next_stop
from strategy import Bar, make_strategy


def simulate(bars, p, tick, pv, slippage_ticks=1, accept=None):
    """accept(signal) -> bool lets a study filter signals (e.g. by bias) without changing the strategy."""
    strat = make_strategy(p, tick, pv)
    agg = Aggression(p.AGG_WINDOW_MIN)
    guard = DayGuard(p)
    trades, skips, pos = [], [], None
    slip = slippage_ticks * tick
    for b in bars:
        if pos:
            d = 1 if pos["side"] == "long" else -1
            exit_px, reason = None, None
            if (d > 0 and b.l <= pos["stop"]) or (d < 0 and b.h >= pos["stop"]):
                gap = (b.o - pos["stop"]) * d < 0
                exit_px, reason = (b.o if gap else pos["stop"]) - d * slip, "stop"
            elif (d > 0 and b.h >= pos["target"]) or (d < 0 and b.l <= pos["target"]):
                exit_px, reason = pos["target"], "target"
            elif guard.pnl > 0 and ((d > 0 and b.h >= pos["day_cap"]) or (d < 0 and b.l <= pos["day_cap"])):
                exit_px, reason = pos["day_cap"], "day_cap"      # the day reached +$1,500 with this trade
            elif guard.must_flatten(b.t):
                exit_px, reason = b.o - d * slip, "flat_by"
            elif pos.get("exit_by") and b.t >= pos["exit_by"]:
                exit_px, reason = b.o - d * slip, "time_exit"
            if exit_px is not None:
                pnl = (exit_px - pos["entry"]) * d * pos["size"] * pv - p.FEE_PER_CONTRACT_RT * pos["size"]
                if reason == "stop" and pos["stop"] != pos["initial_stop"]:
                    reason = "trail" if (pos["stop"] - pos["entry"]) * d > tick else "breakeven"
                guard.record(b.t, pnl)
                trades.append({**{k: pos[k] for k in ("side", "size", "entry", "initial_stop", "target", "opened")},
                               "exit": round(exit_px, 2), "reason": reason, "pnl": round(pnl, 2),
                               "closed": b.t, "day": str(trading_day(b.t, p.DAY_START))})
                pos = None
            else:
                pos["peak"] = max(pos["peak"], b.h) if d > 0 else min(pos["peak"], b.l)
                if pos["trail"]:
                    pos["stop"] = next_stop(pos["side"], pos["entry"], pos["stop"], pos["peak"],
                                        pos["size"], pv, tick, p)
        agg.add_bar(b)
        sig = strat.on_bar(b, agg.ratio(b.t + 60), live=True)
        if not sig or pos:
            continue
        if "skip" in sig:
            skips.append(sig["skip"])
            continue
        ok, why = guard.can_enter(b.t + 60)
        if not ok or (accept and not accept(sig)):
            continue
        d = 1 if sig["side"] == "long" else -1
        entry = sig["entry"] + d * slip
        fixed = p.FIXED_STOP_PTS if sig.get("strategy", "level_sweep") == "level_sweep" else None
        stop = entry - d * fixed if fixed else sig["stop"] + d * slip   # the bracket is placed from the fill
        room = p.DAILY_PROFIT_STOP_USD - guard.pnl + p.FEE_PER_CONTRACT_RT * sig["size"]
        pos = {"side": sig["side"], "size": sig["size"], "entry": entry, "stop": stop,
               "initial_stop": stop, "target": sig["target"], "peak": entry, "opened": b.t + 60,
               "day_cap": entry + d * room / (sig["size"] * pv), "exit_by": sig.get("exit_by"),
               "trail": sig.get("strategy", "level_sweep") == "level_sweep" or p.ASIA_TRAIL}
    return trades, skips


def report(trades, skips, p):
    if not trades:
        print("No trades.")
        return
    pnl = [t["pnl"] for t in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < p.LOSS_THRESHOLD_USD]
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in pnl:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    days = {}
    for t in trades:
        days[t["day"]] = days.get(t["day"], 0.0) + t["pnl"]
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    total = sum(pnl)
    best_day = max(days.values())
    print(f"Trades: {len(trades)}   wins: {len(wins)} ({len(wins) / len(trades):.0%})   "
          f"losses: {len(losses)}   scratches: {len(trades) - len(wins) - len(losses)}")
    print(f"Net P&L: ${total:,.2f}   avg/trade: ${total / len(trades):,.2f}   "
          f"avg win: ${sum(wins) / max(1, len(wins)):,.2f}   "
          f"avg loss: ${sum(losses) / max(1, len(losses)):,.2f}")
    print(f"Max drawdown: ${dd:,.2f}   trading days: {len(days)}   "
          f"green days: {sum(1 for v in days.values() if v > 0)}   best day: ${best_day:,.2f}"
          + (f" ({best_day / total:.0%} of net)" if total > 0 else ""))
    print(f"Exits: {reasons}")
    if skips:
        print(f"Setups skipped: {len(skips)} (stop too wide or target too small)")


def load_csv(path):
    bars = []
    with open(path) as f:
        for row in csv.DictReader(f):
            t = row.get("time") or row.get("t")
            ts = float(t) if t.replace(".", "").isdigit() else dt.datetime.fromisoformat(t).timestamp()
            bars.append(Bar(ts, float(row["open"]), float(row["high"]), float(row["low"]),
                            float(row["close"]), float(row.get("volume") or 0)))
    return sorted(bars, key=lambda b: b.t)


def load_api(days):
    import bot
    client, contract = bot.connect(read_only=True)
    end = dt.datetime.now(dt.timezone.utc)
    bars = client.bars_range(contract["id"], end - dt.timedelta(days=days), end)
    tick = contract["tickSize"]
    return bars, tick, contract["tickValue"] / tick


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=60, help="days of history to download (default 60)")
    ap.add_argument("--csv", help="use 1-minute bars from a CSV instead of the API")
    ap.add_argument("--out", default=os.path.join(config.HERE, "backtest_trades.json"))
    args = ap.parse_args()
    if args.csv:
        bars, tick, pv = load_csv(args.csv), config.TICK_SIZE, config.POINT_VALUE
    else:
        bars, tick, pv = load_api(args.days)
    if not bars:
        sys.exit("No bars.")
    print(f"{len(bars)} one-minute bars: {et(bars[0].t):%Y-%m-%d %H:%M} to {et(bars[-1].t):%Y-%m-%d %H:%M} NY time")
    trades, skips = simulate(bars, config, tick, pv)
    report(trades, skips, config)
    with open(args.out, "w") as f:
        json.dump(trades, f, indent=1)
    print(f"Trade list saved to {args.out}")


if __name__ == "__main__":
    main()
