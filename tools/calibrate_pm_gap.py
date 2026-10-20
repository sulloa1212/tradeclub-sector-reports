"""Calibrate how much the rest of the session widens a mid-session Gap Scout.

The 2:00 PM edition prices "tomorrow's open" from the 2:00 PM price, so its
spread must cover the rest of today's session as well as the overnight gap.
This measures, for the four index ETFs over the last ~2 years of hourly bars,

    ratio(h) = var(log next_open / price with h hours left) / var(log next_open / close)

and prints the average over the four ETFs at each mark as the engine's
PM_VAR_RATIO table (h = 0 at the close, ratio 1). The effect is not linear
in h (small late in the day, larger around midday), so the engine
interpolates this table rather than fitting one slope.

    .venv/bin/python tools/calibrate_pm_gap.py

Free (yfinance), read-only. Re-run it if the number ever needs refreshing.
"""
import numpy as np
import yfinance as yf

ETFS = ("SPY", "QQQ", "IWM", "DIA")
MARKS = {"10:30": 5.5, "11:30": 4.5, "12:30": 3.5, "13:30": 2.5, "14:30": 1.5, "15:30": 0.5}


def ratios(sym: str) -> dict:
    h = yf.Ticker(sym).history(period="730d", interval="1h")
    h.index = h.index.tz_convert("America/New_York")
    days = sorted(set(h.index.date))
    rows = {m: [] for m in MARKS}
    close_open = []
    for d0, d1 in zip(days[:-1], days[1:]):
        a, b = h[h.index.date == d0], h[h.index.date == d1]
        if a.empty or b.empty:
            continue
        stamps = set(a.index.strftime("%H:%M"))
        if not all(m in stamps for m in MARKS):
            continue                      # half days and gaps in the data
        nxt = float(b["Open"].iloc[0])
        close_open.append(np.log(nxt / float(a["Close"].iloc[-1])))
        for m in MARKS:
            px = float(a[a.index.strftime("%H:%M") == m]["Open"].iloc[0])
            rows[m].append(np.log(nxt / px))
    base = np.var(close_open)
    return {m: np.var(v) / base for m, v in rows.items()}, len(close_open)


def main():
    pooled = {m: [] for m in MARKS}
    for sym in ETFS:
        r, n = ratios(sym)
        print(f"{sym} ({n} days): " + "  ".join(f"{m} {r[m]:.2f}" for m in MARKS))
        for m, v in r.items():
            pooled[m].append(v)
    table = [(0.0, 1.0)] + sorted((MARKS[m], round(float(np.mean(v)), 2)) for m, v in pooled.items())
    (h1, r1), (h2, r2) = table[-2], table[-1]
    table.append((6.5, round(r2 + (r2 - r1) / (h2 - h1) * (6.5 - h2), 2)))  # to the open
    print("\nPM_VAR_RATIO = (" + ", ".join(f"({h:g}, {r:.2f})" for h, r in table) + ")")
    for h in (0.5, 1.0, 2.0, 2.82, 4.0):
        lo = max(p for p in table if p[0] <= h); hi = min(p for p in table if p[0] >= h)
        r = lo[1] if hi[0] == lo[0] else lo[1] + (hi[1] - lo[1]) * (h - lo[0]) / (hi[0] - lo[0])
        print(f"  {h:>4} h left -> spread x{np.sqrt(r):.3f}")


if __name__ == "__main__":
    main()
