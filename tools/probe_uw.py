#!/usr/bin/env python3
"""
probe_uw.py — one-time reconnaissance against the Unusual Whales REST API.

WHY THIS EXISTS
---------------
The UW *MCP connector* is unusable for this report: its multi-command tools
(uw_market, uw_stock, uw_flow, uw_etf, ...) arrive with an empty parameter
schema, so the `command` argument is stripped before it reaches UW's server.
Verified by calling uw_market with NO arguments and with a valid `command` --
byte-identical "No matching discriminator" errors both times.

The REST API has no such problem. This script does NOT fetch report data; it
maps the terrain first -- which endpoints exist, what they return, what the
field names actually are, and (critically) how fresh the data is. fetch_idx.py
gets written against what this observes, not against what I assume.

SETUP (once)
------------
    cd "<this folder>"
    printf '%s' 'YOUR_UW_API_TOKEN' > .uw_token
    chmod 600 .uw_token

  The token is read from that file and never printed, logged, or written into
  any output file this script produces. Add .uw_token to .gitignore before the
  next GitLab handoff zip -- see the NOTE at the bottom of this file.

RUN
---
    python3 probe_uw.py

  Writes, next to this script:
    uw_openapi.yaml        full endpoint spec (the authoritative list)
    uw_probe_output.json   status + sample payload for every probed endpoint
    uw_probe_summary.txt   human-readable digest; skim this first

  Stdlib only. No pip install.
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
BASE = "https://api.unusualwhales.com"
TIMEOUT = 25
PAUSE = 0.35          # be polite; UW rate-limits per minute

# ----------------------------------------------------------------------------
# token
# ----------------------------------------------------------------------------
def load_token():
    path = os.path.join(HERE, ".uw_token")
    if not os.path.exists(path):
        sys.exit(
            "No .uw_token file found next to this script.\n"
            "Create it with:\n"
            "    printf '%s' 'YOUR_UW_API_TOKEN' > .uw_token\n"
            "    chmod 600 .uw_token"
        )
    tok = open(path, encoding="utf-8").read().strip()
    if not tok:
        sys.exit(".uw_token exists but is empty.")
    if tok.lower().startswith("bearer "):
        tok = tok[7:].strip()          # tolerate a pasted "Bearer xxx"
    return tok


TOKEN = load_token()
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "UW-CLIENT-API-ID": "100001",       # required per UW's published skill file
    "Accept": "application/json",
    "User-Agent": "mwtc-gap-risk-probe/1.0",
}


# ----------------------------------------------------------------------------
# TLS trust store
# ----------------------------------------------------------------------------
# Python builds downloaded from python.org ship WITHOUT a root-certificate
# bundle -- they expect you to run their "Install Certificates.command" once.
# If you never did, every HTTPS call dies with CERTIFICATE_VERIFY_FAILED before
# a single byte leaves the machine. That is a local trust-store gap, not a bad
# token and not a UW problem. We locate a usable CA bundle ourselves.
#
# We do NOT disable verification. Turning verification off would make the
# connection carrying your API token unauthenticated and trivially
# interceptable -- never the right trade for a "just make it work" fix.
def build_ssl_context():
    candidates = []
    try:
        import certifi                     # present in many installs
        candidates.append(("certifi", certifi.where()))
    except ImportError:
        pass
    candidates += [
        ("macOS system bundle", "/etc/ssl/cert.pem"),
        ("homebrew openssl", "/opt/homebrew/etc/ca-certificates/cert.pem"),
        ("homebrew openssl (intel)", "/usr/local/etc/ca-certificates/cert.pem"),
        ("openssl legacy", "/usr/local/etc/openssl/cert.pem"),
    ]
    for name, path in candidates:
        if path and os.path.exists(path):
            try:
                ctx = ssl.create_default_context(cafile=path)
                return ctx, f"{name} ({path})"
            except Exception:               # noqa: BLE001
                continue
    return ssl.create_default_context(), "python default (may fail)"


SSL_CTX, SSL_SOURCE = build_ssl_context()


def get(path, params=None, accept_json=True):
    """GET {BASE}{path}. Returns (status, parsed_or_text, error_string)."""
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    hdrs = dict(HEADERS)
    if not accept_json:
        hdrs["Accept"] = "text/plain, application/yaml, */*"
    req = urllib.request.Request(url, headers=hdrs)
    ctx = SSL_CTX
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            raw = r.read().decode("utf-8", "replace")
            if not accept_json:
                return r.status, raw, None
            try:
                return r.status, json.loads(raw), None
            except json.JSONDecodeError:
                return r.status, raw[:2000], "non-JSON body"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:600]
        return e.code, None, f"HTTP {e.code}: {body}"
    except Exception as e:                       # noqa: BLE001
        return 0, None, f"{type(e).__name__}: {e}"


# ----------------------------------------------------------------------------
# what we actually need, field by field, and the candidates that might supply it
# ----------------------------------------------------------------------------
# IDX block fields we must populate per index:
#   lvl          index level (last / close)
#   day          today's % change
#   vol          30-day annualized IV   (VIX / VXN / RVX / VXD analogue)
#   vol1d        1-day annualized IV    (VIX1D analogue)  <- v11's new field
#   above_sma20 / above_sma50 / ma_rising    trend vs moving averages
#   mom5_pct     5-day % change
#   gamma        "pos" / "neg" / "thin" regime
#   r            put/call skew ratio
#
# ETFs are the reliable UW symbols; index symbols are probed too in case they
# resolve, which would let us skip the ETF->index conversion entirely.
ETFS = ["SPY", "QQQ", "IWM", "DIA"]
INDEX_SYMBOLS = ["SPX", "NDX", "RUT", "DJX", "VIX"]

PROBES = []


def probe(label, path, params=None, need=None):
    PROBES.append(dict(label=label, path=path, params=params or {}, need=need or ""))


# -- price / level / daily move ------------------------------------------------
for t in ETFS[:2]:                       # two is enough to confirm the shape
    probe(f"ohlc_1d[{t}]", f"/api/stock/{t}/ohlc/1d", {"limit": 8},
          need="lvl, day, mom5_pct")
    probe(f"stock_state[{t}]", f"/api/stock/{t}/stock-state", need="lvl, day (live)")
    probe(f"info[{t}]", f"/api/stock/{t}/info", need="lvl, sector meta")

# -- does UW resolve raw index symbols? ----------------------------------------
for s in INDEX_SYMBOLS:
    probe(f"index_ohlc[{s}]", f"/api/stock/{s}/ohlc/1d", {"limit": 3},
          need="native index level -- avoids ETF conversion")

# -- implied volatility --------------------------------------------------------
for t in ETFS[:2]:
    probe(f"interpolated_iv[{t}]", f"/api/stock/{t}/interpolated-iv", need="vol (30d IV)")
    probe(f"vol_term_structure[{t}]", f"/api/stock/{t}/volatility/term-structure",
          need="vol1d -- THE v11 field; needs a short-tenor point")
    probe(f"vol_stats[{t}]", f"/api/stock/{t}/volatility/stats", need="vol, IV rank")
    probe(f"vol_realized[{t}]", f"/api/stock/{t}/volatility/realized", need="RV cross-check")
    probe(f"iv_rank[{t}]", f"/api/stock/{t}/iv-rank", need="vol context")

# -- FRESHNESS TEST (the one that decides whether any of this works) -----------
# MCP returned SPY daily SMA/RSI ending 2026-07-01 with holes. If REST returns
# current bars, the staleness lives in the MCP layer and we are fine. If REST is
# equally stale, it is UW's data store and this whole approach is dead.
for t in ["SPY", "QQQ"]:
    probe(f"FRESHNESS_sma20[{t}]", f"/api/stock/{t}/technical-indicator/SMA",
          {"interval": "daily", "time_period": 20, "series_type": "close"},
          need="above_sma20 + FRESHNESS CHECK vs MCP's 2026-07-01 ceiling")
probe("FRESHNESS_sma50[SPY]", "/api/stock/SPY/technical-indicator/SMA",
      {"interval": "daily", "time_period": 50, "series_type": "close"},
      need="above_sma50, ma_rising")

# -- gamma regime --------------------------------------------------------------
for t in ETFS[:2]:
    probe(f"spot_exposures_strike[{t}]", f"/api/stock/{t}/spot-exposures/strike",
          need="gamma regime (pos/neg/thin)")
    probe(f"greek_exposure[{t}]", f"/api/stock/{t}/greek-exposure", need="gamma regime")

# -- skew ratio r --------------------------------------------------------------
probe("risk_reversal_skew[SPY]", "/api/stock/SPY/historical-risk-reversal-skew",
      need="r (put/call skew ratio)")
probe("greeks[SPY]", "/api/stock/SPY/greeks", need="r fallback from put/call IV")

# -- market context (nice to have, not required for IDX) -----------------------
probe("market_tide", "/api/market/market-tide", {"interval_5m": "false"},
      need="context / catalyst_adj sanity")
probe("sector_etfs", "/api/market/sector-etfs", need="sector bull/bear picks")


# ----------------------------------------------------------------------------
# sampling: keep payloads small but structurally complete
# ----------------------------------------------------------------------------
def sample(obj, max_items=2):
    """Trim a response to something readable while preserving all field names."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, list):
                out[k] = dict(
                    _list_len=len(v),
                    _first_items=[sample(x, max_items) for x in v[:max_items]],
                )
            elif isinstance(v, dict):
                out[k] = sample(v, max_items)
            else:
                out[k] = v
        return out
    if isinstance(obj, list):
        return dict(_list_len=len(obj),
                    _first_items=[sample(x, max_items) for x in obj[:max_items]])
    return obj


def date_range(obj):
    """Pull min/max of any date-ish field found — this is the freshness signal."""
    seen = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str) and k.lower() in (
                    "date", "timestamp", "executed_at", "start_time", "time", "expiry"
                ):
                    seen.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o[:5000]:
                walk(x)

    walk(obj)
    if not seen:
        return None
    return dict(count=len(seen), newest=max(seen), oldest=min(seen))


# ----------------------------------------------------------------------------
# run
# ----------------------------------------------------------------------------
def main():
    print(f"probe_uw.py — {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"base: {BASE}   token: ...{TOKEN[-4:]} (last 4 shown only)")
    print(f"python: {sys.executable}")
    print(f"TLS trust store: {SSL_SOURCE}\n")

    # --- preflight: one cheap call to separate "no trust store" from real errors.
    # Without this the script fires 32 requests that all fail identically and
    # buries the single actual cause under 32 copies of the same message.
    st, _, err = get("/api/market/market-tide", {"interval_5m": "false"})
    if err and "CERTIFICATE_VERIFY_FAILED" in err:
        print("STOPPED — this Python has no usable root certificates.\n")
        print("Every HTTPS request will fail until that's fixed. Nothing is")
        print("wrong with your token or with Unusual Whales.\n")
        print("Fix it by running Apple's/python.org's certificate installer:\n")
        found = False
        import glob as _g
        for p in sorted(_g.glob("/Applications/Python*/Install Certificates.command")):
            print(f'    "{p}"')
            found = True
        if not found:
            print("    (no python.org installer found on this Mac)")
            print("    Alternative — install the certifi bundle:")
            print(f"    {sys.executable} -m pip install --user certifi")
        print("\nThen run this probe again.")
        sys.exit(2)
    if st == 401 or st == 403:
        print(f"STOPPED — UW rejected the token (HTTP {st}).")
        print("The token in .uw_token is wrong, expired, or lacks API access.")
        print("Delete .uw_token and run again to re-enter it.")
        sys.exit(3)
    print(f"preflight ok (market-tide -> HTTP {st})\n")
    time.sleep(PAUSE)

    # 1. OpenAPI spec — the authoritative endpoint list, worth more than any probe
    print("[1/2] fetching OpenAPI spec ...", end=" ", flush=True)
    status, body, err = get("/api/openapi", accept_json=False)
    if status == 200 and body:
        p = os.path.join(HERE, "uw_openapi.yaml")
        open(p, "w", encoding="utf-8").write(body)
        print(f"ok -> uw_openapi.yaml ({len(body):,} bytes)")
    else:
        print(f"FAILED ({err or status})")
    time.sleep(PAUSE)

    # 2. endpoint probes
    print(f"[2/2] probing {len(PROBES)} endpoints ...\n")
    results = []
    for i, pr in enumerate(PROBES, 1):
        status, body, err = get(pr["path"], pr["params"])
        rec = dict(
            label=pr["label"], path=pr["path"], params=pr["params"],
            need=pr["need"], status=status, error=err,
            dates=date_range(body) if body is not None else None,
            sample=sample(body) if body is not None else None,
        )
        results.append(rec)
        mark = "ok " if status == 200 else "FAIL"
        extra = ""
        if rec["dates"]:
            extra = f"  newest={rec['dates']['newest']}"
        print(f"  {i:>2}/{len(PROBES)}  {mark}  {pr['label']:<34}{extra}")
        if err and status != 200:
            print(f"          {err[:160]}")
        time.sleep(PAUSE)

    out = dict(
        generated=datetime.now().isoformat(timespec="seconds"),
        base=BASE,
        note="Token deliberately excluded from this file.",
        results=results,
    )
    pj = os.path.join(HERE, "uw_probe_output.json")
    json.dump(out, open(pj, "w", encoding="utf-8"), indent=2, default=str)

    # human digest
    lines = [
        f"UW REST probe — {out['generated']}",
        "=" * 72,
        "",
        "FRESHNESS (the deciding question):",
        "  MCP's technical-indicator returned SPY daily bars ending 2026-07-01",
        "  with gaps. If REST shows a current date below, the staleness is in the",
        "  MCP layer and fetch_idx.py is viable. If REST matches 07-01, the",
        "  problem is UW's data and we need a different source for trend inputs.",
        "",
    ]
    for r in results:
        if r["label"].startswith("FRESHNESS"):
            d = r["dates"]
            lines.append(
                f"  {r['label']:<28} status={r['status']}  "
                + (f"newest={d['newest']}  oldest={d['oldest']}  n={d['count']}"
                   if d else "no dates found")
            )
    lines += ["", "ALL ENDPOINTS:", "-" * 72]
    for r in results:
        d = r["dates"]
        lines.append(f"[{r['status']}] {r['label']}")
        lines.append(f"      path: {r['path']}")
        lines.append(f"      need: {r['need']}")
        if d:
            lines.append(f"      dates: newest={d['newest']} oldest={d['oldest']}")
        if r["error"]:
            lines.append(f"      error: {r['error'][:220]}")
        if r["sample"] is not None:
            s = json.dumps(r["sample"], default=str)
            lines.append(f"      keys: {s[:400]}")
        lines.append("")
    pt = os.path.join(HERE, "uw_probe_summary.txt")
    open(pt, "w", encoding="utf-8").write("\n".join(lines))

    ok = sum(1 for r in results if r["status"] == 200)
    print(f"\n{ok}/{len(results)} endpoints returned 200")
    print(f"wrote uw_probe_output.json and uw_probe_summary.txt")
    print("\nTell Claude it's done — the files are in this folder and readable from there.")


if __name__ == "__main__":
    main()

# NOTE ON THE HANDOFF ZIP -------------------------------------------------------
# Daily_AI_Gap_Risk_Report_DevHandoff_*.zip is built from this folder. Before the
# next zip, confirm .uw_token is excluded, or the token ships to GitLab. Add to
# .gitignore:   .uw_token
# and when zipping:            zip -r out.zip . -x '.uw_token' -x 'uw_probe_*'
