"""Financial Modeling Prep (FMP) source — premarket movers, analyst actions,
market breadth, and the economic calendar. One key (FMP_API_KEY).

Uses the current **/stable/** API (the legacy /api/v3/ paths are deprecated and
return empty for new keys — confirmed by live testing June 2026). Endpoints and
field names verified live:
  - /stable/biggest-gainers, /stable/biggest-losers   movers
  - /stable/grades-latest-news                        analyst actions
  - /stable/economic-calendar                         econ calendar (tier-gated)
  - /stable/sp500-constituent + /stable/batch-quote   breadth (batch-quote is a
                                                      higher-tier endpoint; falls
                                                      back to the sector proxy)
Everything fails safe to None.
"""
from __future__ import annotations

import logging
import datetime as dt
from typing import Any, Optional

import requests

from .. import config

log = logging.getLogger("fmp")

BASE = "https://financialmodelingprep.com/stable"
TIMEOUT = 30


def _get(path: str, params: Optional[dict] = None) -> Optional[Any]:
    if not config.FMP_API_KEY:
        log.warning("FMP_API_KEY not set; skipping %s", path)
        return None
    params = {**(params or {}), "apikey": config.FMP_API_KEY}
    try:
        r = requests.get(f"{BASE}{path}", params=params, timeout=TIMEOUT)
        r.raise_for_status()
        body = r.text.strip()
        if not body:  # tier-gated endpoints return an empty body
            log.warning("FMP empty body (likely plan-gated): %s", path)
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("FMP fetch failed (%s): %s", path, e)
        return None


# Heuristic to keep only optionable common stocks: drop leveraged/inverse ETFs,
# units/rights/warrants, foreign OTC tickers, and sub-$5 microcaps (illiquid or
# no listed options). Not a guarantee — upgrade to a live options-chain check if
# the FMP plan supports it.
_EXCL_NAME = ("2X", "3X", "DAILY TARGET", "LEVERAGE", "PROSHARES", "DIREXION",
              "ULTRASHORT", "ULTRAPRO", "GRANITESHARES", " ETF", "RIGHTS",
              "UNITS", "WARRANT", "ACQUISITION CORP")


def _optionable(r: dict) -> bool:
    name = (r.get("name") or "").upper()
    sym = (r.get("symbol") or "")
    price = r.get("price") or 0
    if any(x in name for x in _EXCL_NAME):
        return False
    if len(sym) == 5 and sym.endswith("F"):   # foreign OTC (e.g. SNMYF)
        return False
    if sym.endswith(("R", "U", "W")) and len(sym) >= 5:  # rights/units/warrants
        return False
    if price and price < 5:
        return False
    return True


def _trim_movers(rows: Any, n: int = 8) -> Optional[list]:
    if not isinstance(rows, list):
        return None
    opt = [r for r in rows if _optionable(r)]
    return [{
        "ticker": r.get("symbol"),
        "name": r.get("name"),
        "price": r.get("price"),
        "pct_change": r.get("changesPercentage"),
        "change": r.get("change"),
        "exchange": r.get("exchange"),
    } for r in opt[:n]]


def gainers(n: int = 10) -> Optional[list]:
    return _trim_movers(_get("/biggest-gainers"), n)


def losers(n: int = 10) -> Optional[list]:
    return _trim_movers(_get("/biggest-losers"), n)


def analyst_actions(limit: int = 25) -> Optional[list]:
    """Market-wide recent upgrades/downgrades/initiations (grades-latest-news)."""
    rows = _get("/grades-latest-news", {"page": 0, "limit": limit})
    if not isinstance(rows, list):
        return None
    return [{
        "ticker": r.get("symbol"),
        "firm": r.get("gradingCompany"),
        "action": r.get("action"),
        "from_grade": r.get("previousGrade"),
        "to_grade": r.get("newGrade"),
        "note": r.get("newsTitle"),
        "price_when_posted": r.get("priceWhenPosted"),
        "date": r.get("publishedDate"),
    } for r in rows[:limit]]


def economic_calendar(days: int = 7) -> Optional[list]:
    """US economic events. NOTE: confirmed plan-gated on the current key (empty
    body) — returns None until the FMP tier includes the economic calendar."""
    today = dt.date.today()
    rows = _get("/economic-calendar",
                {"from": today.isoformat(),
                 "to": (today + dt.timedelta(days=days)).isoformat()})
    if not isinstance(rows, list):
        return None
    us = [r for r in rows if str(r.get("country", "")).upper() in ("US", "USA")]
    return us or rows


def insider_trading(limit: int = 50) -> Optional[list]:
    """Latest market-wide insider Form 4 activity. Open-market buys (P)/sells (S)
    are the high-signal rows; awards/tax-withholding are tagged lower priority."""
    rows = _get("/insider-trading/latest", {"page": 0, "limit": limit})
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows:
        ttype = (r.get("transactionType") or "")
        is_open_market = ttype.startswith(("P-", "S-"))
        out.append({
            "ticker": r.get("symbol"),
            "insider": r.get("reportingName"),
            "role": r.get("typeOfOwner"),
            "buy_sell": "BUY" if r.get("acquisitionOrDisposition") == "A" else "SELL",
            "transaction_type": ttype,
            "open_market": is_open_market,
            "shares": r.get("securitiesTransacted"),
            "price": r.get("price"),
            "date": r.get("transactionDate"),
        })
    # Surface open-market transactions first (higher signal than awards).
    out.sort(key=lambda x: (not x["open_market"]))
    return out


def earnings_calendar(days: int = 7) -> Optional[list]:
    """SECOND SOURCE for earnings (cross-check against Finnhub in sources/earnings.py).

    FMP /stable/earnings-calendar returns one row per company per report date with
    epsActual/epsEstimated/revenueActual/revenueEstimated. A non-null epsActual (or
    revenueActual) means the name has ALREADY reported — same deterministic signal
    the Finnhub path uses, from an independent provider. Normalized to a small,
    stable shape so the reconciler can compare apples to apples. Fails safe to None
    (key absent or plan-gated), in which case earnings stay single-source and every
    row is flagged unverified rather than asserted."""
    today = dt.date.today()
    rows = _get("/earnings-calendar",
                {"from": today.isoformat(),
                 "to": (today + dt.timedelta(days=days)).isoformat()})
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("symbol"):
            continue
        eps_act = r.get("epsActual")
        rev_act = r.get("revenueActual")
        out.append({
            "symbol": r.get("symbol"),
            "date": r.get("date"),
            "eps_actual": eps_act,
            "eps_estimate": r.get("epsEstimated"),
            "revenue_actual": rev_act,
            "revenue_estimate": r.get("revenueEstimated"),
            "reported": (eps_act is not None) or (rev_act is not None),
            "source": "FMP earnings-calendar",
        })
    return out or None


def _sp500_symbols() -> Optional[list]:
    rows = _get("/sp500-constituent")
    if not isinstance(rows, list):
        return None
    return [r.get("symbol") for r in rows if r.get("symbol")]


def market_breadth() -> Optional[dict]:
    """Full S&P 500 breadth via batch-quote (a higher-tier endpoint). Returns None
    when batch-quote isn't available on the plan — main.py then uses the free
    sector-ETF breadth proxy from macro.py instead."""
    symbols = _sp500_symbols()
    if not symbols:
        return None
    quotes: list[dict] = []
    for i in range(0, len(symbols), 100):
        part = symbols[i:i + 100]
        rows = _get("/batch-quote", {"symbols": ",".join(part)})
        if isinstance(rows, list):
            quotes.extend([r for r in rows if isinstance(r, dict)])
    if not quotes:
        return None  # batch-quote gated -> fall back to sector proxy
    n = adv = dec = a50 = a200 = hi = lo = 0
    for q in quotes:
        p = q.get("price")
        if p is None:
            continue
        n += 1
        c = q.get("change")
        if c is not None:
            adv += c > 0
            dec += c < 0
        if q.get("priceAvg50"):
            a50 += p > q["priceAvg50"]
        if q.get("priceAvg200"):
            a200 += p > q["priceAvg200"]
        if q.get("yearHigh") and p >= q["yearHigh"] * 0.99:
            hi += 1
        if q.get("yearLow") and p <= q["yearLow"] * 1.01:
            lo += 1
    if not n:
        return None
    return {
        "universe": "S&P 500", "count": n,
        "pct_above_50dma": round(a50 / n * 100, 1),
        "pct_above_200dma": round(a200 / n * 100, 1),
        "advancers": adv, "decliners": dec,
        "ad_ratio": round(adv / dec, 2) if dec else None,
        "new_highs_52w": hi, "new_lows_52w": lo,
        "mcclellan": None,
        "source": "FMP full S&P 500",
    }


def collect() -> dict:
    return {
        "available": bool(config.FMP_API_KEY),
        "gainers": gainers(),
        "losers": losers(),
        "analyst_actions": analyst_actions(),
        "breadth": market_breadth(),
        "economic_calendar": economic_calendar(),
        "earnings_calendar": earnings_calendar(),
        "insider": insider_trading(),
    }
