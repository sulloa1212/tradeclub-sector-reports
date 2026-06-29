"""Earnings reconciliation — the two-source verification layer.

Why this exists
---------------
The earnings list used to be single-source (Finnhub). A single source can be
wrong about the one thing that matters most for a trader: WHEN a name reports and
WHETHER it has already reported. If the source says "reported" with a stale/garbled
row, the report would assert a date and numbers that are simply false.

This module takes the Finnhub-enriched rows (primary) and the FMP earnings
calendar (secondary, independent provider) and reconciles them per name:

  * Both agree on date AND reported-status  -> verified=True,  confidence "high"
  * They agree on date+status but EPS actuals differ materially
                                            -> verified=True,  confidence "medium"
  * Report DATES disagree                   -> verified=False, confidence "low"
  * Reported-STATUS disagrees               -> verified=False, confidence "low"
  * Only one source has the name            -> verified=False, confidence "single-source"

`display_status` collapses to "reported" / "upcoming" only when verified; otherwise
"unconfirmed". The prompt is instructed to assert actuals/dates ONLY for verified
rows and to label everything else "unconfirmed — verify", withholding specific
figures. That converts a confident-but-wrong number into an honest blank — the
exact failure (Nike listed as "reported this week" with bogus EPS) this prevents.

No network here: callers pass already-fetched lists, so the logic is pure and unit
-testable. Run `python -m src.sources.earnings` for the offline self-test.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

# Tolerance for calling two EPS-actual prints "the same" across providers:
# the larger of an absolute floor and a relative band (handles tiny/penny EPS).
_EPS_ABS_TOL = 0.02
_EPS_REL_TOL = 0.05


def _parse_date(s: Any) -> Optional[dt.date]:
    if not s:
        return None
    txt = str(s)[:10]
    try:
        return dt.date.fromisoformat(txt)
    except ValueError:
        return None


def _eps_matches(a: Any, b: Any) -> bool:
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return True  # can't compare -> don't manufacture a conflict
    tol = max(_EPS_ABS_TOL, _EPS_REL_TOL * max(abs(a), abs(b)))
    return abs(a - b) <= tol


def _index_secondary(fmp_rows: Optional[list]) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for r in fmp_rows or []:
        sym = (r.get("symbol") or "").upper()
        if sym:
            idx.setdefault(sym, []).append(r)
    return idx


def _pick_secondary(p_date: Optional[dt.date], candidates: list[dict]) -> Optional[dict]:
    """Choose the FMP row for this name: exact date match if present, else nearest."""
    if not candidates:
        return None
    if p_date is not None:
        exact = [c for c in candidates if _parse_date(c.get("date")) == p_date]
        if exact:
            return exact[0]
        return min(
            candidates,
            key=lambda c: abs(((_parse_date(c.get("date")) or dt.date.min) - p_date).days),
        )
    return candidates[0]


def reconcile(primary_rows: Optional[list], fmp_rows: Optional[list]) -> list:
    """Annotate each primary (Finnhub-enriched) row with verification fields.

    Adds: verified (bool), confidence (high|medium|low|single-source),
    verify_sources (list[str]), verify_note (str|None), display_status
    (reported|upcoming|unconfirmed).
    """
    out: list[dict] = []
    sec_idx = _index_secondary(fmp_rows)
    have_secondary = bool(fmp_rows)

    for p in primary_rows or []:
        if not isinstance(p, dict):
            continue
        sym = (p.get("symbol") or "").upper()
        p_date = _parse_date(p.get("date"))
        p_reported = bool(p.get("reported"))

        verified = False
        confidence = "single-source"
        note: Optional[str] = None
        sources = ["Finnhub"]

        sec = _pick_secondary(p_date, sec_idx.get(sym, []))

        if not have_secondary:
            note = "second source (FMP) not connected — confirm before asserting"
        elif sec is None:
            note = "only Finnhub lists this name — unconfirmed, verify on platform"
        else:
            sources.append("FMP")
            s_date = _parse_date(sec.get("date"))
            s_reported = bool(sec.get("reported"))
            date_match = (p_date is not None and p_date == s_date)
            reported_match = (p_reported == s_reported)

            if date_match and reported_match:
                verified = True
                confidence = "high"
                if p_reported and not _eps_matches(p.get("epsActual"), sec.get("eps_actual")):
                    confidence = "medium"
                    note = (f"EPS actual differs across sources "
                            f"(Finnhub {p.get('epsActual')} / FMP {sec.get('eps_actual')}) "
                            f"— treat the surprise as approximate")
            elif not date_match:
                confidence = "low"
                note = (f"report-date conflict — Finnhub {p.get('date')} vs "
                        f"FMP {sec.get('date')}; do NOT state a date, verify on platform")
            else:  # dates agree, reported-status disagrees
                confidence = "low"
                note = ("reported-status conflict — one source says reported, the other "
                        "upcoming; withhold actuals, verify on platform")

        if verified:
            display = "reported" if p_reported else "upcoming"
        else:
            display = "unconfirmed"

        out.append({
            **p,
            "verified": verified,
            "confidence": confidence,
            "verify_sources": sources,
            "verify_note": note,
            "display_status": display,
        })
    return out


def summarize(rows: list) -> dict:
    """Small headline block for the packet + logs."""
    total = len(rows)
    verified = sum(1 for r in rows if r.get("verified"))
    conflicts = [r for r in rows if r.get("confidence") == "low"]
    return {
        "total": total,
        "verified": verified,
        "unverified": total - verified,
        "conflicts": [
            {"symbol": r.get("symbol"), "note": r.get("verify_note")} for r in conflicts
        ] or None,
    }


def build(primary_rows: Optional[list], fmp_rows: Optional[list]) -> dict:
    """Reconcile + package. Returns {calendar, verification, second_source}."""
    reconciled = reconcile(primary_rows, fmp_rows)
    # Keep the established sort (today first, then size) but float verified up
    # within equal buckets so the prompt leads with confirmed names.
    reconciled.sort(key=lambda r: (not r.get("is_today"), not r.get("verified")))
    return {
        "calendar": reconciled or None,
        "verification": summarize(reconciled),
        "second_source": "FMP" if fmp_rows else None,
    }


# --------------------------------------------------------------------------- #
# Offline self-test — reproduces the real Nike misread and proves it's caught.
# Run: python -m src.sources.earnings
# --------------------------------------------------------------------------- #
def _selftest() -> int:
    today = dt.date.today().isoformat()

    # PRIMARY (Finnhub-enriched, as macro._enrich_earnings would emit).
    finnhub = [
        # Agreement, reported -> should verify HIGH
        {"symbol": "MU", "date": today, "reported": True,
         "epsActual": 3.05, "epsEstimate": 2.80, "is_today": True},
        # Agreement, upcoming -> should verify HIGH
        {"symbol": "STZ", "date": "2026-06-30", "reported": False,
         "epsEstimate": 3.28, "is_today": False},
        # THE NIKE BUG: Finnhub wrongly says reported THIS week with bad numbers;
        # FMP says it's upcoming on 6/30. Must be caught -> NOT verified.
        {"symbol": "NKE", "date": today, "reported": True,
         "epsActual": 0.02, "epsEstimate": 0.12, "is_today": True},
        # Date conflict only -> NOT verified.
        {"symbol": "FDX", "date": "2026-06-23", "reported": True,
         "epsActual": 5.10, "epsEstimate": 5.00, "is_today": False},
        # Single-source (FMP has never heard of it) -> NOT verified.
        {"symbol": "ZZZZ", "date": today, "reported": True,
         "epsActual": 1.00, "epsEstimate": 0.90, "is_today": True},
        # EPS actual disagreement -> verified but MEDIUM.
        {"symbol": "ORCL", "date": today, "reported": True,
         "epsActual": 1.50, "epsEstimate": 1.40, "is_today": True},
    ]
    # SECONDARY (FMP earnings-calendar normalized).
    fmp_rows = [
        {"symbol": "MU", "date": today, "eps_actual": 3.05, "reported": True},
        {"symbol": "STZ", "date": "2026-06-30", "eps_actual": None, "reported": False},
        {"symbol": "NKE", "date": "2026-06-30", "eps_actual": None, "reported": False},
        {"symbol": "FDX", "date": "2026-06-25", "eps_actual": 5.10, "reported": True},
        {"symbol": "ORCL", "date": today, "eps_actual": 1.72, "reported": True},
    ]

    rows = {r["symbol"]: r for r in reconcile(finnhub, fmp_rows)}
    checks = [
        ("MU verified high",       rows["MU"]["verified"] is True and rows["MU"]["confidence"] == "high"),
        ("MU display reported",    rows["MU"]["display_status"] == "reported"),
        ("STZ verified upcoming",  rows["STZ"]["verified"] is True and rows["STZ"]["display_status"] == "upcoming"),
        ("NKE NOT verified",       rows["NKE"]["verified"] is False),
        ("NKE flagged unconfirmed",rows["NKE"]["display_status"] == "unconfirmed"),
        ("NKE note = date conflict","report-date conflict" in (rows["NKE"]["verify_note"] or "")),
        ("FDX date conflict caught",rows["FDX"]["verified"] is False and rows["FDX"]["confidence"] == "low"),
        ("ZZZZ single-source",     rows["ZZZZ"]["verified"] is False and rows["ZZZZ"]["confidence"] == "single-source"),
        ("ORCL verified medium",   rows["ORCL"]["verified"] is True and rows["ORCL"]["confidence"] == "medium"),
    ]

    # Degraded mode: no FMP at all -> everything single-source, nothing verified.
    nofmp = {r["symbol"]: r for r in reconcile(finnhub, None)}
    checks.append(("No-FMP -> none verified",
                   all(not r["verified"] and r["confidence"] == "single-source"
                       for r in nofmp.values())))

    ok = True
    print("Earnings reconciliation self-test")
    print("-" * 52)
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    print("-" * 52)
    print("Conflicts surfaced:", summarize(reconcile(finnhub, fmp_rows))["conflicts"])
    print("RESULT:", "ALL PASS ✅" if ok else "FAILURES ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
