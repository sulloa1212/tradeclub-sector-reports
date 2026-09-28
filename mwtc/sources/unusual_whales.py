"""Unusual Whales REST client.

Uses the REST API (not the MCP server) because the MCP's multi-command tools
publish an empty input schema and fail in hosted clients, and the MCP is
desktop-only and cannot run in GitHub Actions.

Endpoint list verified against https://unusualwhales.com/skill.md (June 2026).
Only whitelisted endpoints are used — per UW's own anti-hallucination guidance,
a URL not on that list does not exist. All endpoints are GET. Auth = Bearer token
+ the required client header. Every fetch degrades to None on failure.
"""
from __future__ import annotations

import json
import time
import math
import logging
from typing import Any, Optional

import requests

from .. import config

log = logging.getLogger("uw")

BASE = "https://api.unusualwhales.com"
HEADERS = {
    "Authorization": f"Bearer {config.UW_API_KEY}",
    "UW-CLIENT-API-ID": "100001",  # required per UW skill spec
    "Accept": "application/json",
}
TIMEOUT = 30
MAX_RETRIES = 3


def _get(path: str, params: Optional[dict] = None) -> Optional[Any]:
    """GET a UW endpoint, returning the parsed ``data`` field or None on failure."""
    if not config.UW_API_KEY:
        log.warning("UW_API_KEY not set; skipping %s", path)
        return None
    url = f"{BASE}{path}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
            if r.status_code == 429:
                wait = 2 ** attempt
                log.warning("429 on %s, retry in %ss", path, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            payload = r.json()
            return payload.get("data", payload) if isinstance(payload, dict) else payload
        except Exception as e:  # noqa: BLE001
            log.warning("UW fetch failed (%s) attempt %d: %s", path, attempt, e)
            if attempt == MAX_RETRIES:
                return None
            time.sleep(1.5 * attempt)
    return None


# --- Options / institutional ---------------------------------------------------

def market_tide() -> Optional[Any]:
    """Market-wide net premium tide. 5-minute intervals (UW default is 1-minute):
    a full session is ~80 rows instead of ~400, so the whole day INCLUDING THE
    CLOSE fits under the size cap. At 1-minute the prefix clamp was cutting the
    last ~40 minutes of the session out of the post-market report."""
    return _get("/api/market/market-tide", {"interval_5m": "true"})


def gex_by_strike(ticker: str) -> Optional[Any]:
    """Spot gamma exposure by strike — GEX / gamma-flip."""
    return _get(f"/api/stock/{ticker}/spot-exposures/strike")


def greeks(ticker: str) -> Optional[Any]:
    return _get(f"/api/stock/{ticker}/greeks")


def options_volume(ticker: str) -> Optional[Any]:
    """Options volume incl. put/call ratio."""
    return _get(f"/api/stock/{ticker}/options-volume")


def interpolated_iv(ticker: str) -> Optional[Any]:
    """Interpolated IV / percentile — vol & expected-move context."""
    return _get(f"/api/stock/{ticker}/interpolated-iv")


def dark_pool(ticker: str, limit: int = 20) -> Optional[Any]:
    return _get(f"/api/darkpool/{ticker}", {"limit": limit})


def dark_pool_recent(limit: int = 30) -> Optional[Any]:
    return _get("/api/darkpool/recent", {"limit": limit})


def flow_alerts(ticker: Optional[str] = None, limit: int = 25,
                min_premium: int = 100_000) -> Optional[Any]:
    """Unusual options activity (smart-money flow)."""
    params: dict = {"limit": limit, "min_premium": min_premium}
    if ticker:
        params["ticker_symbol"] = ticker
    return _get("/api/option-trades/flow-alerts", params)


def unusual_screener(limit: int = 40, min_premium: int = 250_000) -> Optional[Any]:
    """Hottest / unusual option contracts market-wide (screener). vol_greater_oi
    surfaces fresh positioning (today's volume above existing OI = new bets)."""
    return _get("/api/screener/option-contracts",
                {"limit": limit, "min_premium": min_premium, "vol_greater_oi": "true"})


def option_contracts(ticker: str) -> Optional[Any]:
    """Per-ticker option contract list w/ volume + OI per strike/expiry. Whitelisted
    endpoint (NOT the blacklisted /options). Used to find a name's busiest strikes."""
    return _get(f"/api/stock/{ticker}/option-contracts")


def net_prem_ticks(ticker: str) -> Optional[Any]:
    """Net premium ticks — directional whale pressure (net call vs put premium)."""
    return _get(f"/api/stock/{ticker}/net-prem-ticks")


# --- Whale-intel parsing/derivation -------------------------------------------
# Field names across UW payloads aren't fully pinned in the public spec, so every
# extractor tries a list of candidate keys and degrades to None — never invents a
# value. Shapes are confirmed on the first live run; until then this fails safe.

def _first(d: dict, keys, cast=float):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            try:
                return cast(d[k])
            except (TypeError, ValueError):
                pass
    return None


_OCC_RE = None

def _parse_occ(sym: str):
    """Parse an OCC option symbol -> (underlying, expiry ISO, type, strike).
    e.g. 'AAPL  240119C00150000' -> ('AAPL','2026-01-19'? ,'C',150.0). Returns
    (None, None, None, None) if it doesn't match."""
    global _OCC_RE
    if _OCC_RE is None:
        import re
        _OCC_RE = re.compile(r"^([A-Z]{1,6})\s*(\d{6})([CP])(\d{8})$")
    if not isinstance(sym, str):
        return (None, None, None, None)
    m = _OCC_RE.match(sym.strip().replace(" ", ""))
    if not m:
        return (None, None, None, None)
    root, ymd, cp, strike = m.groups()
    try:
        yy, mm, dd = int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6])
        expiry = f"20{yy:02d}-{mm:02d}-{dd:02d}"
    except ValueError:
        expiry = None
    return (root, expiry, cp, int(strike) / 1000.0)


def _norm_contract(r: dict) -> Optional[dict]:
    """Normalize a contract row (from the screener or a per-ticker list) to a
    stable shape: ticker, option_symbol, type, strike, expiry, volume, oi,
    vol_oi_ratio, premium."""
    if not isinstance(r, dict):
        return None
    sym = (r.get("option_symbol") or r.get("option") or r.get("symbol") or "")
    o_root, o_exp, o_cp, o_strike = _parse_occ(sym)
    ticker = (r.get("ticker_symbol") or r.get("ticker") or r.get("underlying_symbol")
              or o_root)
    vol = _first(r, ("volume", "total_volume", "day_volume", "ask_side_volume"))
    oi = _first(r, ("open_interest", "oi", "prev_oi", "prev_open_interest"))
    prem = _first(r, ("total_premium", "premium", "prem"))
    ratio = _first(r, ("volume_oi_ratio", "vol_oi_ratio"))
    if ratio is None and vol and oi:
        ratio = round(vol / oi, 2) if oi else None
    ctype = (r.get("type") or r.get("option_type") or
             ("call" if o_cp == "C" else "put" if o_cp == "P" else None))
    strike = _first(r, ("strike", "strike_price")) or o_strike
    expiry = (r.get("expiry") or r.get("expiration") or r.get("expiration_date") or o_exp)
    if ticker is None and strike is None and vol is None:
        return None
    return {
        "ticker": ticker, "option_symbol": sym or None,
        "type": (str(ctype).lower() if ctype else None),
        "strike": strike, "expiry": expiry,
        "volume": int(vol) if vol is not None else None,
        "open_interest": int(oi) if oi is not None else None,
        "vol_oi_ratio": ratio,
        "premium": prem,
    }


def _extract_iv_percentile(payload: Any) -> Optional[float]:
    """Pull IV percentile/rank (0-100) from the interpolated-iv payload."""
    keys = ("iv_percentile", "iv_rank", "iv30_percentile", "iv_pctile",
            "percentile", "iv_rank_252", "rank")

    def from_dict(d: dict):
        v = _first(d, keys)
        if v is None:
            return None
        return round(v * 100, 1) if v <= 1 else round(v, 1)  # normalize 0-1 -> 0-100

    if isinstance(payload, dict):
        return from_dict(payload)
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict):
                v = from_dict(row)
                if v is not None:
                    return v
    return None


def iv_rank_lists(universe: list[str], top: int = 10) -> Optional[dict]:
    """Scan the universe's IV percentile and split into ELEVATED (high end of the
    annual range) and LOW (low end) lists. Derived — UW has no market IV screener."""
    rows = []
    for t in universe:
        payload = interpolated_iv(t)
        pct = _extract_iv_percentile(payload)
        iv = _extract_atm_iv(payload)
        if pct is None:
            continue
        rows.append({"ticker": t, "iv_percentile": pct,
                     "atm_iv": round(iv, 4) if iv is not None else None})
    if not rows:
        return None
    rows.sort(key=lambda r: r["iv_percentile"])
    return {
        "elevated": list(reversed(rows[-top:])),
        "low": rows[:top],
        "scanned": len(rows),
        "universe_note": (f"Ranked across {len(rows)} liquid optionable names "
                          f"(UW has no market-wide IV screener; this is a scanned set)."),
    }


def top_strikes(ticker: str, n: int = 5) -> Optional[dict]:
    """A name's busiest strikes by VOLUME and by OPEN INTEREST."""
    rows = option_contracts(ticker)
    if not isinstance(rows, list):
        return None
    norm = [c for c in (_norm_contract(r) for r in rows) if c]
    by_vol = sorted((c for c in norm if c["volume"] is not None),
                    key=lambda c: c["volume"], reverse=True)[:n]
    by_oi = sorted((c for c in norm if c["open_interest"] is not None),
                   key=lambda c: c["open_interest"], reverse=True)[:n]
    if not by_vol and not by_oi:
        return None
    return {"ticker": ticker, "by_volume": by_vol, "by_open_interest": by_oi}


def options_intel(iv_universe: list[str], strike_tickers: list[str]) -> Optional[dict]:
    """Whale-activity intel: IV elevated/low lists, unusual high-vol/OI contracts
    (each carries its strike), per-name busiest strikes, and net-premium pressure.
    Fails safe to None without a key."""
    if not config.UW_API_KEY:
        return None
    unusual = unusual_screener()
    unusual_norm = ([c for c in (_norm_contract(r) for r in unusual) if c]
                    if isinstance(unusual, list) else None)
    strikes = {}
    for t in strike_tickers:
        ts = top_strikes(t)
        if ts:
            strikes[t] = ts
    net_prem = {}
    for t in strike_tickers:
        np_ = net_prem_ticks(t)
        if np_ is not None:
            net_prem[t] = _bucket_net_prem(f"net_prem_ticks.{t}", np_)
    return {
        "iv_rank": iv_rank_lists(iv_universe),
        "unusual_contracts": unusual_norm,
        "top_strikes": strikes or None,
        "net_prem_ticks": net_prem or None,
    }


# --- Other data ----------------------------------------------------------------

def news_headlines(limit: int = 30) -> Optional[Any]:
    return _get("/api/news/headlines", {"limit": limit})


def insider_transactions(limit: int = 30) -> Optional[Any]:
    return _get("/api/insider/transactions", {"limit": limit})


def congress_trades(limit: int = 20) -> Optional[Any]:
    return _get("/api/congress/recent-trades", {"limit": limit})


def earnings(ticker: str) -> Optional[Any]:
    return _get(f"/api/stock/{ticker}/earnings")


# --- Derived: expected (implied) move -----------------------------------------

def _extract_atm_iv(iv_payload: Any) -> Optional[float]:
    """Best-effort: pull a representative ATM annualized IV (decimal) from the
    interpolated-iv response, whose exact shape is confirmed on first live run.
    Returns None if no plausible IV field is found."""
    candidates = ("atm_iv", "implied_volatility", "iv", "interpolated_iv", "iv_atm")

    def from_dict(d: dict) -> Optional[float]:
        for k in candidates:
            if k in d:
                try:
                    v = float(d[k])
                    return v / 100 if v > 3 else v  # normalize % -> decimal
                except (TypeError, ValueError):
                    pass
        return None

    if isinstance(iv_payload, dict):
        return from_dict(iv_payload)
    if isinstance(iv_payload, list):
        for row in iv_payload:
            if isinstance(row, dict):
                v = from_dict(row)
                if v is not None:
                    return v
    return None


def expected_move(ticker: str, spot: Optional[float]) -> Optional[dict]:
    """1-day and 1-week expected move from ATM IV: move = spot * IV * sqrt(t).
    Best-effort; returns None if IV or spot unavailable."""
    if not spot:
        return None
    iv = _extract_atm_iv(interpolated_iv(ticker))
    if iv is None:
        return None
    one_day = spot * iv * math.sqrt(1 / 252)
    one_week = spot * iv * math.sqrt(5 / 252)
    return {
        "atm_iv_annual": round(iv, 4),
        "expected_move_1d": round(one_day, 2),
        "expected_move_1d_pct": round(one_day / spot * 100, 2),
        "expected_move_1w": round(one_week, 2),
        "expected_move_1w_pct": round(one_week / spot * 100, 2),
        "spot_used": spot,
    }


def _clamp(name: str, v: Any, char_cap: int = 60_000) -> Any:
    """Hard CLIENT-SIDE cap on one endpoint's payload. Never trust the server's
    `limit` param: on 2026-09-24 an endpoint started returning a huge payload
    regardless of it, the institutional block hit 2.2M chars (~630K tokens),
    and two reports 400-failed on the model's context limit. Lists keep their
    newest-first prefix (binary-searched to the budget); dicts clamp their
    children; an oversized scalar is dropped. 60K chars ≈ 17K tokens — far
    above any normal payload here (usual limits are 20-40 rows)."""
    if v is None:
        return None
    try:
        size = len(json.dumps(v, default=str))
        if size <= char_cap:
            return v
        if isinstance(v, list):
            lo, hi = 0, len(v)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if len(json.dumps(v[:mid], default=str)) <= char_cap:
                    lo = mid
                else:
                    hi = mid - 1
            log.warning("UW %s clamped client-side: %s chars / %d rows -> %d rows",
                        name, f"{size:,}", len(v), lo)
            return v[:lo]
        if isinstance(v, dict):
            child_cap = max(char_cap // max(len(v), 1), 8_000)
            log.warning("UW %s dict payload %s chars — clamping children to %s",
                        name, f"{size:,}", f"{child_cap:,}")
            return {k: _clamp(f"{name}.{k}", x, child_cap) for k, x in v.items()}
        log.warning("UW %s oversized scalar dropped (%s chars)", name, f"{size:,}")
        return None
    except Exception as e:  # noqa: BLE001 — the clamp must never kill a fetch
        log.warning("UW clamp skipped for %s: %s", name, e)
        return v


def _num(x) -> Optional[float]:
    """Parse a UW number (they arrive as JSON strings). None unless finite."""
    try:
        if x is None or isinstance(x, bool):
            return None
        v = float(str(x).replace(",", "").strip())
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _jsize(v) -> int:
    return len(json.dumps(v, default=str))


def _sample_series(name: str, rows: Any, char_cap: int = 60_000) -> Any:
    """Trim a TIME SERIES by even sampling, never by prefix. UW does not
    document the sort order of its series, so a prefix could be dropping either
    the open or the close; evenly spaced rows with the first and last always
    kept preserve the shape of the session whichever way it is sorted."""
    if not isinstance(rows, list) or len(rows) < 3:
        return _clamp(name, rows, char_cap)
    try:
        if _jsize(rows) <= char_cap:
            return rows
        n = len(rows)

        def pick(m: int) -> list:
            idx = sorted({round(i * (n - 1) / (m - 1)) for i in range(m)})
            return [rows[i] for i in idx]

        lo, hi = 2, n
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if _jsize(pick(mid)) <= char_cap:
                lo = mid
            else:
                hi = mid - 1
        out = pick(lo)
        while len(out) > 2 and _jsize(out) > char_cap:   # rows differ in size
            lo -= 1
            out = pick(lo)
        if _jsize(out) > char_cap:
            return _clamp(name, rows, char_cap)
        log.warning("UW %s sampled evenly: %d rows -> %d (first and last kept)",
                    name, n, len(out))
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("UW %s even sampling failed (%s) — prefix clamp instead", name, e)
        return _clamp(name, rows, char_cap)


# net-prem-ticks fields that are per-minute INCREMENTS and therefore summable.
_NP_SUM = ("net_call_premium", "net_put_premium", "net_call_volume", "net_put_volume",
           "net_delta", "call_volume", "put_volume", "call_volume_ask_side",
           "call_volume_bid_side", "put_volume_ask_side", "put_volume_bid_side")


def _bucket_net_prem(name: str, rows: Any, minutes: int = 30) -> Any:
    """Sum UW's per-minute net-premium ticks into buckets plus a session total.
    UW documents each tick as ONE MINUTE's increment ("to build a daily chart
    you would have to add the previous data to the current tick"), so the
    truthful compression is a sum. The old prefix clamp kept ~21 of ~390 rows:
    a 20-minute slice presented as the day's whale pressure.

    Only the most recent session in the payload is kept, every label carries
    its date, and the output says how much of the payload it covers. Unknown
    shapes are returned untouched for the size clamp to handle."""
    if not isinstance(rows, list) or not rows:
        return rows
    try:
        import datetime as _dt
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        parsed = []
        for r in rows:
            ts = r.get("tape_time") if isinstance(r, dict) else None
            if not ts or not isinstance(ts, str):
                continue
            try:
                t = _dt.datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
            except ValueError:
                continue
            if not isinstance(t, _dt.datetime):
                continue
            t = t.replace(tzinfo=et) if t.tzinfo is None else t.astimezone(et)
            parsed.append((t, r))
        if len(parsed) < max(1, (len(rows) + 1) // 2):
            log.warning("UW %s: tape_time missing/unparsable on most rows — left raw", name)
            return rows
        parsed.sort(key=lambda x: x[0])
        session = parsed[-1][0].date()
        ticks = [(t, r) for t, r in parsed if t.date() == session]
        span = (ticks[-1][0] - ticks[0][0]).total_seconds() / 60.0
        if span / minutes > 20:                 # extended tape: keep it compact
            minutes = 60
        buckets: dict = {}
        total = {k: 0.0 for k in _NP_SUM}
        seen = set()
        for t, r in ticks:
            m = t.hour * 60 + t.minute
            key = t.replace(hour=(m // minutes * minutes) // 60,
                            minute=(m // minutes * minutes) % 60,
                            second=0, microsecond=0)
            b = buckets.setdefault(key, {k: 0.0 for k in _NP_SUM})
            for k in _NP_SUM:
                v = _num(r.get(k))
                if v is not None:
                    b[k] += v
                    total[k] += v
                    seen.add(k)
        if not seen:
            log.warning("UW %s: none of the expected numeric fields present — left raw", name)
            return rows
        fields = [k for k in _NP_SUM if k in seen]
        rnd = lambda d: {k: round(d[k], 2) for k in fields}  # noqa: E731
        dropped = len(rows) - len(ticks)
        out = {
            "note": ("UW per-minute ticks SUMMED into %d-minute buckets (ET). "
                     "session_total is the sum of the %d ticks between first_tick_et "
                     "and last_tick_et; each bucket is that window's own flow, not "
                     "a running total." % (minutes, len(ticks))),
            "session_date": session.isoformat(),
            "ticks": len(ticks),
            "first_tick_et": ticks[0][0].strftime("%Y-%m-%d %H:%M"),
            "last_tick_et": ticks[-1][0].strftime("%Y-%m-%d %H:%M"),
            "session_total": rnd(total),
            "buckets": [{"from_et": k.strftime("%Y-%m-%d %H:%M"), **rnd(b)}
                        for k, b in sorted(buckets.items())],
        }
        if dropped:
            out["rows_not_included"] = dropped
            out["note"] += (" %d rows of the payload are NOT included (another "
                            "session's, or an unreadable time)." % dropped)
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("UW %s bucketing failed (%s) — left raw", name, e)
        return rows


# gex_by_strike rows carry 35 fields (charm/delta/gamma/vanna x ask/bid/oi/vol).
# The report discusses gamma only — its prompt forbids vanna/charm jargon — so
# the row is projected to these before trimming: ~205 chars instead of ~950,
# which is what lets ~150 strikes (about +/-9% on SPY) fit the same budget.
_GEX_KEEP = ("strike", "price", "time",
             "call_gamma_oi", "put_gamma_oi", "call_gamma_vol", "put_gamma_vol")
_GEX_GAMMA = _GEX_KEEP[3:]


def _project_gex(r: Any) -> Any:
    if not isinstance(r, dict) or not any(k in r for k in _GEX_GAMMA):
        return r            # unknown schema: keep the whole row rather than gut it
    return {k: r[k] for k in _GEX_KEEP if k in r}


def _near_spot(name: str, rows: Any, spot: Any = None,
               char_cap: int = 30_000) -> tuple:
    """Trim a STRIKE-ordered payload around the spot. Returns (rows, window).

    _clamp keeps a list's prefix. That is right for newest-first feeds and wrong
    for gex_by_strike, which UW returns ascending by strike: on 2026-09-28 the
    prefix kept the ~66 LOWEST of ~500 strikes and the report duly described
    SPY put gamma "from $200-$295" with SPY at $768.

    Rows are projected to the gamma fields, then the strikes nearest the spot
    are kept in their original order. `window` tells the model what range it is
    looking at, so it cannot mistake the edge of the window for a wall. The
    reference price is the caller's spot, else the median of the payload's own
    `price` field. If a window cannot be built (no usable strikes, no reference
    price, unexpected payload type) the ladder is DROPPED and both values are
    None: the report prints GEX as not connected rather than describing the
    lowest strikes of the chain as if they were the money."""
    if not isinstance(rows, list) or not rows:
        if rows:                       # a dict or scalar where a ladder should be
            log.warning("UW %s: unexpected payload type %s — GEX dropped for this ticker",
                        name, type(rows).__name__)
        return None, None
    try:
        total = len(rows)
        proj = [_project_gex(r) for r in rows]
        strikes = [(_num(r.get("strike")) if isinstance(r, dict) else None) for r in proj]
        usable = [i for i, k in enumerate(strikes) if k is not None]
        if len(usable) < max(1, (total + 1) // 2):
            log.warning("UW %s: strike missing/unparsable on most rows — GEX dropped for this ticker", name)
            return None, None
        prices = sorted(v for v in (_num(r.get("price")) for r in proj
                                    if isinstance(r, dict)) if v and v > 0)
        uw_px = prices[len(prices) // 2] if prices else None
        ref = _num(spot)
        if ref is None or ref <= 0:
            ref = uw_px
        elif uw_px and abs(ref - uw_px) / uw_px > 0.10:
            # a bad quote must not re-centre the ladder far from the money;
            # UW's own underlying price is what these strikes were computed at
            log.warning("UW %s: caller spot %.2f disagrees with UW price %.2f by "
                        ">10%% — using UW's", name, ref, uw_px)
            ref = uw_px
        if ref is None:
            log.warning("UW %s: no reference price — GEX dropped for this ticker", name)
            return None, None
        if _jsize(proj) <= char_cap:
            keep = list(range(total))
        else:
            order = sorted(usable, key=lambda i: (abs(strikes[i] - ref), i))
            lo, hi = 0, len(order)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if _jsize([proj[i] for i in sorted(order[:mid])]) <= char_cap:
                    lo = mid
                else:
                    hi = mid - 1
            keep = sorted(order[:lo])
        if not keep:
            # not even the nearest strike fits: better no GEX than the lowest
            # strikes of the chain dressed up as a window
            log.warning("UW %s: nearest row alone exceeds the budget — GEX dropped", name)
            return None, None
        out = [proj[i] for i in keep]
        ks = [strikes[i] for i in keep if strikes[i] is not None]
        all_lo, all_hi = min(strikes[i] for i in usable), max(strikes[i] for i in usable)
        window = {
            "spot_used": round(ref, 2),
            "rows_kept": len(out),
            "rows_returned_by_uw": total,
            "strike_min": min(ks),
            "strike_max": max(ks),
            "pct_below_spot": round((ref - min(ks)) / ref * 100, 1),
            "pct_above_spot": round((max(ks) - ref) / ref * 100, 1),
            "note": ("gex_by_strike is a WINDOW of the strikes nearest the spot, not "
                     "the whole chain. Never name a gamma wall or floor outside "
                     "strike_min..strike_max. A gamma-flip level cannot be derived "
                     "from this window."),
        }
        if total >= 500:
            window["uw_page_cap"] = ("UW returned its 500-row page limit; the highest "
                                     "strikes of the chain were not delivered")
        if not (all_lo <= ref <= all_hi):
            window["warning"] = ("spot lies outside the strike range UW returned; "
                                 "this window is one-sided")
        if len(out) < total:        # expected every day: INFO, not a warning
            log.info("UW %s window around spot %.2f: %d rows -> %d (strikes %g..%g)",
                     name, ref, total, len(out), min(ks), max(ks))
        return out, window
    except Exception as e:  # noqa: BLE001 — a failed trim must never kill the run
        log.warning("UW %s near-spot trim failed (%s) — GEX dropped for this ticker", name, e)
        return None, None


def collect(tickers: list[str], etf_spots: Optional[dict] = None) -> dict:
    """Pull the full institutional packet. Values are None where unavailable so the
    report can clearly mark missing sections. ``etf_spots`` maps ticker->spot for
    the expected-move calc. Every payload passes through _clamp — the server's
    row limits are advisory, ours are not."""
    etf_spots = etf_spots or {}
    packet: dict = {
        "available": bool(config.UW_API_KEY),
        "market_tide": _sample_series("market_tide", market_tide()),
        "dark_pool_recent": _clamp("dark_pool_recent", dark_pool_recent()),
        "flow_alerts_market": _clamp("flow_alerts_market", flow_alerts()),
        "unusual_screener": _clamp("unusual_screener", unusual_screener()),
        "options_intel": _clamp("options_intel",
                                options_intel(config.OPTIONS_IV_UNIVERSE,
                                              config.OPTIONS_TOP_STRIKE_TICKERS),
                                char_cap=200_000),
        "news_headlines": _clamp("news_headlines", news_headlines()),
        "insider": _clamp("insider", insider_transactions()),
        "congress": _clamp("congress", congress_trades()),
        "per_ticker": {},
    }
    for t in tickers:
        gex_rows, gex_window = _near_spot(f"{t}.gex_by_strike", gex_by_strike(t),
                                          etf_spots.get(t))
        packet["per_ticker"][t] = {
            "gex_by_strike": gex_rows,
            "gex_window": gex_window,
            "options_volume": _clamp(f"{t}.options_volume", options_volume(t)),
            "dark_pool": _clamp(f"{t}.dark_pool", dark_pool(t)),
            "flow_alerts": _clamp(f"{t}.flow_alerts", flow_alerts(t)),
            "interpolated_iv": _clamp(f"{t}.interpolated_iv", interpolated_iv(t)),
            "expected_move": expected_move(t, etf_spots.get(t)),
        }
    return packet
