"""gap_feed.py — deterministic market inputs for the Gap Risk engine (v2 hybrid).

Levels, day %, moving-average trend flags, 5-day momentum and (pre-open only)
overnight futures come from yfinance — it carries the REAL indices (^RUT/^DJI),
sidestepping ETF-ratio drift. Implied vols and the dealer-gamma regime come
from the Unusual Whales REST API (v12 handoff, 2026-07-20): the index's own
option chain gives a true 30-day IV plus a 1-day IV for the calculator's
rest-of-day horizon, and greek-exposure gives a computed pos/neg/thin gamma
read. Every field degrades independently — UW down → yfinance vol-index quote
(marked est., no 1-day IV, gamma "thin"); a missing level → the model supplies
a searched estimate. Bad plumbing must never kill the run.

UW rules baked in (established empirically in the v12 handoff — see
tools/probe_uw.py): HTTP 200 with data:null means "not carried"; an explicit
User-Agent is mandatory (Cloudflare 1010 otherwise); /technical-indicator is
stale at source (we never call it — trend comes from local bar math).

Symbols:
  levels : ^GSPC (SPX) · ^NDX · ^DJI (DJX = DJI/100) · ^RUT     [yfinance]
  IV     : SPX→SPY · NDX→QQQ · IWM · DIA option chains          [UW, fallback yf]
  gamma  : same chains via /greek-exposure                       [UW, fallback thin]
  futures: ES=F · NQ=F · YM=F · RTY=F  (pre-open runs only)      [yfinance]
"""
from __future__ import annotations

import math
import os

INDEXES = {
    "spx": {"nm": "SPX", "sym": "^GSPC", "vol_sym": "^VIX", "vn": "VIX", "fut": "ES=F", "div": 1.0},
    "ndx": {"nm": "NDX", "sym": "^NDX", "vol_sym": "^VXN", "vn": "VXN", "fut": "NQ=F", "div": 1.0},
    "djx": {"nm": "DJX", "sym": "^DJI", "vol_sym": "^VXD", "vn": "VXD", "fut": "YM=F", "div": 100.0},
    "rut": {"nm": "RUT", "sym": "^RUT", "vol_sym": "^RVX", "vn": "RVX", "fut": "RTY=F", "div": 1.0},
}

# Tradeable ETF twin per index — its price feeds the report's ETF panels and
# the breakeven calculator's "ETF $" mode (index and ETF have no fixed ratio).
ETF = {"spx": "SPY", "ndx": "QQQ", "djx": "DIA", "rut": "IWM"}

# When a vol index can't be fetched, estimate it from VIX by the typical ratio
# (marked "est." downstream — the model may override with a searched value).
VOL_RATIO_VS_VIX = {"ndx": 1.25, "rut": 1.30, "djx": 0.92, "spx": 1.0}

# ── Unusual Whales layer (chain IV + gamma regime) ──────────────────────────
UW_BASE = "https://api.unusualwhales.com"
# Option-chain symbols per index: (primary, fallback). UW carries SPX/NDX
# natively; RUT/DJX chains come via the liquid ETF.
UW_OPT = {"spx": ("SPX", "SPY"), "ndx": ("NDX", "QQQ"),
          "rut": ("IWM", None), "djx": ("DIA", None)}


def _uw_get(path: str, params: dict | None = None):
    """GET a UW endpoint; None when unavailable, not entitled, or data:null."""
    token = os.environ.get("UW_API_KEY", "").strip()
    if not token:
        return None
    import requests
    headers = {
        "Authorization": f"Bearer {token}",
        "UW-CLIENT-API-ID": "100001",
        "Accept": "application/json",
        # Mandatory: without a real User-Agent, Cloudflare rejects the client
        # outright (Error 1010) before the request reaches UW.
        "User-Agent": "mwtc-gap-risk/2.0",
    }
    try:
        r = requests.get(UW_BASE + path, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
    except Exception as e:
        print(f"  [feed] uw {path} failed: {type(e).__name__}: {e}")
        return None
    d = body.get("data") if isinstance(body, dict) else None
    if d is None or (isinstance(d, (list, dict)) and len(d) == 0):
        return None  # the 200-with-data:null trap — absence, not an error
    return d


def uw_iv(key: str):
    """(30-day IV %, 1-day IV %, source symbol) from the interpolated-IV curve."""
    primary, fallback = UW_OPT[key]
    for sym in (primary, fallback):
        if not sym:
            continue
        d = _uw_get(f"/api/stock/{sym}/interpolated-iv")
        if not d:
            continue
        pts = []
        for row in d:
            try:
                pts.append((int(row["days"]), float(row["volatility"])))
            except (KeyError, TypeError, ValueError):
                continue
        if not pts:
            continue
        near30 = min(pts, key=lambda p: abs(p[0] - 30))
        near1 = min(pts, key=lambda p: abs(p[0] - 1))
        return round(near30[1] * 100, 2), round(near1[1] * 100, 2), sym
    return None, None, None


def uw_gamma(key: str):
    """Dealer-gamma regime: net (call+put) gamma vs its own 60-day typical
    magnitude — near-zero reads "thin" rather than being forced pos/neg."""
    primary, fallback = UW_OPT[key]
    for sym in (primary, fallback):
        if not sym:
            continue
        d = _uw_get(f"/api/stock/{sym}/greek-exposure")
        if not d:
            continue
        nets = []
        for row in d:
            try:
                nets.append(float(row["call_gamma"]) + float(row["put_gamma"]))
            except (KeyError, TypeError, ValueError):
                continue
        if not nets:
            continue
        latest = nets[-1]
        recent = sorted(abs(x) for x in nets[-60:])
        typical = recent[len(recent) // 2] if recent else 0.0
        if typical and abs(latest) < 0.25 * typical:
            return "thin", sym
        return ("pos" if latest > 0 else "neg"), sym
    return "thin", None


def _history(sym: str, period: str = "4mo"):
    import yfinance as yf
    h = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=False)
    if h is None or len(h) < 60:
        raise ValueError(f"{sym}: insufficient history ({0 if h is None else len(h)} rows)")
    return h["Close"]


# Cboe's own delayed (~15 min) quote for its volatility indices. Yahoo stopped
# carrying ^RVX, so RVX comes from Cboe first; the others keep Yahoo and fall
# back to Cboe. Display/anchor only, like every vol-index spot here.
CBOE_QUOTE = "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_{sym}.json"
CBOE_FIRST = {"RVX"}


def _cboe_vol_index(vn: str):
    """Latest Cboe quote for a vol index ('RVX'), or None on any failure."""
    import requests
    try:
        r = requests.get(CBOE_QUOTE.format(sym=vn), timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 (mwtc-gap-scout)",
                                  "Accept": "application/json"})
        r.raise_for_status()
        d = (r.json() or {}).get("data") or {}
        for k in ("current_price", "close", "prev_day_close"):
            try:
                p = float(d.get(k) or 0)
            except (TypeError, ValueError):
                continue
            if math.isfinite(p) and 3.0 <= p <= 200.0:
                return round(p, 2)
        print(f"  [feed] Cboe {vn} quote had no usable price")
    except Exception as e:
        print(f"  [feed] Cboe {vn} quote failed: {type(e).__name__}: {e}")
    return None


def _vol_index(meta: dict):
    """(spot, source) for an index's Cboe vol index, or (None, None)."""
    order = ("cboe", "yf") if meta["vn"] in CBOE_FIRST else ("yf", "cboe")
    for src in order:
        if src == "cboe":
            p = _cboe_vol_index(meta["vn"])
            if p is not None:
                return p, "cboe"
        else:
            try:
                return round(_last_price(meta["vol_sym"]), 2), "yf"
            except Exception as e:
                print(f"  [feed] {meta['vol_sym']} vol failed: {e}")
    return None, None


def _last_price(sym: str):
    import yfinance as yf
    t = yf.Ticker(sym)
    try:
        p = t.fast_info["last_price"]
        if p and p > 0:
            return float(p)
    except Exception:
        pass
    h = t.history(period="5d", interval="1d")
    if h is None or h.empty:
        raise ValueError(f"{sym}: no price")
    return float(h["Close"].iloc[-1])


def fetch_index(key: str, premarket: bool) -> dict:
    """One index's mechanical inputs. Any individual failure -> that field None."""
    meta = INDEXES[key]
    out = {
        "key": key, "nm": meta["nm"], "vn": meta["vn"],
        "lvl": None, "day": None, "est": True,
        "vol": None, "vol_live": False,
        "fut_pct": None,
        "above_sma20": None, "above_sma50": None, "ma_rising": None,
        "mom5_pct": None,
    }
    bar_date = None
    try:
        closes = _history(meta["sym"])
        bar_date = closes.index[-1].date()
        px = float(closes.iloc[-1]) / meta["div"]
        prev = float(closes.iloc[-2]) / meta["div"]
        out["lvl"] = round(px, 2)
        out["day"] = round((px / prev - 1.0) * 100.0, 2)
        out["est"] = False
        sma20 = float(closes.tail(20).mean()) / meta["div"]
        sma50 = float(closes.tail(50).mean()) / meta["div"]
        sma20_prev = float(closes.iloc[-25:-5].mean()) / meta["div"]
        out["above_sma20"] = px > sma20
        out["above_sma50"] = px > sma50
        out["ma_rising"] = sma20 > sma20_prev
        out["mom5_pct"] = round((px / (float(closes.iloc[-6]) / meta["div"]) - 1.0) * 100.0, 2)
    except Exception as e:
        print(f"  [feed] {meta['sym']} level/trend failed: {e}")
    # ETF twin (SPY/QQQ/IWM/DIA) for the ETF panels and the calculator's ETF-$
    # mode. Display only — it never feeds the band math. Read straight after the
    # index so the two are seconds apart, from the REGULAR-SESSION daily bar:
    # before the open that is the prior close, never an extended-hours print.
    # The ETF is used only if its bar is from the SAME SESSION as the index's;
    # otherwise the panel would pair one day's odds with another day's price.
    # Fail-safe: None drops that ETF's panel and disables the calculator toggle.
    out["etf_sym"], out["etf_spot"], out["etf_day"] = ETF.get(key), None, None
    try:
        ec = _history(ETF[key])
        etf_date = ec.index[-1].date()
        epx, eprev = float(ec.iloc[-1]), float(ec.iloc[-2])
        if bar_date is None:
            print(f"  [feed] {ETF[key]} skipped: no {meta['sym']} bar to match it to")
        elif etf_date != bar_date:
            print(f"  [feed] {ETF[key]} skipped: its last bar is {etf_date}, "
                  f"{meta['sym']}'s is {bar_date} — different sessions")
        elif epx > 0 and eprev > 0:
            out["etf_spot"] = round(epx, 2)
            out["etf_day"] = round((epx / eprev - 1.0) * 100.0, 2)
    except Exception as e:
        print(f"  [feed] {ETF.get(key)} ETF price failed: {e}")
    vx, vx_src = _vol_index(meta)
    if vx is not None:
        out["vol"], out["vol_live"] = vx, True
        if vx_src == "cboe":
            print(f"  [feed] {meta['vn']} {vx} (Cboe delayed quote)")
    if premarket:
        try:
            import yfinance as yf
            t = yf.Ticker(meta["fut"])
            fi = t.fast_info
            last, prev = fi["last_price"], fi["previous_close"]
            if last and prev:
                out["fut_pct"] = round((float(last) / float(prev) - 1.0) * 100.0, 2)
        except Exception as e:
            print(f"  [feed] {meta['fut']} futures failed: {e}")
    return out


def fetch_all(premarket: bool = False) -> dict:
    """All four indices, hybrid: yfinance levels/trend/futures + UW chain IV
    and gamma regime. Vol preference: UW 30-day chain IV (live, brings a 1-day
    IV too) → yfinance vol-index quote → VIX-ratio estimate (marked est)."""
    data = {k: fetch_index(k, premarket) for k in INDEXES}
    for k, d in data.items():
        d["vol1d"], d["gamma"], d["vol_src"] = None, "thin", None
        # Real CBOE vol-index spot (v13 fix): DISPLAY ONLY, never feeds the
        # band math. fetch_index put the yfinance quote in d["vol"]; keep it
        # as the reference spot before chain IV takes over the model vol.
        d["vx_spot"] = d["vol"] if d["vol_live"] else None
        d["vx1d_spot"] = None
        if k == "spx":
            try:
                d["vx1d_spot"] = round(_last_price("^VIX1D"), 2)
            except Exception as e:
                print(f"  [feed] ^VIX1D spot failed: {e}")
        iv30, iv1d, src = uw_iv(k)
        if iv30 is not None:
            d["vol"], d["vol_live"], d["vol1d"] = iv30, True, iv1d
            d["vol_src"] = f"uw:{src}"
        elif d["vol"] is not None:
            d["vol_src"] = "yf:" + d["vn"]
        regime, gsrc = uw_gamma(k)
        d["gamma"] = regime
        if gsrc:
            d["gamma_src"] = gsrc
    vix = data["spx"]["vol"] if data["spx"]["vol_live"] else None
    for k, d in data.items():
        if d["vol"] is None and vix is not None:
            d["vol"] = round(vix * VOL_RATIO_VS_VIX[k], 1)
            d["vol_src"] = "est:VIX-ratio"
    print("[feed] ETF prices: " + ", ".join(
        f"{d.get('etf_sym')} {d['etf_spot']}" if d.get("etf_spot") else f"{d.get('etf_sym')} MISSING"
        for d in data.values()))
    ok = sum(1 for d in data.values() if d["lvl"] is not None)
    print(f"[feed] levels ok for {ok}/4; "
          + ", ".join(f"{d['nm']} vol={d['vol_src'] or 'MISSING'}"
                      f"{'+1d' if d['vol1d'] else ''} gamma={d['gamma']}"
                      f" vx={d['vx_spot'] or 'n/a'}"
                      + (f"/{d['vx1d_spot']}" if d.get("vx1d_spot") else "")
                      for d in data.values()))
    return data
