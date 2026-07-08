"""`--check-keys` health check: pings each provider with a minimal call and
reports ✅/❌, so the first run on a real machine instantly shows which keys are
live. Anthropic uses the no-cost /v1/models endpoint (no token charge)."""
from __future__ import annotations

import requests

from . import config

TIMEOUT = 20


def _finnhub():
    if not config.FINNHUB_API_KEY:
        return None, "not set"
    r = requests.get("https://finnhub.io/api/v1/quote",
                     params={"symbol": "AAPL", "token": config.FINNHUB_API_KEY}, timeout=TIMEOUT)
    ok = r.status_code == 200 and "c" in r.json()
    return ok, f"HTTP {r.status_code}"


def _fmp():
    if not config.FMP_API_KEY:
        return None, "not set"
    r = requests.get("https://financialmodelingprep.com/stable/quote",
                     params={"symbol": "AAPL", "apikey": config.FMP_API_KEY}, timeout=TIMEOUT)
    ok = r.status_code == 200 and bool(r.text.strip()) and isinstance(r.json(), list) and r.json()
    return bool(ok), f"HTTP {r.status_code}"


def _fmp_econ():
    """Separate check: is the economic calendar included in this FMP plan?"""
    if not config.FMP_API_KEY:
        return None, "not set"
    import datetime as dt
    t = dt.date.today()
    r = requests.get("https://financialmodelingprep.com/stable/economic-calendar",
                     params={"from": t.isoformat(), "to": (t + dt.timedelta(days=3)).isoformat(),
                             "apikey": config.FMP_API_KEY}, timeout=TIMEOUT)
    ok = bool(r.text.strip()) and isinstance(r.json(), list) and len(r.json()) > 0
    return bool(ok), ("included" if ok else "plan-gated / empty")


def _nasdaq():
    if not config.NASDAQ_DATA_LINK_API_KEY:
        return None, "not set"
    r = requests.get("https://data.nasdaq.com/api/v3/datasets/AAII/AAII_SENTIMENT.json",
                     params={"rows": 1, "api_key": config.NASDAQ_DATA_LINK_API_KEY}, timeout=TIMEOUT)
    ok = r.status_code == 200 and bool(r.text.strip()) and "dataset" in r.json()
    return bool(ok), f"HTTP {r.status_code}"


def _uw():
    if not config.UW_API_KEY:
        return None, "not set"
    r = requests.get("https://api.unusualwhales.com/api/stock/AAPL/options-volume",
                     headers={"Authorization": f"Bearer {config.UW_API_KEY}",
                              "UW-CLIENT-API-ID": "100001", "Accept": "application/json"},
                     timeout=TIMEOUT)
    return r.status_code == 200, f"HTTP {r.status_code}"


def _anthropic():
    if not config.ANTHROPIC_API_KEY:
        return None, "not set"
    # /v1/models validates the key WITHOUT a generation charge.
    r = requests.get("https://api.anthropic.com/v1/models",
                     headers={"x-api-key": config.ANTHROPIC_API_KEY,
                              "anthropic-version": "2023-06-01"}, timeout=TIMEOUT)
    return r.status_code == 200, f"HTTP {r.status_code} (no token cost)"


def _cme():
    if not (config.CME_FEDWATCH_API_ID and config.CME_FEDWATCH_API_SECRET):
        return None, "not set (optional)"
    try:
        r = requests.post("https://auth.cmegroup.com/as/token.oauth2",
                          data={"grant_type": "client_credentials"},
                          auth=(config.CME_FEDWATCH_API_ID, config.CME_FEDWATCH_API_SECRET),
                          timeout=TIMEOUT)
        return r.status_code == 200, f"token HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__


def _kalshi():
    # Kalshi Fed-odds market data is PUBLIC — no key required; this pings it live.
    # The optional KALSHI_* key is only for future authenticated calls, so just
    # note whether it's set (never a pass/fail basis).
    auth = ("auth key set" if (config.KALSHI_API_KEY_ID and config.KALSHI_PRIVATE_KEY)
            else "public — no key needed")
    try:
        r = requests.get("https://external-api.kalshi.com/trade-api/v2/events",
                         params={"series_ticker": "KXFEDDECISION", "status": "open", "limit": 1},
                         headers={"Accept": "application/json"}, timeout=TIMEOUT)
        ok = r.status_code == 200 and isinstance(r.json().get("events"), list)
        return bool(ok), f"HTTP {r.status_code} · {auth}"
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__


CHECKS = [
    ("Unusual Whales (required)", _uw),
    ("Anthropic (required)", _anthropic),
    ("Finnhub — earnings cal", _finnhub),
    ("FMP — movers/analyst/breadth", _fmp),
    ("FMP — economic calendar", _fmp_econ),
    ("Nasdaq Data Link — AAII", _nasdaq),
    ("CME FedWatch (optional)", _cme),
    ("Kalshi Fed odds (optional)", _kalshi),
]


def run() -> int:
    print("\nMWTC bot — API key check\n" + "-" * 42)
    any_required_fail = False
    for label, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}"
        mark = "⬜ skip" if ok is None else ("✅ ok  " if ok else "❌ FAIL")
        print(f"{mark}  {label:32} {detail}")
        if ok is False and "required" in label:
            any_required_fail = True
    print("-" * 42)
    if any_required_fail:
        print("One or more REQUIRED keys failed — fix before running the report.\n")
        return 1
    print("Required keys look good. Optional/plan-gated items marked above.\n")
    return 0
