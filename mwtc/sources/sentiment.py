"""Sentiment feeds: CNN Fear & Greed (free, no key) + AAII bull/bear via
Nasdaq Data Link (free key). Both fail safe to None."""
from __future__ import annotations

import logging
from typing import Optional

import requests

from .. import config

log = logging.getLogger("sentiment")

# CNN's data-viz backend powers the public Fear & Greed page. No key; needs a
# browser-like UA. Unofficial but stable for years.
FNG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
FNG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


def fear_greed() -> Optional[dict]:
    try:
        r = requests.get(FNG_URL, headers=FNG_HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        fg = data.get("fear_and_greed", {})
        score = fg.get("score")
        if score is None:
            return None
        return {
            "score": round(float(score), 1),
            "rating": fg.get("rating"),
            "previous_close": fg.get("previous_close"),
            "previous_1_week": fg.get("previous_1_week"),
        }
    except Exception as e:  # noqa: BLE001
        log.warning("Fear & Greed fetch failed: %s", e)
        return None


def aaii() -> Optional[dict]:
    """AAII bull/neutral/bear via Nasdaq Data Link (dataset AAII/AAII_SENTIMENT)."""
    if not config.NASDAQ_DATA_LINK_API_KEY:
        return None
    try:
        r = requests.get(
            "https://data.nasdaq.com/api/v3/datasets/AAII/AAII_SENTIMENT.json",
            params={"rows": 1, "api_key": config.NASDAQ_DATA_LINK_API_KEY},
            timeout=30,
        )
        r.raise_for_status()
        ds = r.json().get("dataset", {})
        cols = ds.get("column_names", [])
        rows = ds.get("data", [])
        if not rows:
            return None
        latest = dict(zip(cols, rows[0]))
        return {
            "date": latest.get("Date"),
            "bullish": latest.get("Bullish"),
            "neutral": latest.get("Neutral"),
            "bearish": latest.get("Bearish"),
            "bull_bear_spread": latest.get("Bull-Bear Spread"),
        }
    except Exception as e:  # noqa: BLE001
        log.warning("AAII fetch failed: %s", e)
        return None


def collect() -> dict:
    return {"fear_greed": fear_greed(), "aaii": aaii()}
