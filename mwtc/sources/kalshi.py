"""Kalshi — prediction-market odds for the next FOMC rate decision (FREE).

Kalshi's market-DATA endpoints are PUBLIC: reading events/markets needs no API
key and no request signing (only trading/portfolio calls do). So this fetcher
runs credential-free and just reads the KXFEDDECISION series — the mutually
exclusive hold / cut / hike buckets for the next FOMC meeting, whose YES prices
sum to ~1.0 and give a clean probability distribution to cross-reference against
CME FedWatch.

Flow: GET /events (next open meeting) -> GET /markets (per-bucket YES price).
Prices come back as fixed-point DOLLAR strings already in 0..1 (e.g. "0.8100" =
81% implied) — cast to float, do NOT multiply by 100. The whole module fails
safe to None so a Kalshi outage or schema change never blocks the report.

(No auth is needed for the above. config.KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY
exist only for future authenticated/portfolio endpoints and are unused here.)
"""
from __future__ import annotations

import logging
import datetime as dt
from typing import Optional

import requests

log = logging.getLogger("kalshi")

# external-api is recommended for new integrations; api.elections is the
# legacy-but-identical host kept as a fallback (it serves ALL markets, not just
# elections, despite the name). Tried in order until one answers.
BASE_URLS = [
    "https://external-api.kalshi.com/trade-api/v2",
    "https://api.elections.kalshi.com/trade-api/v2",
]
SERIES = "KXFEDDECISION"  # mutually-exclusive hold/cut/hike buckets (sum to ~1)
TIMEOUT = 30
HEADERS = {"Accept": "application/json", "User-Agent": "mwtc-report-bot/1.0"}


def _get(path: str, **params) -> dict:
    """GET a public Kalshi endpoint, trying each host until one succeeds."""
    last_err: Optional[Exception] = None
    for base in BASE_URLS:
        try:
            r = requests.get(base + path, params=params, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("no Kalshi host reachable")


def _f(val) -> Optional[float]:
    """Parse Kalshi's fixed-point dollar strings ('0.8100') to a float, else None."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _fmt_meeting(strike_date: Optional[str]) -> Optional[str]:
    """'2026-07-29T18:00:00Z' -> 'July 29, 2026' (best-effort, never raises)."""
    if not strike_date:
        return None
    try:
        d = dt.datetime.fromisoformat(strike_date.replace("Z", "+00:00"))
        return d.strftime("%B %d, %Y").replace(" 0", " ")
    except Exception:  # noqa: BLE001
        return strike_date


def fed_odds() -> Optional[dict]:
    """Prediction-market odds for the NEXT FOMC decision, or None on any failure.

    Returns a compact dict the model can read directly:
        {
          "source": "kalshi",
          "series": "KXFEDDECISION",
          "event_ticker": "KXFEDDECISION-26JUL",
          "meeting_date": "July 29, 2026",
          "title": "Fed decision in Jul 2026?",
          "top_outcome": "Fed maintains rate",
          "top_pct": 81,
          "markets": [   # sorted most-likely first
            {"outcome": "Fed maintains rate", "yes_pct": 81, "yes_mid": 0.815, "last": 0.81},
            ...
          ],
          "fetched_at": "2026-07-08T...Z",
        }
    """
    try:
        events = (_get("/events", series_ticker=SERIES, status="open",
                       limit=100) or {}).get("events") or []
        dated = [e for e in events if e.get("strike_date")]
        if not dated:
            return None
        nxt = min(dated, key=lambda e: e["strike_date"])
        event_ticker = nxt.get("event_ticker")
        if not event_ticker:
            return None

        markets = (_get("/markets", event_ticker=event_ticker) or {}).get("markets") or []
        out = []
        for m in markets:
            yb, ya = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
            last = _f(m.get("last_price_dollars"))
            # Midpoint of the YES bid/ask is the cleanest implied probability;
            # fall back to last trade if one side is missing.
            if yb is not None and ya is not None:
                mid = (yb + ya) / 2
            else:
                mid = last if last is not None else yb if yb is not None else ya
            if mid is None:
                continue
            out.append({
                "outcome": m.get("yes_sub_title") or m.get("subtitle") or m.get("ticker"),
                "yes_pct": round(mid * 100),
                "yes_mid": round(mid, 4),
                "last": last,
            })
        if not out:
            return None
        out.sort(key=lambda x: x["yes_mid"], reverse=True)

        return {
            "source": "kalshi",
            "series": SERIES,
            "event_ticker": event_ticker,
            "meeting_date": _fmt_meeting(nxt.get("strike_date")),
            "title": nxt.get("title"),
            "top_outcome": out[0]["outcome"],
            "top_pct": out[0]["yes_pct"],
            "markets": out,
            "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    except Exception as e:  # noqa: BLE001
        log.warning("Kalshi Fed-odds fetch failed: %s", e)
        return None
