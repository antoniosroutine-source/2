import json, sys
from engine import simulate, load_bars, params
bars = load_bars(sys.argv[1])
STEPS = ["next_open", "stop2", "through", "lookahead", "calendar"]
tr = simulate(bars, STEPS, p=params(ASIA_NY_BIAS=False, ASIA_MIN_RANGE_PTS=0))
out = [{k: v for k, v in t.items() if k != "sig"} | {"w": t["sig"]["w"], "ny_move": t["sig"]["ny_move"], "t": t["sig"]["t"],
       "c": t["sig"]["entry"]} for t in tr]
json.dump(out, open(sys.argv[2], "w")); print(len(out))
