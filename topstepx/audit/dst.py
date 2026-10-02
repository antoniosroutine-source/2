"""1.6: UTC -> New York conversion around every DST change (US and EU)."""
import sys, datetime as dt
import pandas as pd
df = pd.read_csv(sys.argv[1])
t = pd.to_datetime(df["time"], unit="s", utc=True) if pd.api.types.is_numeric_dtype(df["time"]) else pd.to_datetime(df["time"], utc=True)
ny = t.dt.tz_convert("America/New_York")
gap = t.diff().dt.total_seconds()
# the CFD's daily break: find the bar after each gap of 30+ minutes on weekdays
br = ny[(gap > 1800) & (ny.dt.weekday < 5)]
print("daily reopen time (New York) by month, count of each:")
print(br.groupby(br.dt.strftime("%Y-%m")).agg(lambda s: dict(s.dt.strftime("%H:%M").value_counts().head(2))).to_string())
changes = ["2024-10-27", "2024-11-03", "2025-03-09", "2025-03-30", "2025-10-26", "2025-11-02", "2026-03-08", "2026-03-29"]
for c in changes:
    d = pd.Timestamp(c).date()
    for day in (d - dt.timedelta(days=1), d + dt.timedelta(days=1)):
        m = (ny.dt.date == day) & (ny.dt.strftime("%H:%M").isin(["18:59", "19:00", "19:01", "19:02", "19:03"]))
        rows = [f"{u:%H:%M}Z={n:%H:%M}ET" for u, n in zip(t[m], ny[m])]
        print(f"{c} change, {day} ({day:%a}): {' '.join(rows) or 'no bars (weekend)'}")
