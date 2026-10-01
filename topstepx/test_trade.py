"""One-contract execution test on your real account. It places ONE MNQ and closes it again.

    python test_trade.py              # 1 MNQ long, held 60 s
    python test_trade.py --side short --hold 120

It checks the live order path end to end, exactly as the bot uses it:
1. a market order with a linked (OCO) 20-point stop and 20-point target,
2. that both exit orders appear on the account,
3. moving the stop (what breakeven and the trail do),
4. closing the position and cancelling the leftover exits.
Most it can lose: 1 MNQ x 20 points = $40, plus fees. Stop the bot (Ctrl+C) before running this.
"""
import argparse
import sys
import time

import bot
from projectx import ORDER_LIMIT, ORDER_STOP, PXError

STOP_TICKS = TARGET_TICKS = 80   # 20 points on MNQ


def show_orders(broker):
    orders = broker.working_orders()
    for o in orders:
        kind = {ORDER_STOP: "STOP", ORDER_LIMIT: "TARGET"}.get(o.get("type"), f"type {o.get('type')}")
        side = "sell" if o.get("side") == 1 else "buy"
        print(f"   {kind:6} {side} {o.get('size')} @ {o.get('stopPrice') or o.get('limitPrice')}  (order {o['id']})")
    return orders


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--side", choices=("long", "short"), default="long")
    ap.add_argument("--hold", type=int, default=60, help="seconds to hold before closing (default 60)")
    args = ap.parse_args()

    client, contract = bot.connect()
    account = bot.pick_account(client)
    broker = bot.LiveBroker(client, account["id"], contract)
    if broker.position() or broker.working_orders():
        sys.exit("This account already has an MNQ position or working order. Close it and stop the bot first.")

    print(f"\nAccount: {account.get('name')}   Contract: {contract['name']}")
    answer = input(f"Place a REAL 1 MNQ {args.side.upper()} market order with a 20-point stop and target, "
                   f"then close it after {args.hold} s? Type YES and press Enter: ")
    if answer.strip().lower() not in ("yes", "y"):
        sys.exit("Cancelled. Nothing was placed.")

    print("\n1) Sending the market order with linked stop and target...")
    try:
        fill = broker.enter(args.side, 1, None, f"test-{int(time.time())}", STOP_TICKS, TARGET_TICKS)
    except PXError as e:
        if "Position Brackets" in str(e.message):
            sys.exit(f"REJECTED: {e.message}\n{bot.BRACKET_HINT}")
        sys.exit(f"REJECTED: {e}")
    pos = broker.position()
    if not pos:
        sys.exit("No position appeared after 10 s. Check TopstepX, and close anything there by hand.")
    print(f"   FILLED: {pos['side']} {pos['size']} @ {pos['entry']}")

    ok = True
    print("\n2) Exit orders on the account:")
    time.sleep(1)
    orders = show_orders(broker)
    stop = broker.stop_order()
    if not stop or not any(o.get("type") == ORDER_LIMIT for o in orders):
        print("   PROBLEM: the stop or target is missing. Closing the position now.")
        ok = False
    else:
        d = 1 if pos["side"] == "long" else -1
        new_stop = round(float(stop["stopPrice"]) + d * 5.0, 2)   # 5 points closer, like a breakeven move
        print(f"\n3) Moving the stop from {stop['stopPrice']} to {new_stop} (like breakeven/trail)...")
        try:
            broker.move_stop(new_stop)
            time.sleep(1)
            moved = broker.stop_order()
            print(f"   stop is now {moved and moved.get('stopPrice')}")
            ok = bool(moved) and abs(float(moved["stopPrice"]) - new_stop) < 0.01
            if not ok:
                print("   PROBLEM: the stop did not move.")
        except PXError as e:
            print(f"   PROBLEM: stop move failed: {e}")
            ok = False
        if ok:
            print(f"\n   Holding {args.hold} s (watch it in TopstepX)...")
            end = time.time() + args.hold
            while time.time() < end:
                if not broker.position():
                    print("   The position closed on its own (stop or target hit).")
                    break
                time.sleep(2)

    print("\n4) Closing the position and cancelling leftover exits...")
    broker.flatten()
    time.sleep(2)
    left_pos, left_orders = broker.position(), broker.working_orders()
    if left_pos or left_orders:
        print(f"   PROBLEM: still open: {left_pos} {left_orders}. Close them in TopstepX now.")
        ok = False
    else:
        print("   Flat, with no working orders.")
    print("\nRESULT:", "PASS: the live order path works." if ok else "FAIL: see the PROBLEM lines above.")


if __name__ == "__main__":
    main()
