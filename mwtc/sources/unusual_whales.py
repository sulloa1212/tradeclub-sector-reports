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
    return _get("/api/market/market-tide", {"interval_5m": "false"})


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
            net_prem[t] = np_
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


def collect(tickers: list[str], etf_spots: Optional[dict] = None) -> dict:
    """Pull the full institutional packet. Values are None where unavailable so the
    report can clearly mark missing sections. ``etf_spots`` maps ticker->spot for
    the expected-move calc."""
    etf_spots = etf_spots or {}
    packet: dict = {
        "available": bool(config.UW_API_KEY),
        "market_tide": market_tide(),
        "dark_pool_recent": dark_pool_recent(),
        "flow_alerts_market": flow_alerts(),
        "unusual_screener": unusual_screener(),
        "options_intel": options_intel(config.OPTIONS_IV_UNIVERSE,
                                       config.OPTIONS_TOP_STRIKE_TICKERS),
        "news_headlines": news_headlines(),
        "insider": insider_transactions(),
        "congress": congress_trades(),
        "per_ticker": {},
    }
    for t in tickers:
        packet["per_ticker"][t] = {
            "gex_by_strike": gex_by_strike(t),
            "options_volume": options_volume(t),
            "dark_pool": dark_pool(t),
            "flow_alerts": flow_alerts(t),
            "interpolated_iv": interpolated_iv(t),
            "expected_move": expected_move(t, etf_spots.get(t)),
        }
    return packet
