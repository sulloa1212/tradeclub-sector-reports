#!/usr/bin/env python3
"""
probe3_uw.py — find UW's symbols for the Russell 2000 and the Dow.

WHERE WE ARE
------------
  SPX  OK   7443.28   (S&P 500 Index)
  NDX  OK   28604.23  (Nasdaq-100)
  VIX  OK   18.65
  RUT  null
  DJX  null

IMPORTANT LESSON FROM PROBE 2: UW answers HTTP 200 with a body of
{"data": null} for tickers it does not recognise. Status code alone is
meaningless here -- ^SPX and I:SPX both "succeeded" while returning nothing.
Everything below is judged on whether real numbers came back, never on the 200.

METHOD
------
  1. Pull UW's own optionable-ticker list and grep it for anything that looks
     like a Russell or Dow index. Authoritative, beats guessing.
  2. Independently try a candidate list of common symbols for both indices.
  3. Re-confirm the two we already have, so the pass/fail test is calibrated
     against symbols known to work.

RUN
---
    Double-click "Run UW Probe 3.command"
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
UW = "https://api.unusualwhales.com"
PAUSE = 0.3

sys.path.insert(0, HERE)
from probe_uw import TOKEN, HEADERS, SSL_CTX, SSL_SOURCE  # noqa: E402


def fetch(path, params=None):
    url = UW + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as e:
        return e.code, None, f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}"
    except Exception as e:                                  # noqa: BLE001
        return 0, None, f"{type(e).__name__}: {e}"


def has_real_data(body):
    """The only test that means anything against this API."""
    if not isinstance(body, dict):
        return False
    d = body.get("data")
    if d is None:
        return False
    if isinstance(d, (list, dict)) and len(d) == 0:
        return False
    return True


def main():
    print(f"probe3_uw.py — {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"TLS: {SSL_SOURCE}\n")
    found = {}

    # ---------------------------------------------------------------- step 1
    print("STEP 1  UW's own optionable-ticker list")
    st, body, err = fetch("/api/option-trades/optionable-tickers")
    universe = []
    if has_real_data(body):
        d = body["data"]
        universe = [x if isinstance(x, str) else (x.get("ticker") or x.get("symbol"))
                    for x in d] if isinstance(d, list) else []
        universe = [u for u in universe if u]
        print(f"   {len(universe):,} optionable tickers returned")
        pats = ["RUT", "RUS", "IUX", "DJ", "DOW", "INDU", "SPX", "NDX", "VIX", "XSP", "MID"]
        hits = sorted({u for u in universe for p in pats if u.upper().startswith(p)})
        print(f"   index-looking symbols: {', '.join(hits) if hits else '(none)'}")
        open(os.path.join(HERE, "uw_optionable_tickers.json"), "w",
             encoding="utf-8").write(json.dumps(universe, indent=1))
        print("   full list saved -> uw_optionable_tickers.json")
    else:
        print(f"   unavailable ({err or 'empty body'}) — relying on step 2")
    print()

    # ---------------------------------------------------------------- step 2
    print("STEP 2  candidate symbols (real data or bust)\n")
    candidates = {
        "Russell 2000": ["RUT", "RUTW", "IUX", "RUSSELL", "RUA", "IWM", "^RUT", "RTY"],
        "Dow Jones":    ["DJX", "DJI", "DJIA", "INDU", "DOW", "^DJI", "YM", "DIA"],
        "known-good":   ["SPX", "NDX", "VIX"],
        "known-bad":    ["ZZZZQQ"],
    }
    for group, syms in candidates.items():
        print(f"  {group}")
        for s in syms:
            st, body, err = fetch(f"/api/stock/{urllib.parse.quote(s)}/stock-state")
            ok = has_real_data(body)
            if ok:
                d = body["data"]
                close, prev = d.get("close"), d.get("prev_close")
                try:
                    chg = (float(close) / float(prev) - 1) * 100
                    chg_s = f"{chg:+.2f}%"
                except Exception:                            # noqa: BLE001
                    chg_s = "n/a"
                print(f"    REAL  {s:<10} close={close:<14} prev={prev:<14} day={chg_s}")
                if group in ("Russell 2000", "Dow Jones") and s not in found.values():
                    found.setdefault(group, s)
            else:
                why = err if err else "data:null"
                print(f"    ----  {s:<10} {why[:60]}")
            time.sleep(PAUSE)
        print()

    # ---------------------------------------------------------------- step 3
    print("STEP 3  full-name confirmation for anything found\n")
    for group, sym in found.items():
        st, body, err = fetch(f"/api/stock/{sym}/info")
        name = body["data"].get("full_name") if has_real_data(body) else None
        print(f"  {group:<14} -> {sym:<8} {name or '(no info)'}")
        time.sleep(PAUSE)

    print("\n" + "=" * 60)
    if len(found) == 2:
        print("Both indices resolved. Everything can come from UW.")
    elif found:
        print(f"Resolved: {found}. Still missing the other one.")
    else:
        print("Neither resolved — the report will need ETF-derived levels")
        print("for those two, or a second data source.")
    print("=" * 60)
    print("\nTell Claude probe 3 is done.")


if __name__ == "__main__":
    main()
