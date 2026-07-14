"""gap_feed.py — deterministic market inputs for the Gap Risk engine.

Pulls the four index levels, day %, vol indices, moving-average trend flags,
5-day momentum and (pre-open only) overnight futures from yfinance. Every
field degrades gracefully: a failed symbol returns None and the model step
fills the gap with a searched estimate marked "est." — bad plumbing must
never kill the run (the engine + model can carry on without any one input).

Symbols:
  levels : ^GSPC (SPX) · ^NDX · ^DJI (DJX = DJI/100) · ^RUT
  vols   : ^VIX (SPX) · ^VXN (NDX) · ^RVX (RUT) · ^VXD (DJX)
  futures: ES=F · NQ=F · YM=F · RTY=F  (pre-open runs only)
"""
from __future__ import annotations

import math

INDEXES = {
    "spx": {"nm": "SPX", "sym": "^GSPC", "vol_sym": "^VIX", "vn": "VIX", "fut": "ES=F", "div": 1.0},
    "ndx": {"nm": "NDX", "sym": "^NDX", "vol_sym": "^VXN", "vn": "VXN", "fut": "NQ=F", "div": 1.0},
    "djx": {"nm": "DJX", "sym": "^DJI", "vol_sym": "^VXD", "vn": "VXD", "fut": "YM=F", "div": 100.0},
    "rut": {"nm": "RUT", "sym": "^RUT", "vol_sym": "^RVX", "vn": "RVX", "fut": "RTY=F", "div": 1.0},
}

# When a vol index can't be fetched, estimate it from VIX by the typical ratio
# (marked "est." downstream — the model may override with a searched value).
VOL_RATIO_VS_VIX = {"ndx": 1.25, "rut": 1.30, "djx": 0.92, "spx": 1.0}


def _history(sym: str, period: str = "4mo"):
    import yfinance as yf
    h = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=False)
    if h is None or len(h) < 60:
        raise ValueError(f"{sym}: insufficient history ({0 if h is None else len(h)} rows)")
    return h["Close"]


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
    try:
        closes = _history(meta["sym"])
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
    try:
        out["vol"] = round(_last_price(meta["vol_sym"]), 2)
        out["vol_live"] = True
    except Exception as e:
        print(f"  [feed] {meta['vol_sym']} vol failed: {e}")
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
    """All four indices; fills missing vols from the VIX ratio (marked est)."""
    data = {k: fetch_index(k, premarket) for k in INDEXES}
    vix = data["spx"]["vol"] if data["spx"]["vol_live"] else None
    for k, d in data.items():
        if d["vol"] is None and vix is not None:
            d["vol"] = round(vix * VOL_RATIO_VS_VIX[k], 1)
    ok = sum(1 for d in data.values() if d["lvl"] is not None)
    print(f"[feed] levels ok for {ok}/4 indices; "
          + ", ".join(f"{d['nm']} vol={'live' if d['vol_live'] else ('est' if d['vol'] else 'MISSING')}"
                      for d in data.values()))
    return data
