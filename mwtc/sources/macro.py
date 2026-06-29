"""Free macro data layer — no API key required (yfinance).

Covers futures, indices, commodities, FX, crypto, VIX, rates, sector ETFs, and
computes rules-based technical levels. Optional Finnhub key adds the economic and
earnings calendars. Every fetch degrades to None on failure rather than crashing.
"""
from __future__ import annotations

import logging
import datetime as dt
from typing import Optional

import requests

from .. import config

log = logging.getLogger("macro")

try:
    import yfinance as yf
except Exception as e:  # noqa: BLE001
    yf = None
    log.warning("yfinance unavailable: %s", e)


def _quote(ticker: str) -> Optional[dict]:
    """Last close, prior close, % change, 60-day trend for one ticker."""
    if yf is None:
        return None
    try:
        hist = yf.Ticker(ticker).history(period="6mo", interval="1d")
        if hist is None or hist.empty or len(hist) < 2:
            return None
        closes = hist["Close"].dropna()
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2])
        chg = last - prev
        pct = (chg / prev * 100) if prev else 0.0
        return {
            "ticker": ticker,
            "last": round(last, 2),
            "prev_close": round(prev, 2),
            "change": round(chg, 2),
            "pct_change": round(pct, 2),
            "high_252": round(float(closes.tail(252).max()), 2),
            "low_252": round(float(closes.tail(252).min()), 2),
            "closes": [round(float(c), 2) for c in closes.tail(60)],
        }
    except Exception as e:  # noqa: BLE001
        log.warning("quote failed for %s: %s", ticker, e)
        return None


def _quote_group(mapping: dict[str, str]) -> dict[str, Optional[dict]]:
    return {name: _quote(tk) for name, tk in mapping.items()}


def _strip_closes(group: dict) -> dict:
    """Drop the 60-day closes array from a quote group to keep the packet lean
    (technicals already consumed them)."""
    out = {}
    for k, v in group.items():
        if isinstance(v, dict):
            out[k] = {kk: vv for kk, vv in v.items() if kk != "closes"}
        else:
            out[k] = v
    return out


def _technical_levels(q: Optional[dict]) -> Optional[dict]:
    """Rules-based support/resistance: 20/50-day MAs, classic pivots, 60-day swing,
    plus a 14-day RSI. Defined (not discretionary) so it's reproducible daily."""
    if not q or not q.get("closes") or len(q["closes"]) < 20:
        return None
    closes = q["closes"]
    last = closes[-1]
    ma20 = round(sum(closes[-20:]) / 20, 2)
    ma50 = round(sum(closes[-50:]) / 50, 2) if len(closes) >= 50 else None
    hi = max(closes[-60:])
    lo = min(closes[-60:])
    pivot = round((hi + lo + last) / 3, 2)
    r1 = round(2 * pivot - lo, 2)
    s1 = round(2 * pivot - hi, 2)
    trend = "up" if (ma50 and ma20 > ma50) else ("down" if ma50 and ma20 < ma50 else "flat")
    return {
        "last": last, "ma20": ma20, "ma50": ma50, "rsi14": _rsi(closes, 14),
        "pivot": pivot, "resistance_1": r1, "support_1": s1,
        "swing_high_60d": hi, "swing_low_60d": lo, "trend": trend,
    }


def _rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def sector_rotation() -> dict:
    """Rank 11 S&P sectors by 1-day and ~1-month relative performance."""
    out = {}
    for name, tk in config.SECTOR_ETFS.items():
        q = _quote(tk)
        if not q:
            out[name] = None
            continue
        closes = q.get("closes") or []
        mo = None
        if len(closes) >= 21 and closes[-21]:
            mo = round((closes[-1] - closes[-21]) / closes[-21] * 100, 2)
        out[name] = {"etf": tk, "pct_change_1d": q["pct_change"],
                     "pct_change_1mo": mo, "last": q["last"]}
    return out


def sector_breadth() -> Optional[dict]:
    """Free breadth PROXY from the 11 SPDR sector ETFs (no extra API key): how
    many sectors are above their own 50- and 200-day moving averages, and net
    advancers. A labeled stand-in for full S&P 500 breadth when the FMP batch
    endpoint isn't on the plan. (Not a McClellan/A-D substitute.)"""
    if yf is None:
        return None
    n = adv = dec = a50 = a200 = 0
    for name, tk in config.SECTOR_ETFS.items():
        try:
            hist = yf.Ticker(tk).history(period="1y", interval="1d")
            closes = hist["Close"].dropna().tolist() if hist is not None else []
        except Exception:  # noqa: BLE001
            closes = []
        if len(closes) < 60:
            continue
        n += 1
        if len(closes) >= 2:
            ch = closes[-1] - closes[-2]
            adv += ch > 0
            dec += ch < 0
        if closes[-1] > sum(closes[-50:]) / 50:
            a50 += 1
        if len(closes) >= 200 and closes[-1] > sum(closes[-200:]) / 200:
            a200 += 1
    if not n:
        return None
    return {
        "universe": "11 SPDR sectors (proxy)",
        "count": n,
        "sectors_above_50dma": a50,
        "sectors_above_200dma": a200,
        "advancers": adv,
        "decliners": dec,
        "note": "Sector-level proxy; full S&P 500 breadth needs an FMP batch-quote tier.",
        "source": "yfinance sector ETFs",
    }


def _finnhub_calendar(path: str, params: dict, key_in: str) -> Optional[list]:
    if not config.FINNHUB_API_KEY:
        return None
    try:
        params = {**params, "token": config.FINNHUB_API_KEY}
        r = requests.get(f"https://finnhub.io/api/v1{path}", params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data.get(key_in, data) if isinstance(data, dict) else data
    except Exception as e:  # noqa: BLE001
        log.warning("Finnhub %s failed: %s", path, e)
        return None


def economic_calendar() -> Optional[list]:
    """US economic events for the next 7 days (Finnhub, optional)."""
    today = dt.date.today()
    events = _finnhub_calendar(
        "/calendar/economic",
        {"from": today.isoformat(), "to": (today + dt.timedelta(days=7)).isoformat()},
        "economicCalendar",
    )
    if isinstance(events, list):
        us = [e for e in events if str(e.get("country", "")).upper() in ("US", "USA")]
        return us or events
    return events


def _enrich_earnings(r: dict, today_iso: str) -> dict:
    """Tag each row reported-vs-upcoming and compute beat/miss. The key fix:
    use epsActual/revenueActual to know a name has ALREADY reported, instead of
    blindly calling everything 'upcoming'."""
    eps_est, eps_act = r.get("epsEstimate"), r.get("epsActual")
    reported = eps_act is not None
    surprise = beat = None
    if reported and eps_est not in (None, 0):
        try:
            surprise = round((eps_act - eps_est) / abs(eps_est) * 100, 1)
            beat = "beat" if eps_act > eps_est else ("miss" if eps_act < eps_est else "inline")
        except (TypeError, ZeroDivisionError):
            pass
    hour = (r.get("hour") or "").lower()
    when = ("reported" if reported else
            "before open" if hour == "bmo" else
            "after close" if hour == "amc" else "time TBD")
    return {**r, "reported": reported, "eps_surprise_pct": surprise,
            "beat_miss": beat, "when": when, "is_today": r.get("date") == today_iso}


def earnings_calendar() -> Optional[list]:
    """Earnings for the next 7 days (Finnhub free tier — confirmed live), enriched
    with reported/upcoming status + beat/miss. Trims micro-cap noise, keeps the
    largest names by revenue, and surfaces today's names first."""
    today = dt.date.today()
    today_iso = today.isoformat()
    rows = _finnhub_calendar(
        "/calendar/earnings",
        {"from": today_iso, "to": (today + dt.timedelta(days=7)).isoformat()},
        "earningsCalendar",
    )
    if not isinstance(rows, list):
        return rows
    named = [r for r in rows if r.get("epsEstimate") is not None
             or r.get("revenueEstimate")]
    enriched = [_enrich_earnings(r, today_iso) for r in named]
    # Today's names first, then by revenue size.
    enriched.sort(key=lambda r: (not r["is_today"], -(r.get("revenueEstimate") or 0)))
    return enriched[:40] or [_enrich_earnings(r, today_iso) for r in rows[:40]]


_ECON_KEYWORDS = ("pce", "inflation", "jobless", "initial claims", "gdp",
                  "durable goods", "payroll", "nonfarm", "cpi", "ppi",
                  "retail sales", "fed", "fomc", "rate", "consumer sentiment")


def economic_news(limit: int = 15) -> Optional[list]:
    """Fallback for econ ACTUALS: recent general-market news (Finnhub free tier),
    filtered to economic-data headlines. When the structured calendar is gated or
    lags the 8:30 release, the report reads the printed numbers out of these
    headlines/summaries instead of inventing them. Needs FINNHUB_API_KEY."""
    if not config.FINNHUB_API_KEY:
        return None
    try:
        r = requests.get("https://finnhub.io/api/v1/news",
                         params={"category": "general", "token": config.FINNHUB_API_KEY},
                         timeout=30)
        r.raise_for_status()
        items = r.json()
        if not isinstance(items, list):
            return None
        import time as _t
        cutoff = _t.time() - 36 * 3600
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            text = f"{it.get('headline','')} {it.get('summary','')}".lower()
            if it.get("datetime", 0) < cutoff:
                continue
            if not any(k in text for k in _ECON_KEYWORDS):
                continue
            out.append({
                "headline": it.get("headline"),
                "summary": (it.get("summary") or "")[:280],
                "source": it.get("source"),
                "url": it.get("url"),
                "datetime": it.get("datetime"),
            })
        return out[:limit] or None
    except Exception as e:  # noqa: BLE001
        log.warning("economic_news fallback failed: %s", e)
        return None


def collect() -> dict:
    """Assemble the full free-macro packet."""
    indices = _quote_group(config.INDICES)
    technicals = {name: _technical_levels(q) for name, q in indices.items()}
    # ETF spots for the implied-move calc (kept separately).
    etf_spots = {t: _quote(t) for t in config.UW_FOCUS_TICKERS}
    return {
        "futures": _strip_closes(_quote_group(config.FUTURES)),
        "rates_vol": _strip_closes(_quote_group(config.RATES_VOL)),
        "commodities": _strip_closes(_quote_group(config.COMMODITIES)),
        "fx": _strip_closes(_quote_group(config.FX)),
        "crypto": _strip_closes(_quote_group(config.CRYPTO)),
        "indices": _strip_closes(indices),
        "global_indices": _strip_closes(_quote_group(config.GLOBAL_INDICES)),
        "sector_rotation": sector_rotation(),
        "technicals": technicals,
        "etf_spots": {t: (q or {}).get("last") for t, q in etf_spots.items()},
        "economic_calendar": economic_calendar(),
        "earnings_calendar": earnings_calendar(),
        "economic_news": economic_news(),
    }
