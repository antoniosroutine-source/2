"""Run each frozen finalist once on the dataset in HARNESS2_DATA; save R and P&L per trade."""
import importlib.util, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
out = {}
for key, rel in (("trend1", "trend/finalist_1.py"), ("quant1", "quant/finalist_1.py"),
                 ("quant2", "quant/finalist_2.py"), ("trader1", "trader/finalist_1.py")):
    path = os.path.join(HERE, rel)
    sys.path.insert(0, os.path.dirname(path))
    spec = importlib.util.spec_from_file_location(key, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    sigs = m.signals()
    if hasattr(m, "exits"):
        tr = m.exits(sigs)
    else:
        from harness2 import backtest
        tr = backtest(sigs)
    out[key] = [{"R": float(t["R"]), "pnl": float(t["pnl"]), "day": t["day"]} for t in tr]
    print(key, len(tr), flush=True)
    sys.path.pop(0)
json.dump(out, open(sys.argv[1], "w"))
