"""CME FedWatch — market-implied FOMC rate probabilities (OPTIONAL).

CME's FedWatch API uses OAuth 2.0 and requires an entitled API ID/secret
(paid; self-service onboarding). This is optional: if the credentials aren't set,
the report falls back to the qualitative Fed stance from the news feed.

Flow: POST id/secret to the token endpoint -> Bearer token -> GET probabilities.
Endpoint paths are confirmed against your CME entitlement on first live run; the
whole module fails safe to None.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from .. import config

log = logging.getLogger("fedwatch")

TOKEN_URL = "https://auth.cmegroup.com/as/token.oauth2"
# EOD FedWatch probabilities endpoint (entitlement-specific; adjust to your plan).
DATA_URL = "https://markets.api.cmegroup.com/fedwatch/v1/probabilities"
TIMEOUT = 30


def _token() -> Optional[str]:
    cid = config.CME_FEDWATCH_API_ID
    secret = config.CME_FEDWATCH_API_SECRET
    if not (cid and secret):
        return None
    try:
        r = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(cid, secret),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:  # noqa: BLE001
        log.warning("CME token request failed: %s", e)
        return None


def probabilities() -> Optional[dict]:
    """Implied probabilities for the next FOMC meeting, or None if not entitled."""
    tok = _token()
    if not tok:
        return None
    try:
        r = requests.get(DATA_URL, headers={"Authorization": f"Bearer {tok}"},
                         timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("CME FedWatch fetch failed: %s", e)
        return None
