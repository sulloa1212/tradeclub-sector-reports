#!/usr/bin/env python3
"""
probe2_uw.py — follow-up reconnaissance. Answers the three questions left open
by probe_uw.py, so fetch_idx.py can be written once, correctly.

WHAT PROBE 1 ESTABLISHED
------------------------
  GOOD  Price, IV, greeks, skew and tide are all current (newest 2026-07-20).
  BAD   /technical-indicator/ is genuinely stale at UW: SMA20 stops 2026-07-01,
        SMA50 stops 2026-04-17. Confirmed over REST, so this is UW's data, not
        an MCP artifact. We will not use that endpoint -- we compute moving
        averages ourselves from the (fresh) daily bars instead.
  BLOCK Index tickers are entitlement-locked on this account:
        HTTP 422 "You do not have permissions to retrieve OHLC data for index
        ticker SPX". Same for NDX, RUT, DJX, VIX.

The report quotes real index levels (SPX 7,515.34 -- not SPY 742). So the open
question is where those levels come from. Three candidates, tested below.

QUESTIONS THIS ANSWERS
----------------------
  Q1  Is the index block only on OHLC, or account-wide? If SPX resolves on
      stock-state / interpolated-iv, we stay entirely inside UW.
  Q2  If UW is fully blocked, does a free no-auth quote source work from this
      machine? (Stooq -- no key, no account, plain CSV.)
  Q3  What exactly do the daily bars look like across a session boundary? They
      carry a market_time field ("pr" premarket / "po" postmarket / regular)
      and multiple rows per date, so a naive moving average would double-count.

RUN
---
    Double-click "Run UW Probe 2.command"

  Writes uw_probe2_output.json + uw_probe2_summary.txt next to this script.
  Stdlib only. Reuses the token already saved in .uw_token.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
UW = "https://api.unusualwhales.com"
TIMEOUT = 25
PAUSE = 0.35

# --- reuse probe 1's token loader + TLS repair -------------------------------
sys.path.insert(0, HERE)
try:
    from probe_uw import TOKEN, HEADERS, SSL_CTX, SSL_SOURCE, sample, date_range
except Exception as e:                                  # noqa: BLE001
    sys.exit(f"Couldn't import from probe_uw.py ({e}).\n"
             "Both files must sit in the same folder. Run probe 1 first.")


def fetch(url, headers=None, params=None, raw=False):
    """GET any URL. Returns (status, parsed_or_text, error)."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
            body = r.read().decode("utf-8", "replace")
            if raw:
                return r.status, body, None
            try:
                return r.status, json.loads(body), None
            except json.JSONDecodeError:
                return r.status, body[:1500], "non-JSON"
    except urllib.error.HTTPError as e:
        return e.code, None, f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:400]}"
    except Exception as e:                              # noqa: BLE001
        return 0, None, f"{type(e).__name__}: {e}"


results = []


def record(group, label, status, body, err, note=""):
    results.append(dict(group=group, label=label, status=status, error=err, note=note,
                        dates=date_range(body) if body is not None else None,
                        sample=sample(body) if body is not None else None))
    mark = "ok  " if status == 200 else "FAIL"
    extra = f"  ({err[:90]})" if err and status != 200 else ""
    print(f"   {mark} {label:<40}{extra}")


def main():
    print(f"probe2_uw.py — {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"TLS: {SSL_SOURCE}")
    print(f"token: ...{TOKEN[-4:]}\n")

    # ---------------------------------------------------------------- Q1
    print("Q1  Are index tickers blocked account-wide, or only on OHLC?")
    print("    (If any of these return 200, we can stay inside UW.)\n")
    for sym in ["SPX", "NDX", "RUT", "DJX", "VIX", "SPXW", "^SPX", "I:SPX"]:
        st, body, err = fetch(f"{UW}/api/stock/{sym}/stock-state", headers=HEADERS)
        record("Q1", f"stock-state[{sym}]", st, body, err)
        time.sleep(PAUSE)
    for sym in ["SPX", "VIX"]:
        for ep in ["interpolated-iv", "volatility/stats", "greek-exposure", "info"]:
            st, body, err = fetch(f"{UW}/api/stock/{sym}/{ep}", headers=HEADERS)
            record("Q1", f"{ep}[{sym}]", st, body, err)
            time.sleep(PAUSE)

    # ---------------------------------------------------------------- Q2
    print("\nQ2  Does a free no-auth index quote source reach this machine?")
    print("    (Only needed if Q1 is fully blocked. No key, no account.)\n")
    stooq = "https://stooq.com/q/l/?s=%5Espx,%5Endx,%5Erut,%5Edji,%5Evix&f=sd2t2ohlcv&h&e=csv"
    st, body, err = fetch(stooq, raw=True)
    record("Q2", "stooq_csv[5 indices]", st, body, err,
           note="expect a CSV header + 5 rows with close prices")
    if st == 200 and isinstance(body, str):
        print("    ---- raw response ----")
        for line in body.strip().splitlines()[:8]:
            print(f"    {line}")
        print("    ----------------------")

    # ---------------------------------------------------------------- Q3
    print("\nQ3  What do the daily bars look like around a session boundary?")
    print("    (Multiple rows per date + a market_time field -> must not")
    print("     double-count when computing SMA20/SMA50/5-day momentum.)\n")
    st, body, err = fetch(f"{UW}/api/stock/SPY/ohlc/1d", headers=HEADERS, params={"limit": 12})
    record("Q3", "ohlc_1d[SPY] raw 12", st, body, err)
    if st == 200 and isinstance(body, dict):
        rows = body.get("data", [])
        print(f"    {len(rows)} rows returned. Full detail, newest last:\n")
        print(f"    {'date':<12}{'mkt':<6}{'open':>10}{'close':>10}{'volume':>14}")
        for r in rows[-12:]:
            print(f"    {r.get('date',''):<12}{str(r.get('market_time','')):<6}"
                  f"{str(r.get('open','')):>10}{str(r.get('close','')):>10}"
                  f"{str(r.get('volume','')):>14}")
        seen = {}
        for r in rows:
            seen.setdefault(r.get("date"), []).append(r.get("market_time"))
        dupes = {d: m for d, m in seen.items() if len(m) > 1}
        print(f"\n    dates with >1 row: {len(dupes)}")
        for d, m in list(dupes.items())[:5]:
            print(f"      {d}: market_time values {m}")

    # also grab a wider window so I can verify SMA math against real bars
    st, body, err = fetch(f"{UW}/api/stock/SPY/ohlc/1d", headers=HEADERS, params={"limit": 80})
    record("Q3", "ohlc_1d[SPY] 80 bars", st, body, err, note="for SMA20/50 reconstruction")
    if st == 200 and isinstance(body, dict):
        # keep the FULL series for this one - I need real numbers, not a sample
        results[-1]["full_series"] = body.get("data", [])

    # DIA and IWM too: DJX/RUT conversion ratios depend on these
    for t in ["QQQ", "IWM", "DIA"]:
        st, body, err = fetch(f"{UW}/api/stock/{t}/stock-state", headers=HEADERS)
        record("Q3", f"stock-state[{t}]", st, body, err)
        time.sleep(PAUSE)

    # ---------------------------------------------------------------- write
    out = dict(generated=datetime.now().isoformat(timespec="seconds"),
               note="Token deliberately excluded.", results=results)
    json.dump(out, open(os.path.join(HERE, "uw_probe2_output.json"), "w",
                        encoding="utf-8"), indent=2, default=str)

    lines = [f"UW probe 2 — {out['generated']}", "=" * 72, ""]
    for g, title in [("Q1", "INDEX TICKER ENTITLEMENT"),
                     ("Q2", "FREE INDEX QUOTE FALLBACK"),
                     ("Q3", "DAILY BAR STRUCTURE")]:
        lines += [title, "-" * 72]
        for r in results:
            if r["group"] != g:
                continue
            lines.append(f"[{r['status']}] {r['label']}")
            if r["error"]:
                lines.append(f"      error: {r['error'][:300]}")
            if r["sample"] is not None:
                lines.append(f"      body: {json.dumps(r['sample'], default=str)[:500]}")
            lines.append("")
        lines.append("")
    open(os.path.join(HERE, "uw_probe2_summary.txt"), "w",
         encoding="utf-8").write("\n".join(lines))

    ok = sum(1 for r in results if r["status"] == 200)
    print(f"\n{ok}/{len(results)} returned 200")
    print("wrote uw_probe2_output.json and uw_probe2_summary.txt")
    print("\nTell Claude probe 2 is done.")


if __name__ == "__main__":
    main()
