# Pre-Market Data Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 8:40 AM MWTC Pre-Market report use real pre-market data and earnings figures that can't be misread, and keep each run's earnings and movers data so a wrong number can be traced.

**Architecture:**
- A new `mwtc/sources/premarket.py` reads 5-minute bars, including extended hours, from yfinance. It builds two things: Pre-Market Movers over the S&P 500 ∪ Nasdaq-100, and per-ticker pre-market reactions.
- `mwtc/sources/sessions.py` gives the previous NYSE trading day. `macro._enrich_earnings` and `fmp.earnings_calendar` use it for the "since last close" window.
- EPS and revenue beat/miss are computed in code and rendered as one `result_line` per reported row. The AI quotes that line.
- `generator._coverage_gaps` gains a direction check that drives the existing one-retry mechanism.
- `publish.py` saves a trace file (earnings and movers only) to `mwtc_out/` and gains `--preview`. `mwtc.yml` uploads `mwtc_out/` as an artifact and gets a `preview` input.

**Tech Stack:** Python 3.11 in CI (3.9 locally), yfinance (1.7.0 in CI, 1.2.0 locally), pandas (3.0.6 in CI, 2.3.3 locally), pandas_market_calendars (5.4.0 in CI from the root `requirements.txt`, 4.4.1 locally), stdlib `unittest`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-24-premarket-data-fixes-design.md`

## Global Constraints

- **Where commands run.** Run every command from the worktree root. Use the main checkout's interpreter, because the worktree has no venv of its own: `PY=/Users/admin/Desktop/Trae-Projects/TradeClub-AIReports-Sectors/.venv/bin/python`.
- **Pre-market price source.** yfinance 5-minute bars with `prepost=True`.
  - Yahoo returns the index in **UTC**; convert it to ET before any time-of-day logic.
  - Yahoo reports **Volume 0 on every pre-market bar** (measured 2026-10-26 on META, AAPL, TSLA, CLF, BRK-B and 503 S&P names).
  - A bar is labeled by its **start** time, so the price "as of 8:40" is the bar that starts at 8:35.
  - The **prior close is the official close** from daily bars. The last regular 5-minute bar can miss the closing auction: META on 10/21 closed officially at $697.52, while the 15:55 bar ended at $698.43.
  - Measured replay: META at 8:40 AM on 2026-10-22 = −2.38%.
- **Movers universe.** S&P 500 ∪ Nasdaq-100, about 520 unique symbols (the two lists overlap heavily). Source: FMP `/sp500-constituent` + `/nasdaq-constituent`. The committed `mwtc/data/universe.txt` is added when either list is missing.
- **Mover thresholds.** Trades in at least **6 separate 5-minute windows** since 4:00 AM ET, and a trade within the last 30 minutes. Top 10 up and top 10 down.
  - This replaces the spec's "$1M traded pre-market", which Yahoo data can't support. **Owner approved 2026-10-26.**
  - Measured at 8:40 AM on 503 S&P names, it keeps the real movers: SNA −9.8%, LRCX −8.8%, IBM −6.9% (10/22); CCI +11.9%, DECK −8.5% (10/23); FICO −9.6%, EQT −8.3% (10/26).
  - It drops 2–3-print names such as EFX −3.5% (3 windows).
- **Overnight window.**
  - Included: the previous NYSE trading day with hour `amc`, plus today with hour `bmo` or no hour. Only rows already reported count.
  - A previous-day row with no hour is "timing unconfirmed — verify".
- **EPS label.** "EPS (provider figure, may be adjusted)".
- **Direction rule.** No "will gap up/down" for a name without pre-market data. Enforced by the existing one-retry check, which costs about $1.40 and only on a run that breaks the rule.
- **Run data.**
  - The repo `sulloa1212/tradeclub-sector-reports` is **public**, so its Actions artifacts can be downloaded by any signed-in GitHub user.
  - The saved file therefore holds only the earnings and movers slice. No Unusual Whales, CME FedWatch, analyst or insider data.
  - Artifact `mwtc-<mode>-packet`, kept 14 days. This narrows the spec's "full packet". **Owner approved 2026-10-26.**
- **Failure must never kill a run.** Every new data step degrades to `None`/`{}` and a stated "unavailable".
- **Compatibility.** Code must run on Python 3.9 and 3.11, and on pandas 2.3 and 3.0. Keep `from __future__ import annotations` in every new module.
- **Commits.** Messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A late run (forced re-run at 10:15 AM).** Pre-market should be read as of 9:30 AM, not shown empty. Test in Task 3 (`test_run_after_open_uses_930`).
2. **Nothing passes the filters** (weekend probe, a run before 4 AM, a holiday). The report must say why instead of showing an empty or invented list. Test in Task 3 (`test_no_qualifiers_says_why`).
3. **Yahoo returns only part of the universe** (rate limit, missing symbols). Movers are built from what came back, `priced` is reported, and nothing crashes. Test in Task 3 (`test_partial_download`).
4. **Dotted tickers (BRK.B, BF.B)** must reach Yahoo as BRK-B and come back under BRK.B. Test in Task 3 (`test_download_maps_dotted_symbols`).
5. **Monday after a holiday weekend, and the day after Thanksgiving.** The prior close and the overnight window must come from the right session. Tests in Task 1 (`test_previous_trading_day_cases`, `test_day_after_thanksgiving`) and Task 3 (`test_prior_close_skips_weekend`).

---

### Task 1: Previous trading day and the overnight window

**Files:**
- Create: `mwtc/sources/sessions.py`
- Create: `mwtc/tests/__init__.py` (empty)
- Create: `mwtc/tests/test_overnight.py`
- Modify: `mwtc/sources/macro.py:192-241` (`_enrich_earnings`, `earnings_calendar`)
- Modify: `mwtc/sources/fmp.py:151-166` (`earnings_calendar` window start)

**Interfaces:**
- Produces:
  - `sessions.previous_trading_day(d: datetime.date) -> datetime.date`
  - `macro._enrich_earnings(r: dict, today_iso: str, prev_iso: str | None = None) -> dict`. It adds `is_overnight_result: bool` and `timing_unconfirmed: bool`.
  - `macro.earnings_calendar(today: datetime.date | None = None) -> list | None`
  - `fmp.earnings_calendar(days: int = 7, today: datetime.date | None = None) -> list | None`

- [ ] **Step 1: Write the failing tests**

`mwtc/tests/__init__.py`: empty file.

`mwtc/tests/test_overnight.py`:

```python
import datetime as dt
import unittest
from unittest import mock

from mwtc.sources import sessions, macro


class PreviousTradingDay(unittest.TestCase):
    def test_previous_trading_day_cases(self):
        cases = {
            "2026-10-20": "2026-10-19",   # Tue -> Mon
            "2026-10-19": "2026-10-16",   # Mon -> Fri
            "2026-11-27": "2026-11-25",   # day after Thanksgiving -> Wed
            "2026-11-30": "2026-11-27",   # Mon after the 11-27 half day -> Fri
            "2026-09-08": "2026-09-04",   # Tue after Labor Day -> Fri
        }
        for d, want in cases.items():
            got = sessions.previous_trading_day(dt.date.fromisoformat(d)).isoformat()
            self.assertEqual(got, want, d)

    def test_weekday_fallback_without_calendar(self):
        with mock.patch.object(sessions, "_nyse_sessions", side_effect=RuntimeError("no calendar")):
            self.assertEqual(sessions.previous_trading_day(dt.date(2026, 10, 19)).isoformat(), "2026-10-16")


def row(sym, date, hour, eps_a=1.0, eps_e=0.9):
    return {"symbol": sym, "date": date, "hour": hour, "epsActual": eps_a, "epsEstimate": eps_e}


class OvernightWindow(unittest.TestCase):
    def enrich(self, r, today):
        prev = sessions.previous_trading_day(dt.date.fromisoformat(today)).isoformat()
        return macro._enrich_earnings(r, today, prev)

    def test_2026_10_20_replay(self):
        # Monday 10-19: CLF and FITB reported before the open; STLD, AERO, WRB
        # and CCK after the close. Only the after-close ones are overnight news
        # on Tuesday 10-20.
        today = "2026-10-20"
        flags = {s: self.enrich(row(s, "2026-10-19", h), today)["is_overnight_result"]
                 for s, h in (("CLF", "bmo"), ("FITB", "bmo"), ("STLD", "amc"),
                              ("AERO", "amc"), ("WRB", "amc"), ("CCK", "amc"))}
        self.assertEqual(flags, {"CLF": False, "FITB": False, "STLD": True,
                                 "AERO": True, "WRB": True, "CCK": True})

    def test_monday_includes_friday_after_close(self):
        r = self.enrich(row("ABC", "2026-10-16", "amc"), "2026-10-19")
        self.assertTrue(r["is_overnight_result"])

    def test_day_after_thanksgiving(self):
        # Thanksgiving 2026-11-26 is a holiday: Wednesday's after-close
        # reporters are Friday's overnight news.
        self.assertTrue(self.enrich(row("XYZ", "2026-11-25", "amc"), "2026-11-27")["is_overnight_result"])

    def test_today_before_open_and_untimed(self):
        self.assertTrue(self.enrich(row("A", "2026-10-20", "bmo"), "2026-10-20")["is_overnight_result"])
        self.assertTrue(self.enrich(row("B", "2026-10-20", ""), "2026-10-20")["is_overnight_result"])

    def test_previous_day_untimed_is_unconfirmed(self):
        r = self.enrich(row("C", "2026-10-19", ""), "2026-10-20")
        self.assertFalse(r["is_overnight_result"])
        self.assertTrue(r["timing_unconfirmed"])

    def test_not_reported_is_never_overnight(self):
        r = self.enrich(row("D", "2026-10-19", "amc", eps_a=None), "2026-10-20")
        self.assertFalse(r["is_overnight_result"])
        self.assertFalse(r["timing_unconfirmed"])

    def test_finnhub_window_starts_at_previous_trading_day(self):
        seen = {}
        def fake(path, params, key):
            seen.update(params)
            return []
        with mock.patch.object(macro, "_finnhub_calendar", side_effect=fake):
            macro.earnings_calendar(today=dt.date(2026, 10, 19))
        self.assertEqual(seen["from"], "2026-10-16")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$PY -m unittest mwtc.tests.test_overnight -v`
Expected: ERROR, `ImportError: cannot import name 'sessions'`.

- [ ] **Step 3: Write `mwtc/sources/sessions.py`**

```python
"""NYSE session arithmetic for the "since last close" window."""
from __future__ import annotations

import datetime as dt
import logging

log = logging.getLogger("sessions")


def _nyse_sessions(start: dt.date, end: dt.date) -> list:
    import pandas_market_calendars as mcal
    sched = mcal.get_calendar("NYSE").schedule(start_date=start.isoformat(),
                                               end_date=end.isoformat())
    return [ts.date() for ts in sched.index]


def previous_trading_day(d: dt.date) -> dt.date:
    """The NYSE session before `d`: weekends and exchange holidays skipped.
    Falls back to plain weekday arithmetic if the calendar is unavailable."""
    try:
        days = _nyse_sessions(d - dt.timedelta(days=14), d - dt.timedelta(days=1))
        if days:
            return days[-1]
    except Exception as e:  # noqa: BLE001
        log.warning("NYSE calendar unavailable (%s) — weekday fallback", e)
    p = d - dt.timedelta(days=1)
    while p.weekday() >= 5:
        p -= dt.timedelta(days=1)
    return p
```

- [ ] **Step 4: Update `macro._enrich_earnings` and `macro.earnings_calendar`**

In `mwtc/sources/macro.py`, add `from . import sessions` below `from .. import config`.

Replace the whole `_enrich_earnings` function (lines 192–217) with:

```python
def _enrich_earnings(r: dict, today_iso: str, prev_iso: Optional[str] = None) -> dict:
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
    # Reported SINCE THE LAST CLOSE: the previous trading day's after-close
    # reports and this morning's before-open (or untimed) ones. A report from
    # before yesterday's open already moved the stock yesterday (CLF/FITB shown
    # as overnight news on 2026-10-20); on a Monday the previous session is
    # Friday, not Sunday (NVDA 2026-08-26 is why the window starts a day back).
    if prev_iso is None:
        prev_iso = sessions.previous_trading_day(dt.date.fromisoformat(today_iso)).isoformat()
    date = r.get("date")
    overnight = reported and ((date == prev_iso and hour == "amc") or
                              (date == today_iso and hour in ("bmo", "")))
    timing_unconfirmed = reported and date == prev_iso and hour not in ("amc", "bmo")
    return {**r, "reported": reported, "eps_surprise_pct": surprise,
            "beat_miss": beat, "when": when, "is_today": date == today_iso,
            "is_overnight_result": overnight, "timing_unconfirmed": timing_unconfirmed}
```

Replace the whole `earnings_calendar` function (lines 220–241) with:

```python
def earnings_calendar(today: Optional[dt.date] = None) -> Optional[list]:
    """Earnings from the previous trading day through the next 7 days (Finnhub
    free tier — confirmed live), enriched with reported/upcoming status +
    beat/miss. Trims micro-cap noise, keeps the largest names by revenue, and
    surfaces the freshest names first."""
    today = today or dt.date.today()
    today_iso = today.isoformat()
    prev_iso = sessions.previous_trading_day(today).isoformat()
    # Window starts at the PREVIOUS TRADING DAY so its after-close reporters
    # are included — they were "upcoming" on that day's calendar and would
    # otherwise vanish (NVDA 2026-08-26; Friday's after-close on a Monday).
    rows = _finnhub_calendar(
        "/calendar/earnings",
        {"from": prev_iso, "to": (today + dt.timedelta(days=7)).isoformat()},
        "earningsCalendar",
    )
    if not isinstance(rows, list):
        return rows
    named = [r for r in rows if r.get("epsEstimate") is not None
             or r.get("revenueEstimate")]
    enriched = [_enrich_earnings(r, today_iso, prev_iso) for r in named]
    # Freshest first: overnight results and today's names, then by revenue size.
    enriched.sort(key=lambda r: (not (r["is_today"] or r.get("is_overnight_result")),
                                 -(r.get("revenueEstimate") or 0)))
    return enriched[:40] or [_enrich_earnings(r, today_iso, prev_iso) for r in rows[:40]]
```

- [ ] **Step 5: Align the FMP cross-check window in `mwtc/sources/fmp.py`**

Add `from . import sessions` below `from .. import config`. In `earnings_calendar`, change the signature, the first line and the `_get` call, and leave the rest of the body unchanged:

```python
def earnings_calendar(days: int = 7, today: Optional[dt.date] = None) -> Optional[list]:
    ...  # docstring unchanged
    today = today or dt.date.today()
    # From the PREVIOUS TRADING DAY, matching the Finnhub window: its after-close
    # reporters must exist in BOTH sources or the reconciler withholds them.
    rows = _get("/earnings-calendar",
                {"from": sessions.previous_trading_day(today).isoformat(),
                 "to": (today + dt.timedelta(days=days)).isoformat()})
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `$PY -m unittest mwtc.tests.test_overnight -v`
Expected: 9 tests, OK.

Run: `$PY -m mwtc.sources.earnings`
Expected: `RESULT: ALL PASS ✅`. The existing self-test still passes.

- [ ] **Step 7: Commit**

```bash
git add mwtc/sources/sessions.py mwtc/sources/macro.py mwtc/sources/fmp.py mwtc/tests/__init__.py mwtc/tests/test_overnight.py
git commit -m "MWTC earnings: 'since last close' means the previous trading day's after-close + today's pre-open

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: EPS and revenue figures computed in code

**Files:**
- Modify: `mwtc/sources/macro.py` (`_enrich_earnings`: revenue surprise)
- Modify: `mwtc/sources/earnings.py` (revenue mismatch note in `reconcile`, `_money`, `result_line`)
- Create: `mwtc/tests/test_earnings_figures.py`

**Interfaces:**
- Consumes: `macro._enrich_earnings(r, today_iso, prev_iso)` from Task 1.
- Produces:
  - `_enrich_earnings` adds `revenue_surprise_pct: float | None` and `revenue_beat_miss: "beat"|"miss"|"inline"|None`.
  - `earnings.result_line(r: dict, include_reaction: bool = True) -> str | None`. It reads `premarket_reaction_pct` (float) and `premarket_as_of_et` (str like `"8:35 AM ET"`) when present.

- [ ] **Step 1: Write the failing tests**

`mwtc/tests/test_earnings_figures.py`:

```python
import unittest

from mwtc.sources import macro, earnings


META = {"symbol": "META", "date": "2026-10-21", "hour": "amc",
        "epsActual": 7.40, "epsEstimate": 5.79,
        "revenueActual": 64_601_000_000, "revenueEstimate": 63_100_000_000}


class Figures(unittest.TestCase):
    def enrich(self, r):
        return macro._enrich_earnings(r, "2026-10-22", "2026-10-21")

    def test_revenue_surprise_sign_comes_from_the_numbers(self):
        r = self.enrich(dict(META))
        self.assertEqual(r["revenue_beat_miss"], "beat")
        self.assertAlmostEqual(r["revenue_surprise_pct"], 2.4, places=1)
        swapped = dict(META, revenueActual=63_100_000_000, revenueEstimate=64_601_000_000)
        self.assertEqual(self.enrich(swapped)["revenue_beat_miss"], "miss")

    def test_result_line_meta(self):
        r = self.enrich(dict(META))
        r.update(premarket_reaction_pct=-2.51, premarket_as_of_et="8:35 AM ET")
        self.assertEqual(
            earnings.result_line(r),
            "META — EPS (provider figure, may be adjusted) $7.40 vs est. $5.79: BEAT +27.8%"
            " · revenue $64.6B vs est. $63.1B: BEAT +2.4%"
            " · pre-market -2.5% at 8:35 AM ET")

    def test_result_line_without_reaction_says_so(self):
        r = self.enrich(dict(META))
        self.assertTrue(earnings.result_line(r).endswith(
            "pre-market reaction not available — verify live"))
        self.assertNotIn("pre-market", earnings.result_line(r, include_reaction=False))

    def test_result_line_only_for_reported(self):
        r = self.enrich(dict(META, epsActual=None, revenueActual=None))
        self.assertIsNone(earnings.result_line(r))

    def test_revenue_mismatch_across_sources_is_flagged(self):
        p = macro._enrich_earnings(dict(META, date="2026-10-22", hour="bmo"), "2026-10-22", "2026-10-21")
        fmp = [{"symbol": "META", "date": "2026-10-22", "eps_actual": 7.40,
                "revenue_actual": 63_100_000_000, "reported": True}]
        out = earnings.reconcile([p], fmp)[0]
        self.assertEqual(out["confidence"], "medium")
        self.assertIn("revenue actual differs", out["verify_note"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify the tests fail**

Run: `$PY -m unittest mwtc.tests.test_earnings_figures -v`
Expected: FAIL / ERROR, with `KeyError: 'revenue_beat_miss'` and `AttributeError: module 'mwtc.sources.earnings' has no attribute 'result_line'`.

- [ ] **Step 3: Add the revenue surprise to `macro._enrich_earnings`**

Insert this right after the EPS `try/except` block:

```python
    rev_est, rev_act = r.get("revenueEstimate"), r.get("revenueActual")
    rev_surprise = rev_beat = None
    if rev_act is not None and rev_est not in (None, 0):
        try:
            rev_surprise = round((rev_act - rev_est) / abs(rev_est) * 100, 1)
            rev_beat = ("beat" if rev_act > rev_est else
                        "miss" if rev_act < rev_est else "inline")
        except (TypeError, ZeroDivisionError):
            pass
```

Add two entries to the returned dict: `"revenue_surprise_pct": rev_surprise, "revenue_beat_miss": rev_beat,`.

- [ ] **Step 4: Add the revenue cross-check and `result_line` to `mwtc/sources/earnings.py`**

Below `_eps_matches`:

```python
_REV_REL_TOL = 0.01   # revenue actuals more than 1% apart across providers


def _rev_matches(a: Any, b: Any) -> bool:
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return True  # can't compare -> don't manufacture a conflict
    return abs(a - b) <= _REV_REL_TOL * max(abs(a), abs(b))


def _money(v: Any) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "n/a"
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= div:
            return f"${v / div:.1f}{unit}"
    return f"${v:,.0f}"


def result_line(r: dict, include_reaction: bool = True) -> Optional[str]:
    """The one line the report quotes for a reported result: figures and
    beat/miss computed here, never restated by the model from raw fields
    (META 2026-10-22: revenue actual and estimate came out swapped)."""
    if not r.get("reported"):
        return None
    parts = []
    eps_a, eps_e = r.get("epsActual"), r.get("epsEstimate")
    if eps_a is not None:
        s = f"EPS (provider figure, may be adjusted) ${float(eps_a):.2f}"
        if eps_e is not None:
            s += f" vs est. ${float(eps_e):.2f}"
        if r.get("beat_miss") and r.get("eps_surprise_pct") is not None:
            s += f": {r['beat_miss'].upper()} {r['eps_surprise_pct']:+.1f}%"
        parts.append(s)
    rev_a, rev_e = r.get("revenueActual"), r.get("revenueEstimate")
    if rev_a is not None:
        s = f"revenue {_money(rev_a)}"
        if rev_e is not None:
            s += f" vs est. {_money(rev_e)}"
        if r.get("revenue_beat_miss") and r.get("revenue_surprise_pct") is not None:
            s += f": {r['revenue_beat_miss'].upper()} {r['revenue_surprise_pct']:+.1f}%"
        parts.append(s)
    if include_reaction:
        pct = r.get("premarket_reaction_pct")
        parts.append(f"pre-market {pct:+.1f}% at {r.get('premarket_as_of_et')}"
                     if pct is not None else "pre-market reaction not available — verify live")
    return f"{str(r.get('symbol') or '').upper()} — " + " · ".join(parts)
```

In `reconcile`, replace the `if date_match and reported_match:` block (lines 119–126) with:

```python
            if date_match and reported_match:
                verified = True
                confidence = "high"
                notes = []
                if p_reported and not _eps_matches(p.get("epsActual"), sec.get("eps_actual")):
                    confidence = "medium"
                    notes.append(f"EPS actual differs across sources "
                                 f"(Finnhub {p.get('epsActual')} / FMP {sec.get('eps_actual')}) "
                                 f"— treat the surprise as approximate")
                if p_reported and not _rev_matches(p.get("revenueActual"), sec.get("revenue_actual")):
                    confidence = "medium"
                    notes.append(f"revenue actual differs across sources "
                                 f"(Finnhub {_money(p.get('revenueActual'))} / FMP "
                                 f"{_money(sec.get('revenue_actual'))}) — treat the revenue "
                                 f"surprise as approximate")
                note = "; ".join(notes) or None
```

- [ ] **Step 5: Run the tests**

Run: `$PY -m unittest mwtc.tests.test_earnings_figures mwtc.tests.test_overnight -v && $PY -m mwtc.sources.earnings`
Expected: all OK, and `RESULT: ALL PASS ✅`.

- [ ] **Step 6: Commit**

```bash
git add mwtc/sources/macro.py mwtc/sources/earnings.py mwtc/tests/test_earnings_figures.py
git commit -m "MWTC earnings: EPS and revenue beat/miss computed in code, one result line per report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Pre-market prices (`premarket.py`)

**Files:**
- Create: `mwtc/sources/premarket.py`
- Create: `mwtc/tests/test_premarket.py`

**Interfaces:**
- Produces:
  - `premarket.yahoo_symbol(sym: str) -> str`
  - `premarket.summarize(bars: pandas.DataFrame, now_et: datetime, official: dict | None = None) -> dict | None`.
    - `official` is `{date: official close}`.
    - The dict has keys `prev_close, last, pct_change, last_print_et, age_min, trade_windows`.
  - `premarket.snapshot(symbols: list, now_et: datetime | None = None, download=None, closes=None) -> dict` (`{symbol: summary}`)
  - `premarket.movers(universe: list[dict], now_et=None, n=10, download=None, closes=None) -> dict | None`.
    - The dict has keys `gainers, losers, as_of_et, universe_size, priced, qualified, unavailable, source`.
    - Each mover row has keys `ticker, name, pct_change, price, prev_close, last_print_et, trade_windows`.
  - `premarket.reactions(symbols: list, now_et=None, download=None, closes=None) -> dict`. Each value is `{"pct": float, "as_of_et": str}`.
  - The two injectable callables:
    - `download` takes `list[str]` and returns `{symbol: DataFrame with a "Close" column and a tz-aware index}` (default `_download`, 5-minute bars).
    - `closes` takes `list[str]` and returns `{symbol: {date: official close}}` (default `_daily_closes`, daily bars).

- [ ] **Step 1: Write the failing tests**

`mwtc/tests/test_premarket.py`:

```python
import datetime as dt
import sys
import types
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd

from mwtc.sources import premarket as pm

ET = ZoneInfo("America/New_York")
MON, TUE = dt.date(2026, 10, 19), dt.date(2026, 10, 20)
AT_840 = dt.datetime(2026, 10, 20, 8, 40, tzinfo=ET)


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return dt.datetime.combine(day, dt.time(h, m), ET)


def bars(prev_day, prev_close, today, prints):
    """5-minute bars the way Yahoo returns them: UTC index, Volume 0 in the
    pre-market. The end of a regular session on prev_day at prev_close, then
    pre-market `prints` = [(HH:MM, close)] on `today`."""
    idx = [at(prev_day, "15:50"), at(prev_day, "15:55")] + [at(today, t) for t, _ in prints]
    close = [prev_close, prev_close] + [c for _, c in prints]
    return pd.DataFrame({"Close": close, "Volume": [0] * len(close)},
                        index=pd.DatetimeIndex(idx).tz_convert("UTC"))


def run_of(end, n, price):
    """n consecutive 5-minute bars ending at `end` (HH:MM), all at `price`."""
    t = dt.datetime.strptime(end, "%H:%M")
    return [((t - dt.timedelta(minutes=5 * i)).strftime("%H:%M"), price) for i in reversed(range(n))]


class Summarize(unittest.TestCase):
    def test_basic(self):
        s = pm.summarize(bars(MON, 100.0, TUE, [("08:00", 102.0), ("08:30", 103.0)]), AT_840)
        self.assertEqual(s["prev_close"], 100.0)
        self.assertEqual(s["last"], 103.0)
        self.assertAlmostEqual(s["pct_change"], 3.0)
        self.assertEqual(s["last_print_et"], "8:35 AM ET")
        self.assertAlmostEqual(s["age_min"], 5.0)
        self.assertEqual(s["trade_windows"], 2)

    def test_no_premarket_prints(self):
        s = pm.summarize(bars(MON, 100.0, TUE, []), AT_840)
        self.assertIsNone(s["pct_change"])
        self.assertEqual(s["trade_windows"], 0)

    def test_run_after_open_uses_930(self):
        b = bars(MON, 100.0, TUE, [("09:25", 99.0), ("09:35", 95.0)])
        s = pm.summarize(b, dt.datetime(2026, 10, 20, 10, 15, tzinfo=ET))
        self.assertEqual(s["last"], 99.0)           # the 9:35 regular bar is ignored
        self.assertEqual(s["last_print_et"], "9:30 AM ET")
        self.assertAlmostEqual(s["age_min"], 0.0)   # measured against 9:30

    def test_prior_close_skips_weekend(self):
        fri = dt.date(2026, 10, 16)
        s = pm.summarize(bars(fri, 50.0, MON, [("08:30", 49.0)]),
                         dt.datetime(2026, 10, 19, 8, 40, tzinfo=ET))
        self.assertEqual(s["prev_close"], 50.0)
        self.assertAlmostEqual(s["pct_change"], -2.0)

    def test_official_close_preferred(self):
        # META 2026-10-21: official close $697.52, last regular bar $698.43.
        # Today's daily row (Yahoo can create it early) must be ignored.
        s = pm.summarize(bars(MON, 100.0, TUE, [("08:30", 99.0)]), AT_840, {MON: 98.0, TUE: 50.0})
        self.assertEqual(s["prev_close"], 98.0)
        self.assertAlmostEqual(s["pct_change"], (99 / 98 - 1) * 100)


def stub(table):
    return lambda symbols: {s: table[s] for s in symbols if s in table}


def no_closes(symbols):
    return {}


class MoversAndReactions(unittest.TestCase):
    def setUp(self):
        self.table = {
            "UP": bars(MON, 100.0, TUE, run_of("08:30", 8, 110.0)),       # +10%, 8 windows
            "DN": bars(MON, 100.0, TUE, run_of("08:30", 8, 92.0)),        # -8%
            "THIN": bars(MON, 100.0, TUE, [("08:20", 130.0), ("08:30", 130.0)]),  # +30%, 2 windows
            "STALE": bars(MON, 100.0, TUE, run_of("07:30", 8, 120.0)),    # last print 65 min old
            "FLAT": bars(MON, 100.0, TUE, []),
        }
        self.uni = [{"symbol": s, "name": s.title()} for s in self.table] + [{"symbol": "UP", "name": "dupe"}]

    def test_movers_filters_and_ranks(self):
        m = pm.movers(self.uni, AT_840, download=stub(self.table), closes=no_closes)
        self.assertEqual([r["ticker"] for r in m["gainers"]], ["UP"])
        self.assertEqual([r["ticker"] for r in m["losers"]], ["DN"])
        self.assertEqual(m["gainers"][0]["name"], "Up")
        self.assertEqual(m["universe_size"], 5)
        self.assertEqual(m["as_of_et"], "8:40 AM ET")
        self.assertIsNone(m["unavailable"])

    def test_no_qualifiers_says_why(self):
        m = pm.movers(self.uni, AT_840, download=stub({k: self.table[k] for k in ("THIN", "FLAT")}),
                      closes=no_closes)
        self.assertEqual(m["gainers"], [])
        self.assertIn("8:40 AM ET", m["unavailable"])

    def test_partial_download(self):
        m = pm.movers(self.uni, AT_840, download=stub({"UP": self.table["UP"]}), closes=no_closes)
        self.assertEqual(m["priced"], 1)
        self.assertEqual([r["ticker"] for r in m["gainers"]], ["UP"])

    def test_download_failure_returns_none(self):
        def boom(symbols):
            raise RuntimeError("rate limited")
        self.assertIsNone(pm.movers(self.uni, AT_840, download=boom, closes=no_closes))

    def test_closes_failure_falls_back_to_bars(self):
        def boom(symbols):
            raise RuntimeError("daily bars down")
        r = pm.reactions(["UP"], AT_840, download=stub(self.table), closes=boom)
        self.assertAlmostEqual(r["UP"]["pct"], 10.0)

    def test_reactions(self):
        r = pm.reactions(["UP", "FLAT", "MISSING"], AT_840, download=stub(self.table), closes=no_closes)
        self.assertEqual(set(r), {"UP"})
        self.assertAlmostEqual(r["UP"]["pct"], 10.0)
        self.assertEqual(r["UP"]["as_of_et"], "8:35 AM ET")

    def test_download_maps_dotted_symbols(self):
        cols = pd.MultiIndex.from_tuples([("BRK-B", "Close"), ("BRK-B", "Volume")])
        frame = pd.DataFrame([[500.0, 0]], columns=cols,
                             index=pd.DatetimeIndex([pd.Timestamp("2026-10-20 12:30", tz="UTC")]))
        seen = []
        def fake_download(tickers, **kw):
            seen.append((tickers, kw))
            return frame
        with mock.patch.dict(sys.modules, {"yfinance": types.SimpleNamespace(download=fake_download)}):
            bars_out = pm._download(["BRK.B"])
            closes_out = pm._daily_closes(["BRK.B"])
        self.assertEqual(pm.yahoo_symbol("BRK.B"), "BRK-B")
        self.assertEqual(seen[0][0], ["BRK-B"])
        self.assertTrue(seen[0][1]["prepost"])
        self.assertEqual(seen[1][1]["interval"], "1d")
        self.assertEqual(list(bars_out), ["BRK.B"])
        self.assertEqual(closes_out, {"BRK.B": {dt.date(2026, 10, 20): 500.0}})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify the tests fail**

Run: `$PY -m unittest mwtc.tests.test_premarket -v`
Expected: ERROR, `ImportError: cannot import name 'premarket'`.

- [ ] **Step 3: Write `mwtc/sources/premarket.py`**

```python
"""Real pre-market prices from yfinance (5-minute bars incl. extended hours).

FMP's biggest-gainers/losers rank the LAST REGULAR SESSION, so at 8:40 they are
yesterday's movers (CLF/IRON/SLN shown as "pre-market" on 2026-10-20). This
module reads this morning's actual pre-market prints instead.

Yahoo facts (measured 2026-10-26): the index comes back in UTC, and every
pre-market bar has Volume 0 — so liquidity is measured by how many separate
5-minute windows traded, not by dollar volume. A bar exists only if at least
one trade printed in it. Everything degrades to None/{} on failure.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Callable, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("premarket")

ET = ZoneInfo("America/New_York")
PRE_OPEN, OPEN, CLOSE = dt.time(4, 0), dt.time(9, 30), dt.time(16, 0)
MIN_TRADE_WINDOWS = 6   # 5-minute windows with a trade since 4:00 AM ET
MAX_AGE_MIN = 30        # the last trade must be this fresh
BAR = dt.timedelta(minutes=5)


def yahoo_symbol(sym: str) -> str:
    return str(sym).strip().upper().replace(".", "-")


def _fmt(t: dt.datetime) -> str:
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'} ET"


def _download(symbols: list, period: str = "5d") -> dict:
    """{symbol: DataFrame[Close]} of 5-minute bars incl. pre/post market, keyed
    by the caller's spelling (BRK.B), fetched under Yahoo's (BRK-B)."""
    import pandas as pd
    import yfinance as yf
    ys = [yahoo_symbol(s) for s in symbols]
    df = yf.download(ys, period=period, interval="5m", prepost=True, group_by="ticker",
                     auto_adjust=False, progress=False, threads=True)
    out = {}
    for s, y in zip(symbols, ys):
        try:
            sub = df[y] if isinstance(df.columns, pd.MultiIndex) else df
        except KeyError:
            continue
        sub = sub[["Close"]].dropna()
        if not sub.empty:
            out[s] = sub
    return out


def _daily_closes(symbols: list, period: str = "5d") -> dict:
    """{symbol: {date: official close}} from daily bars. The closing auction
    can differ from the last regular 5-minute bar by a few tenths of a percent
    (META 2026-10-21: $697.52 official vs $698.43 at 15:55)."""
    import pandas as pd
    import yfinance as yf
    ys = [yahoo_symbol(s) for s in symbols]
    df = yf.download(ys, period=period, interval="1d", group_by="ticker",
                     auto_adjust=False, progress=False, threads=True)
    out = {}
    for s, y in zip(symbols, ys):
        try:
            col = (df[y] if isinstance(df.columns, pd.MultiIndex) else df)["Close"].dropna()
        except KeyError:
            continue
        out[s] = {ts.date(): float(v) for ts, v in col.items()}
    return out


def summarize(bars, now_et: dt.datetime, official: Optional[dict] = None) -> Optional[dict]:
    """Prior close and this morning's last pre-market print, measured at
    min(now, 9:30) so a late re-run still reads the pre-market. The prior close
    is the official one (`official` = {date: close}) when known, else the last
    regular 5-minute bar. None when neither exists."""
    if bars is None or len(bars) == 0:
        return None
    b = bars.copy()
    b.index = b.index.tz_convert(ET) if b.index.tz is not None else b.index.tz_localize(ET)
    today = now_et.date()
    ref = min(now_et, dt.datetime.combine(today, OPEN, ET))
    times, dates = b.index.time, b.index.date
    regular = b[(dates < today) & (times >= OPEN) & (times < CLOSE)]
    prior = [d for d in (official or {}) if d < today]   # Yahoo may add today's row early
    if prior:
        prev_close = float(official[max(prior)])
    elif not regular.empty:
        prev_close = float(regular["Close"].iloc[-1])
    else:
        return None
    pre = b[(dates == today) & (times >= PRE_OPEN) & (times < OPEN) & (b.index < ref)]
    base = {"prev_close": round(prev_close, 2), "last": None, "pct_change": None,
            "last_print_et": None, "age_min": None, "trade_windows": len(pre)}
    if pre.empty or prev_close <= 0:
        return base
    last = float(pre["Close"].iloc[-1])
    printed = min(pre.index[-1].to_pydatetime() + BAR, ref)   # end of the last traded bar
    base.update(last=round(last, 2), pct_change=(last / prev_close - 1) * 100,
                last_print_et=_fmt(printed),
                age_min=(ref - printed).total_seconds() / 60)
    return base


def snapshot(symbols: list, now_et: Optional[dt.datetime] = None,
             download: Optional[Callable] = None, closes: Optional[Callable] = None) -> dict:
    now_et = now_et or dt.datetime.now(ET)
    download = download or _download
    closes = closes or _daily_closes
    try:
        frames = download(list(symbols))
    except Exception as e:  # noqa: BLE001
        log.warning("pre-market download failed: %s", e)
        return {}
    try:
        official = closes(list(frames)) if frames else {}
    except Exception as e:  # noqa: BLE001
        log.warning("official closes unavailable (%s) — using the last regular 5-minute bar", e)
        official = {}
    out = {}
    for s, f in frames.items():
        try:
            v = summarize(f, now_et, official.get(s))
        except Exception as e:  # noqa: BLE001
            log.warning("pre-market summary failed for %s: %s", s, e)
            continue
        if v is not None:
            out[s] = v
    return out


def movers(universe: list, now_et: Optional[dt.datetime] = None, n: int = 10,
           download: Optional[Callable] = None,
           closes: Optional[Callable] = None) -> Optional[dict]:
    """Top pre-market gainers/losers in the universe. None when no prices came
    back at all; `unavailable` explains an empty list."""
    now_et = now_et or dt.datetime.now(ET)
    names, syms = {}, []
    for u in universe or []:
        s = str((u or {}).get("symbol") or "").strip().upper()
        if s and s not in names:
            names[s] = u.get("name")
            syms.append(s)
    snap = snapshot(syms, now_et, download, closes)
    if not snap:
        return None
    rows = []
    for s, v in snap.items():
        if (v["pct_change"] is None or v["trade_windows"] < MIN_TRADE_WINDOWS
                or v["age_min"] > MAX_AGE_MIN):
            continue
        rows.append({"ticker": s, "name": names.get(s), "pct_change": round(v["pct_change"], 2),
                     "price": v["last"], "prev_close": v["prev_close"],
                     "last_print_et": v["last_print_et"], "trade_windows": v["trade_windows"]})
    as_of = _fmt(min(now_et, dt.datetime.combine(now_et.date(), OPEN, ET)))
    gainers = sorted((r for r in rows if r["pct_change"] > 0), key=lambda r: -r["pct_change"])[:n]
    losers = sorted((r for r in rows if r["pct_change"] < 0), key=lambda r: r["pct_change"])[:n]
    log.info("pre-market movers: %d in universe, %d priced, %d qualified",
             len(syms), len(snap), len(rows))
    return {
        "gainers": gainers, "losers": losers, "as_of_et": as_of,
        "universe_size": len(syms), "priced": len(snap), "qualified": len(rows),
        "unavailable": None if (gainers or losers) else (
            f"no S&P 500 or Nasdaq-100 stock traded in at least {MIN_TRADE_WINDOWS} separate "
            f"5-minute windows with a trade in the {MAX_AGE_MIN} minutes before {as_of}"),
        "source": "yfinance 5-minute bars incl. pre-market",
    }


def reactions(symbols: list, now_et: Optional[dt.datetime] = None,
              download: Optional[Callable] = None, closes: Optional[Callable] = None) -> dict:
    """{symbol: {"pct", "as_of_et"}} for names with a fresh pre-market print."""
    out = {}
    for s, v in snapshot(list(dict.fromkeys(symbols)), now_et, download, closes).items():
        if v["pct_change"] is not None and v["age_min"] <= MAX_AGE_MIN:
            out[s] = {"pct": round(v["pct_change"], 2), "as_of_et": v["last_print_et"]}
    return out
```

- [ ] **Step 4: Run the tests**

Run: `$PY -m unittest mwtc.tests.test_premarket -v`
Expected: 12 tests, OK.

- [ ] **Step 5: Live replay check (network, free)**

```bash
$PY -c "
import datetime as dt; from zoneinfo import ZoneInfo
from mwtc.sources import premarket as pm
when = dt.datetime(2026, 10, 22, 8, 40, tzinfo=ZoneInfo('America/New_York'))
print(pm.reactions(['META'], when, download=lambda s: pm._download(s, period='1mo'),
                   closes=lambda s: pm._daily_closes(s, period='1mo')))"
```

Expected: `{'META': {'pct': -2.38, 'as_of_et': '8:40 AM ET'}}`. That is $680.90, the bar ending at 8:40, against the official $697.52 close.
- The 1-month period keeps 10/21 in range; Yahoo's 5-minute history lasts 60 days.
- At 8:35 the same check reads −2.98%: the bar ending at 8:35 was $676.75.
- The old 15:55-bar method would have read −2.51%, against $698.43.

- [ ] **Step 6: Commit**

```bash
git add mwtc/sources/premarket.py mwtc/tests/test_premarket.py
git commit -m "MWTC: real pre-market prices from yfinance (snapshot, movers, reactions)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Universe and wiring into the packet

**Files:**
- Modify: `mwtc/sources/fmp.py` (`UNIVERSE_FILE`, `_constituents`, `_universe_from_file`, `write_universe`, `index_universe`)
- Modify: `mwtc/main.py` (`_earnings_view`, `collect_data(mode)`, `main`)
- Modify: `mwtc/publish.py:443-444` (`run` passes the mode)
- Create: `mwtc/tests/test_collect_wiring.py`

**Interfaces:**
- Consumes:
  - `premarket.movers` / `premarket.reactions` (Task 3)
  - `earnings.result_line` (Task 2)
  - `is_overnight_result` / `timing_unconfirmed` (Task 1)
- Produces:
  - `fmp.UNIVERSE_FILE: Path`
  - `fmp.index_universe() -> list[dict]` with keys `symbol, name`
  - `fmp.write_universe(rows: list[dict], path: Path) -> None`
  - `main.collect_data(mode: str = "premarket") -> dict`

Packet shape in premarket mode:
- `movers` = the `premarket.movers` dict, or `{"gainers": None, "losers": None, "unavailable": "<reason>"}` when no prices came back at all.
- `earnings.calendar[*]` rows that are reported carry `result_line`. The overnight ones also carry `premarket_reaction_pct` and `premarket_as_of_et` when Yahoo had a fresh print.
- `premarket_reactions` = `{symbol: {"pct", "as_of_et"}}` for the focus tickers (SPY, QQQ, DIA, IWM).

In postmarket mode, `movers` are the FMP lists plus `"source": "FMP — today's regular session"`, and result lines carry no reaction part.

- [ ] **Step 1: Write the failing tests**

`mwtc/tests/test_collect_wiring.py`:

```python
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mwtc import main
from mwtc.sources import fmp


def fake_packets():
    macro_p = {"earnings_calendar": [
        {"symbol": "META", "date": "2026-10-21", "hour": "amc", "reported": True,
         "epsActual": 7.40, "epsEstimate": 5.79, "revenueActual": 64.6e9, "revenueEstimate": 63.1e9,
         "eps_surprise_pct": 27.8, "beat_miss": "beat", "revenue_surprise_pct": 2.4,
         "revenue_beat_miss": "beat", "is_today": False, "is_overnight_result": True,
         "timing_unconfirmed": False},
        {"symbol": "CLF", "date": "2026-10-21", "hour": "bmo", "reported": True,
         "epsActual": 0.10, "epsEstimate": 0.05, "eps_surprise_pct": 100.0, "beat_miss": "beat",
         "is_today": False, "is_overnight_result": False, "timing_unconfirmed": False}],
        "etf_spots": {}}
    fmp_p = {"available": True, "gainers": [{"ticker": "OLD", "pct_change": 50}], "losers": [],
             "earnings_calendar": [
                 {"symbol": "META", "date": "2026-10-21", "eps_actual": 7.40,
                  "revenue_actual": 64.6e9, "reported": True},
                 {"symbol": "CLF", "date": "2026-10-21", "eps_actual": 0.10, "reported": True}],
             "analyst_actions": [], "insider": [], "breadth": None, "economic_calendar": None}
    return macro_p, fmp_p


class Wiring(unittest.TestCase):
    def run_collect(self, mode, movers_ret, react_ret):
        macro_p, fmp_p = fake_packets()
        with mock.patch.object(main.macro, "collect", return_value=macro_p), \
             mock.patch.object(main.macro, "sector_breadth", return_value=None), \
             mock.patch.object(main.unusual_whales, "collect", return_value={"available": True}), \
             mock.patch.object(main.unusual_whales, "earnings", return_value=None), \
             mock.patch.object(main.fmp, "collect", return_value=fmp_p), \
             mock.patch.object(main.fmp, "index_universe", return_value=[{"symbol": "NEW", "name": "New"}]), \
             mock.patch.object(main.sentiment, "collect", return_value={}), \
             mock.patch.object(main.fedwatch, "probabilities", return_value=None), \
             mock.patch.object(main.kalshi, "fed_odds", return_value=None), \
             mock.patch.object(main.premarket, "movers", return_value=movers_ret), \
             mock.patch.object(main.premarket, "reactions", return_value=react_ret):
            return main.collect_data(mode=mode)

    def rows(self, d):
        return {r["symbol"]: r for r in d["earnings"]["calendar"]}

    def test_premarket_uses_real_movers_and_reactions(self):
        mv = {"gainers": [{"ticker": "NEW", "pct_change": 3.1}], "losers": [],
              "as_of_et": "8:40 AM ET", "unavailable": None}
        d = self.run_collect("premarket", mv, {"META": {"pct": -2.5, "as_of_et": "8:35 AM ET"}})
        self.assertEqual(d["movers"]["gainers"][0]["ticker"], "NEW")
        o = d["earnings"]["overnight_results"][0]
        self.assertEqual(o["premarket_reaction_pct"], -2.5)
        self.assertIn("pre-market -2.5% at 8:35 AM ET", o["result_line"])
        # yesterday's before-open print already moved yesterday: no reaction part
        self.assertNotIn("pre-market", self.rows(d)["CLF"]["result_line"])

    def test_premarket_movers_unavailable_is_stated_not_replaced(self):
        d = self.run_collect("premarket", None, {})
        self.assertIsNone(d["movers"]["gainers"])
        self.assertTrue(d["movers"]["unavailable"])
        self.assertIn("premarket_movers", d["notes"]["not_wired"])
        self.assertIn("reaction not available", d["earnings"]["overnight_results"][0]["result_line"])

    def test_postmarket_keeps_fmp_session_movers(self):
        d = self.run_collect("postmarket", None, {})
        self.assertEqual(d["movers"]["gainers"][0]["ticker"], "OLD")
        self.assertNotIn("pre-market", d["earnings"]["overnight_results"][0]["result_line"])


class Universe(unittest.TestCase):
    def test_universe_dedupes(self):
        with mock.patch.object(fmp, "_constituents", side_effect=[
                [{"symbol": "AAPL", "name": "Apple"}, {"symbol": "BRK.B", "name": "Berkshire"}],
                [{"symbol": "AAPL", "name": "Apple Inc"}, {"symbol": "GOOG", "name": "Alphabet"}]]):
            u = fmp.index_universe()
        self.assertEqual([r["symbol"] for r in u], ["AAPL", "BRK.B", "GOOG"])

    def test_missing_list_adds_the_committed_file(self):
        f = Path(tempfile.mkdtemp()) / "universe.txt"
        fmp.write_universe([{"symbol": "ARM", "name": "Arm Holdings"},
                            {"symbol": "AAPL", "name": "Apple"}], f)
        with mock.patch.object(fmp, "UNIVERSE_FILE", f), \
             mock.patch.object(fmp, "_constituents", side_effect=[[{"symbol": "AAPL", "name": "Apple"}], None]):
            u = fmp.index_universe()
        self.assertEqual([r["symbol"] for r in u], ["AAPL", "ARM"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify the tests fail**

Run: `$PY -m unittest mwtc.tests.test_collect_wiring -v`
Expected: ERROR, `AttributeError: <module 'mwtc.main'> does not have the attribute 'premarket'`, and `module 'mwtc.sources.fmp' has no attribute 'write_universe'`.

- [ ] **Step 3: Add the universe to `mwtc/sources/fmp.py`**

Add `from pathlib import Path` to the imports. Then add this below `_sp500_symbols`:

```python
UNIVERSE_FILE = Path(__file__).resolve().parent.parent / "data" / "universe.txt"


def _constituents(path: str) -> Optional[list]:
    rows = _get(path)
    if not isinstance(rows, list):
        return None
    return [{"symbol": r.get("symbol"), "name": r.get("name")}
            for r in rows if isinstance(r, dict) and r.get("symbol")]


def _universe_from_file(path: Optional[Path] = None) -> list:
    path = path or UNIVERSE_FILE
    try:
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            sym, _, name = line.partition("\t")
            if sym.strip():
                out.append({"symbol": sym.strip().upper(), "name": name.strip() or None})
        return out
    except OSError as e:
        log.warning("universe fallback file unreadable (%s)", e)
        return []


def write_universe(rows: list, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{r['symbol']}\t{r.get('name') or ''}\n" for r in rows),
                    encoding="utf-8")


def index_universe() -> list:
    """S&P 500 ∪ Nasdaq-100 as [{symbol, name}], deduplicated, from FMP. The
    committed list is added whenever either FMP list is missing, so the
    pre-market movers never depend on one feed."""
    sp = _constituents("/sp500-constituent")
    nd = _constituents("/nasdaq-constituent")
    rows = (sp or []) + (nd or [])
    if sp is None or nd is None:
        log.warning("FMP index list missing (S&P 500 %s, Nasdaq-100 %s) — adding %s",
                    "ok" if sp is not None else "MISSING",
                    "ok" if nd is not None else "MISSING", UNIVERSE_FILE.name)
        rows += _universe_from_file()
    seen, out = set(), []
    for r in rows:
        s = str(r.get("symbol") or "").strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append({"symbol": s, "name": r.get("name")})
    log.info("movers universe: %d symbols (S&P 500 %d, Nasdaq-100 %d from FMP)",
             len(out), len(sp or []), len(nd or []))
    return out
```

- [ ] **Step 4: Rewire `mwtc/main.py`**

Change the imports:

```python
from typing import Optional

from . import config, checks
from .sources import macro, unusual_whales, fmp, sentiment, fedwatch, kalshi, earnings, premarket
```

Replace the whole `_earnings_view` function:

```python
def _earnings_view(macro_packet: dict, uw_packet: dict, fmp_packet: dict,
                   reactions: Optional[dict] = None) -> dict:
    """Two-source earnings calendar (Finnhub primary x FMP cross-check) + per-focus
    UW detail. The reconciler flags any report-date / reported-status disagreement
    so the report never asserts an unverified earnings date or actual. Every
    reported row carries a code-built result_line the report quotes."""
    per_focus = {}
    for t in config.UW_FOCUS_TICKERS:
        per_focus[t] = unusual_whales.earnings(t)
    verified = earnings.build(
        macro_packet.get("earnings_calendar"),
        fmp_packet.get("earnings_calendar"),
    )
    # Results since the last close (previous session AMC / today BMO): the
    # freshest prints, usually the morning's biggest story.
    cal = verified.get("calendar") or []
    overnight = [r for r in cal if r.get("is_overnight_result")]
    # The reaction is this morning's real pre-market print (premarket.reactions),
    # never the previous session's movers list: on 2026-10-22 the report said
    # META "will gap up" while it traded -2.5% pre-market. Only overnight rows
    # get one (an older print already moved the stock); reactions=None
    # (postmarket) means no reaction part at all.
    for r in cal:
        live = reactions is not None and bool(r.get("is_overnight_result"))
        sym = str(r.get("symbol") or "").upper()
        if live and sym in reactions:
            r["premarket_reaction_pct"] = reactions[sym]["pct"]
            r["premarket_as_of_et"] = reactions[sym]["as_of_et"]
        r["result_line"] = earnings.result_line(r, include_reaction=live)
    return {
        "calendar": verified.get("calendar"),
        "overnight_results": overnight[:6] or None,
        "verification": verified.get("verification"),
        "second_source": verified.get("second_source"),
        "focus_detail": per_focus,
    }
```

In `collect_data`, make these changes:

1. Signature: `def collect_data(mode: str = "premarket") -> dict:`.
2. Right after `breadth = ...`, insert:

```python
    # Pre-Market report: movers and earnings reactions are THIS MORNING's real
    # pre-market prints. FMP's lists rank the last regular session, so on
    # 2026-10-20 they showed Monday's CLF/IRON/SLN moves as "pre-market".
    premarket_mode = mode != "postmarket"
    reactions = focus_reactions = None
    if premarket_mode:
        log.info("Collecting pre-market prices (yfinance, S&P 500 + Nasdaq-100)...")
        movers = premarket.movers(fmp.index_universe(), report_dt) or {
            "gainers": None, "losers": None,
            "unavailable": "pre-market prices could not be fetched this run"}
        ov_syms = [str(r.get("symbol") or "").upper()
                   for r in (macro_packet.get("earnings_calendar") or [])
                   if r.get("is_overnight_result")]
        reactions = premarket.reactions(ov_syms + list(config.UW_FOCUS_TICKERS), report_dt)
        focus_reactions = {t: reactions[t] for t in config.UW_FOCUS_TICKERS if t in reactions} or None
    else:
        movers = {"gainers": fmp_packet.get("gainers"), "losers": fmp_packet.get("losers"),
                  "source": "FMP — today's regular session"}
```

3. Change `earnings_view = _earnings_view(macro_packet, uw_packet, fmp_packet)` to `earnings_view = _earnings_view(macro_packet, uw_packet, fmp_packet, reactions)`.
4. Replace the `if not (fmp_packet.get("gainers") or fmp_packet.get("losers")):` check and its append with:

```python
    if not (movers.get("gainers") or movers.get("losers")):
        not_wired.append("premarket_movers" if premarket_mode else "session_movers")
```

5. In the returned dict:
   - Replace the `"movers": {...}` entry with `"movers": movers,` and add `"premarket_reactions": focus_reactions,` after it.
   - Replace the `"price_levels"` note with `"price_levels": ("yfinance index/ETF levels are prior-session closes; this morning's pre-market prints are in movers and premarket_reactions."),`.

In `main()`, change `data = collect_data()` to `data = collect_data(mode=mode)`. The line `mode = ...` already sits above it.

- [ ] **Step 5: Pass the mode from `mwtc/publish.py`**

In `run`: `data = mwtc_main.collect_data(mode=mode)`.

- [ ] **Step 6: Run all tests**

Run: `$PY -m unittest discover -s mwtc/tests -t . -v`
Expected: all OK.

- [ ] **Step 7: Commit**

```bash
git add mwtc/sources/fmp.py mwtc/main.py mwtc/publish.py mwtc/tests/test_collect_wiring.py
git commit -m "MWTC: pre-market movers from S&P 500 + Nasdaq-100, real reactions in each result line

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Instructions to the AI and the direction check

**Files:**
- Modify: `mwtc/report/prompt.py` (premarket lines 91, 98, 105; postmarket line 126)
- Modify: `mwtc/report/generator.py:176-229` (`_coverage_gaps`)
- Create: `mwtc/tests/test_direction_guard.py`

**Interfaces:**
- Consumes: the packet shape from Task 4:
  - `earnings.overnight_results[*].premarket_reaction_pct` and `result_line`;
  - `movers.gainers/losers[*].ticker/pct_change`, `movers.as_of_et`, `movers.unavailable`.
- Produces: new gap strings from `_coverage_gaps`. These feed the existing single retry in `generate()`, which already logs "coverage gaps persist after retry (publishing anyway)" when the second draft still fails.

- [ ] **Step 1: Write the failing tests**

`mwtc/tests/test_direction_guard.py`:

```python
import unittest

from mwtc.report import generator as g


def data(pct):
    row = {"symbol": "META", "result_line": "META — ..."}
    if pct is not None:
        row["premarket_reaction_pct"] = pct
    return {"earnings": {"overnight_results": [row]}, "movers": {}}


def html(text):
    return f"<html><head><style>p{{color:red}}</style></head><body><p>{text}</p></body></html>"


class DirectionGuard(unittest.TestCase):
    def gaps(self, text, pct):
        return [x for x in g._coverage_gaps(html(text), data(pct))
                if x.startswith("META") and ("direction call" in x or "not gapping" in x)]

    def test_no_data_no_direction_call(self):
        self.assertTrue(self.gaps("META beat estimates. META will gap up at the open.", None))
        self.assertTrue(self.gaps("Both META and TSLA will gap up on the beats.", None))
        self.assertTrue(self.gaps("META is set for a gap-up open.", None))
        self.assertFalse(self.gaps("META beat estimates; pre-market reaction not available — verify live.", None))

    def test_reaction_contradiction(self):
        self.assertTrue(self.gaps("META beat and will gap higher.", -2.5))
        self.assertFalse(self.gaps("META beat but trades -2.5% pre-market; expect a gap down.", -2.5))
        self.assertTrue(self.gaps("META missed and will gap down.", 3.0))
        self.assertFalse(self.gaps("META beat and will gap higher.", 0.4))   # inside ±1%: no call either way

    def test_unrelated_gap_language_is_fine(self):
        self.assertFalse(self.gaps("META beat. Gap-fade risk applies to small caps; AMD will gap up.", None))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify the tests fail**

Run: `$PY -m unittest mwtc.tests.test_direction_guard -v`
Expected: FAIL. `test_no_data_no_direction_call` and `test_reaction_contradiction` fail; the other passes.

- [ ] **Step 3: Add the check to `_coverage_gaps` in `mwtc/report/generator.py`**

Add these at module level, directly above `def _coverage_gaps`:

```python
# Direction calls on upper-cased visible text: "gap up", "gap-up", "gapping
# higher", "will open lower", "will rally"...
_DIR_UP = re.compile(r"\bGAP(?:S|PED|PING)?[\s-]+(?:UP|HIGHER)\b|\bWILL (?:OPEN|TRADE) "
                     r"(?:HIGHER|UP)\b|\bWILL (?:RALLY|SURGE|POP|JUMP)\b")
_DIR_DOWN = re.compile(r"\bGAP(?:S|PED|PING)?[\s-]+(?:DOWN|LOWER)\b|\bWILL (?:OPEN|TRADE) "
                       r"(?:LOWER|DOWN)\b|\bWILL (?:FALL|DROP|SINK|SLIDE|TUMBLE)\b")


def _sentences_with(text: str, sym: str) -> list:
    pat = re.compile(rf"\b{re.escape(sym)}\b")
    return [s for s in re.split(r"(?<=[.!?;])\s+", text) if pat.search(s)]
```

Inside `_coverage_gaps`, insert this after the existing reaction-contradiction `for r in ov:` loop and before `except`:

```python
        # No direction without data (META 2026-10-22: "will gap up" while it
        # traded -2.5% pre-market). A name with no pre-market figure gets no gap
        # call; a name with one (beyond ±1%) must not be called the other way.
        for r in ov:
            sym = str(r.get("symbol") or "").upper()
            if not sym:
                continue
            sents = _sentences_with(up, sym)
            try:
                pct = float(r.get("premarket_reaction_pct"))
            except (TypeError, ValueError):
                pct = None
            if pct is None:
                if any(_DIR_UP.search(s) or _DIR_DOWN.search(s) for s in sents):
                    gaps.append(f"{sym} has no pre-market data in the packet — remove the "
                                "gap/direction call and write 'pre-market reaction not "
                                "available — verify live'")
            elif pct <= -1 and any(_DIR_UP.search(s) for s in sents):
                gaps.append(f"{sym} trades {pct:+.1f}% pre-market — it is not gapping up; "
                            "describe the actual move")
            elif pct >= 1 and any(_DIR_DOWN.search(s) for s in sents):
                gaps.append(f"{sym} trades {pct:+.1f}% pre-market — it is not gapping down; "
                            "describe the actual move")
```

`up` is the existing upper-cased visible text, with `<style>`/`<script>` and tags stripped. Splitting into sentences keeps "AMD will gap up." apart from a META sentence ending in "." or ";".

- [ ] **Step 4: Update the four instruction lines in `mwtc/report/prompt.py`**

Use exact-string `Edit` replacements:

1. **Line 91 (premarket §3).** Replace the substring
   `PAIR THE PRINT WITH ITS REACTION: when an overnight_results row carries premarket_reaction_pct (or its ticker sits in [movers]), report the result AND the stock's actual move together`
   with
   `PAIR THE PRINT WITH ITS REACTION: each overnight_results row has a result_line (figures and beat/miss computed by code — quote it, never restate EPS or revenue from raw fields) ending in the stock's real pre-market move or "pre-market reaction not available — verify live"; report the result AND that move together. NO DIRECTION WITHOUT DATA: if a row has no pre-market move, do not say it will gap, open higher/lower, rally or fall — say the reaction is not available`.
   Keep the rest of the line, from `— a beat trading DOWN` onward, unchanged.

2. **Line 98 (premarket §9).** Replace
   `(last night's after-close + this morning's prints: beat/miss, surprise %, one-line read each — these are the freshest results and set today's tone)`
   with
   `(the previous trading day's after-close + this morning's pre-open prints: quote each row's result_line verbatim, then a one-line read — these are the freshest results and set today's tone; rows with timing_unconfirmed=true go under Unconfirmed as "timing unconfirmed — verify", never as overnight news)`.

3. **Line 105 (premarket §16).** Replace the whole line with:
   `16. Pre-Market Movers [movers.gainers/losers] — section heading must read "Pre-Market Movers". These are S&P 500 and Nasdaq-100 stocks moving BEFORE the open, priced from this morning's pre-market trades. Subtitle: "S&P 500 and Nasdaq-100 names moving before the open, as of <movers.as_of_et>". List each on its OWN line as "TICKER — Company  +X% (last trade <last_print_et>)", gainers then losers, with a brief read only where the packet supports one. If movers.unavailable is set, print that sentence instead of a list — never substitute yesterday's movers or invent names.`

4. **Line 126 (postmarket §10).** Replace `; group day-session vs after-hours.` with `. These are today's regular-session moves (the packet has no after-hours data — do not label any move after-hours).`

- [ ] **Step 5: Run all tests and check the prompt**

Run: `$PY -m unittest discover -s mwtc/tests -t . -v`
Expected: all OK.

Run:
```bash
$PY -c "
from mwtc.report import prompt
s, u = prompt.build_messages('premarket', '{}', '<html></html>', 'Monday')
t = s + u
assert 'NO DIRECTION WITHOUT DATA' in t and 'never substitute yesterday' in t and 'timing_unconfirmed' in t
s, u = prompt.build_messages('postmarket', '{}', '<html></html>', 'Monday')
assert 'day-session vs after-hours' not in s + u
print('prompt ok')"
```
Expected: `prompt ok`.

- [ ] **Step 6: Commit**

```bash
git add mwtc/report/prompt.py mwtc/report/generator.py mwtc/tests/test_direction_guard.py
git commit -m "MWTC: no gap/direction call without pre-market data; quote the code-built result line

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Keep each run's earnings and movers data, and a preview mode

**Files:**
- Modify: `mwtc/publish.py` (`OUT`, `TRACE_KEYS`, `dump_trace`, `run(... preview)`, `main`, `_packet_probe`)
- Modify: `.gitignore` (add `mwtc_out/`)
- Create: `mwtc/tests/test_publish_modes.py`

**Interfaces:**
- Consumes:
  - `main.collect_data(mode)` (Task 4)
  - `fmp.index_universe` / `fmp.write_universe` (Task 4)
- Produces:
  - `publish.OUT: Path` (`<repo>/mwtc_out`)
  - `publish.dump_trace(data: dict, mode: str) -> Path`
  - `publish.run(mode, dry_run=False, stub=False, preview=False) -> int`
  - CLI `python -m mwtc.publish --mode premarket --preview`
  - Files in `mwtc_out/`: `<mode>-<ET date>.json` (trace), `<mode>-<ET date>.html` (preview only), `universe.txt` (dry-run only)

- [ ] **Step 1: Write the failing tests**

`mwtc/tests/test_publish_modes.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mwtc import publish

PACKET = {"report_date": "Monday", "mode": "premarket", "generated_at_et": "x",
          "movers": {"gainers": []}, "premarket_reactions": None, "notes": {},
          "earnings": {"calendar": [], "overnight_results": None, "focus_detail": {"SPY": "uw"}},
          "institutional": {"paid": "Unusual Whales"}, "fed": {"fedwatch": "CME"}}


class Modes(unittest.TestCase):
    def run_publish(self, **kw):
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(publish, "OUT", tmp), \
             mock.patch.object(publish.mwtc_main, "collect_data", return_value=dict(PACKET)), \
             mock.patch.object(publish, "derive_sidecar", return_value={}), \
             mock.patch.object(publish.generator, "generate", return_value="<html>ok</html>"), \
             mock.patch.object(publish, "publish_html", return_value=tmp / "p.html") as pub, \
             mock.patch.object(publish, "notify") as note:
            rc = publish.run("premarket", **kw)
        return rc, tmp, pub, note

    def test_preview_writes_files_and_publishes_nothing(self):
        rc, tmp, pub, note = self.run_publish(preview=True)
        self.assertEqual(rc, 0)
        pub.assert_not_called()
        note.assert_not_called()
        self.assertEqual(sorted(p.suffix for p in tmp.iterdir()), [".html", ".json"])

    def test_trace_keeps_no_paid_feed_data(self):
        _, tmp, pub, _ = self.run_publish()
        pub.assert_called_once()
        saved = json.loads(next(tmp.glob("*.json")).read_text())
        self.assertNotIn("institutional", saved)
        self.assertNotIn("fed", saved)
        self.assertNotIn("focus_detail", saved["earnings"])
        self.assertIn("movers", saved)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify the tests fail**

Run: `$PY -m unittest mwtc.tests.test_publish_modes -v`
Expected: ERROR, `AttributeError: <module 'mwtc.publish'> does not have the attribute 'OUT'`.

- [ ] **Step 3: Implement in `mwtc/publish.py`**

Below the `KEEP = 5` line:

```python
# Each run's trace file (+ the preview page): uploaded as a GitHub artifact,
# never published or committed. The repo is PUBLIC, so its artifacts are too:
# only the earnings + movers slice is kept (enough to tell whether a wrong
# figure came from the feed or from the model) — no Unusual Whales, CME
# FedWatch, analyst or insider payloads.
OUT = REPO_ROOT / "mwtc_out"
TRACE_KEYS = ("generated_at_et", "report_date", "mode", "movers",
              "premarket_reactions", "earnings", "notes")


def dump_trace(data: dict, mode: str) -> Path:
    trace = {k: data.get(k) for k in TRACE_KEYS}
    trace["earnings"] = {k: v for k, v in (data.get("earnings") or {}).items()
                         if k != "focus_detail"}   # focus_detail is UW data
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{mode}-{utc_date()}.json"
    p.write_text(json.dumps(trace, default=str, indent=1), encoding="utf-8")
    return p
```

Replace `run` with:

```python
def run(mode: str, dry_run: bool = False, stub: bool = False, preview: bool = False) -> int:
    data = mwtc_main.collect_data(mode=mode)
    data["mode"] = mode
    try:
        print(f"trace saved -> {dump_trace(data, mode)}")
    except Exception as e:  # noqa: BLE001 — the trace must never cost the report
        print(f"  !! trace not saved (non-fatal) — {e}")
    sidecar = derive_sidecar(data, mode)
    print("sidecar:", json.dumps(sidecar, ensure_ascii=False))

    if dry_run:
        _packet_probe(data)
        print("DRY RUN — collected data + derived sidecar; no report written.")
        return 0

    if stub:
        html = STUB_HTML
    else:
        html = generator.generate(data, data["report_date"], mode=mode)

    if preview:
        OUT.mkdir(parents=True, exist_ok=True)
        p = OUT / f"{mode}-{utc_date()}.html"
        p.write_text(html, encoding="utf-8")
        print(f"PREVIEW — report written to {p}; nothing published, nobody notified.")
        return 0

    out = publish_html(html, data, mode)
    print(f"Published {NAME[mode]} -> {out}")
    if not stub:  # don't notify on a stub/test render
        try:
            notify(sidecar, mode)
        except Exception as e:
            print(f"  !! notification failed (non-fatal) — {e}")
    return 0
```

In `main`, add the flag before `a = ap.parse_args(argv)`, and pass it through:

```python
    ap.add_argument("--preview", action="store_true",
                    help="Generate the report into mwtc_out/ only: no publish, no notification.")
    a = ap.parse_args(argv)
    return run(a.mode, dry_run=a.dry_run, stub=a.stub, preview=a.preview)
```

In `_packet_probe`, insert this inside the `try`, right before `governed = generator._cap_packet(...)`:

```python
        mv = data.get("movers") or {}
        print(f"pre-market movers: as of {mv.get('as_of_et')}, universe {mv.get('universe_size')}, "
              f"priced {mv.get('priced')}, qualified {mv.get('qualified')}, "
              f"unavailable={mv.get('unavailable')!r}")
        for grp in ("gainers", "losers"):
            print(f"  {grp}: " + ", ".join(
                f"{r.get('ticker')} {r.get('pct_change'):+.1f}% ({r.get('last_print_et')}, "
                f"{r.get('trade_windows')} windows)" for r in (mv.get(grp) or [])[:10]))
        print(f"focus reactions: {data.get('premarket_reactions')}")
        for r in ((data.get("earnings") or {}).get("overnight_results") or []):
            print(f"  overnight: {r.get('result_line')}")
        try:
            from .sources import fmp as _fmp
            u = _fmp.index_universe()
            _fmp.write_universe(u, OUT / "universe.txt")
            print(f"universe: {len(u)} symbols -> {OUT / 'universe.txt'}")
        except Exception as e:  # noqa: BLE001
            print(f"universe dump failed (non-fatal): {e}")
```

Append to `.gitignore`:

```
# MWTC run trace + preview pages (uploaded as GitHub artifacts, never committed)
mwtc_out/
```

- [ ] **Step 4: Run all tests**

Run: `$PY -m unittest discover -s mwtc/tests -t . -v`
Expected: all OK.

Run: `$PY -m mwtc.publish --mode premarket --stub --preview`
Expected: prints `trace saved -> …/mwtc_out/premarket-<date>.json` and `PREVIEW — report written to …`. No file under `site/` changes; check with `git status --short site/`, which should print nothing. This step does a real data collection. Keys missing locally mean sources log warnings and return `None`, which is fine.

- [ ] **Step 5: Commit**

```bash
git add mwtc/publish.py .gitignore mwtc/tests/test_publish_modes.py
git commit -m "MWTC: keep each run's earnings/movers trace; --preview writes the report without publishing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Workflow — preview input, artifact, guards

**Files:**
- Modify: `.github/workflows/mwtc.yml`

**Interfaces:**
- Consumes: `python -m mwtc.publish --mode <m> --preview` and `mwtc_out/` (Task 6).
- Produces:
  - `workflow_dispatch` input `preview` (boolean, default false).
  - An artifact named `mwtc-<mode>-packet` containing `mwtc_out/`.

- [ ] **Step 1: Edit `.github/workflows/mwtc.yml`**

1. Under `workflow_dispatch.inputs`, after `force`:

```yaml
      preview:
        description: "PREVIEW: build into mwtc_out/ (artifact only) — nothing published, committed or notified. Required on any branch but main."
        type: boolean
        default: false
```

2. Concurrency group:

```yaml
  group: build-mwtc-${{ github.event.inputs.mode }}${{ inputs.preview && '-preview' || '' }}
```

3. Job `env`: add `PREVIEW_IN: ${{ inputs.preview }}` under `FORCE_IN`.

4. Guard step: insert right after the `MODE="${MODE_IN:-premarket}"` line, before the `check-keys` line:

```bash
          # Only main may publish. A report run on any other ref must be a
          # preview; check-keys is a $0 probe and may run anywhere.
          if [ "$MODE" != "check-keys" ] && [ "$GITHUB_REF_NAME" != "main" ] && [ "$PREVIEW_IN" != "true" ]; then
            echo "::error::Ref '$GITHUB_REF_NAME' is not main — re-run with preview=true (a preview publishes nothing)."
            exit 1
          fi
          # A preview skips the market-day/already-built checks and every
          # publishing step (they all require go=true).
          if [ "$PREVIEW_IN" = "true" ]; then echo "go=false" >> "$GITHUB_OUTPUT"; exit 0; fi
```

5. New step after "Build & publish the report (live data + Anthropic)". It has no Telegram variables, by design:

```yaml
      - name: Build a PREVIEW (no publish, no notification)
        if: inputs.preview == true && inputs.mode != 'check-keys'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          UW_API_KEY: ${{ secrets.UW_API_KEY }}
          FINNHUB_API_KEY: ${{ secrets.FINNHUB_API_KEY }}
          NASDAQ_DATA_LINK_API_KEY: ${{ secrets.NASDAQ_DATA_LINK_API_KEY }}
          FMP_API_KEY: ${{ secrets.FMP_API_KEY }}
          CME_FEDWATCH_API_ID: ${{ secrets.CME_FEDWATCH_API_ID }}
          CME_FEDWATCH_API_SECRET: ${{ secrets.CME_FEDWATCH_API_SECRET }}
          KALSHI_API_KEY_ID: ${{ secrets.KALSHI_API_KEY_ID }}
          KALSHI_PRIVATE_KEY: ${{ secrets.KALSHI_PRIVATE_KEY }}
        run: python -m mwtc.publish --mode "${MODE_IN:-premarket}" --preview
```

6. New step after it:

```yaml
      # Earnings + movers trace (and the preview page). The repo is public, so
      # this artifact is too: mwtc/publish.py keeps paid-feed data out of it.
      - name: Keep this run's trace (artifact, 14 days)
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: mwtc-${{ inputs.mode }}-packet
          path: mwtc_out/
          retention-days: 14
          if-no-files-found: ignore
```

7. "Commit & push the rebuilt site": change `if:` to `steps.guard.outputs.go == 'true' && inputs.preview != true`. Insert these as the first lines of its `run`:

```bash
          if [ "$PREVIEW_IN" = "true" ] || [ "$GITHUB_REF_NAME" != "main" ]; then
            echo "::error::refusing to publish (preview=$PREVIEW_IN, ref=$GITHUB_REF_NAME)"; exit 1
          fi
```

8. "Check in to MWTC Health": append `&& inputs.preview != true` to its `if:`.

- [ ] **Step 2: Validate the YAML**

Run: `$PY -c "import yaml; d=yaml.safe_load(open('.github/workflows/mwtc.yml')); print([s['name'] for s in d['jobs']['mwtc']['steps']]); print(d[True]['workflow_dispatch']['inputs']['preview'])"`
Expected:
- the list includes "Build a PREVIEW (no publish, no notification)" and "Keep this run's trace (artifact, 14 days)";
- the input prints with `'type': 'boolean'`. PyYAML reads the `on:` key as `True`.

- [ ] **Step 3: Exercise the guard script locally**

```bash
G=$(mktemp)
$PY -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/mwtc.yml')); open(sys.argv[1],'w').write([s for s in d['jobs']['mwtc']['steps'] if s.get('id')=='guard'][0]['run'])" "$G"
for c in "premarket false feat/x" "premarket true feat/x" "check-keys false feat/x" "premarket true main"; do
  set -- $c; echo "== mode=$1 preview=$2 ref=$3"
  MODE_IN=$1 PREVIEW_IN=$2 GITHUB_REF_NAME=$3 FORCE_IN=false GITHUB_OUTPUT=/dev/stdout bash "$G"; echo "exit=$?"
done
```

Expected, in order:
- the "not main" error and `exit=1`;
- `go=false exit=0`;
- `go=false exit=0`;
- `go=false exit=0`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/mwtc.yml
git commit -m "mwtc.yml: preview input, run-trace artifact, non-main runs must be previews

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Prove it on GitHub, preview, review, merge

**Files:**
- Create: `mwtc/data/universe.txt` (from the probe's artifact)
- Create on `main`, preview only: `site/preview/pre-market-fix/index.html`

- [ ] **Step 1: Push the branch and run the $0 probe on it**

```bash
git push -u origin feat/premarket-data-fixes
gh workflow run mwtc.yml --ref feat/premarket-data-fixes -f mode=check-keys
gh run list --workflow mwtc.yml --branch feat/premarket-data-fixes --limit 1
```

Watch the run with `gh run watch <run-id>`. Then read the log lines `movers universe:`, `pre-market movers:`, `focus reactions:`, `overnight:` and `universe:` with `gh run view <run-id> --log | grep -E "movers universe|pre-market movers|focus reactions|overnight:|universe:"`.

Expected:
- a universe of about 520 symbols, with both FMP lists "ok";
- `priced` close to the universe size, and no `pre-market download failed` or `summary failed` warnings. This is the first run on pandas 3.0.6 and yfinance 1.7.0.

Between 4:00 and 9:30 AM ET on a weekday, gainers and losers should show last-trade times within 30 minutes. At other times the movers read "as of 9:30 AM ET" (the latest session) or carry `unavailable`, and that's fine for this step.

If `/nasdaq-constituent` comes back MISSING (plan-gated), stop and report it. The fallback file would then have to come from another source.

- [ ] **Step 2: Commit the fallback universe**

```bash
D=$(mktemp -d)
gh run download <run-id> -n mwtc-check-keys-packet -D "$D"
wc -l "$D/universe.txt"
cp "$D/universe.txt" mwtc/data/universe.txt
git add mwtc/data/universe.txt
git commit -m "MWTC: fallback S&P 500 + Nasdaq-100 universe (from the FMP probe)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
```

Expected: about 520 lines.

- [ ] **Step 3: Preview run (one paid run, about $1.40)**

Run this on a weekday morning between 8:00 and 9:30 AM ET, so the movers and reactions are live:

```bash
gh workflow run mwtc.yml --ref feat/premarket-data-fixes -f mode=premarket -f preview=true
```

Expected:
- "Build & publish", "Commit & push" and "Check in to MWTC Health" are skipped;
- no Telegram message arrives;
- the `mwtc-premarket-packet` artifact holds `premarket-<date>.html` and `.json`.

Check the HTML:
- "Pre-Market Movers" lists S&P 500/Nasdaq-100 names with last-trade times and the "as of" subtitle;
- every "Reported Since Last Close" line matches its `result_line` in the JSON, including a pre-market % or "not available — verify live";
- no "will gap up/down" appears for a name without data;
- the run log has no "coverage gaps persist".

- [ ] **Step 4: Publish the preview page for the owner (unlisted)**

From a temporary worktree on `origin/main`:

1. Copy the preview HTML to `site/preview/pre-market-fix/index.html`.
2. Add `<meta name="robots" content="noindex">` and a fixed "PREVIEW — not the live report" ribbon.
3. Run `git add -f site/preview/pre-market-fix/index.html`. The force is needed because the `preview/` ignore rule also matches `site/preview/`.
4. Commit, push, and confirm HEAD moved.
5. Check `https://tradeclub-ai-reports.mwtradecoaching.com/preview/pre-market-fix/`, then send the link to the owner.

- [ ] **Step 5: Independent review (gates the merge; runs while the owner reads the preview)**

Review `git diff origin/main...feat/premarket-data-fixes` through three lenses:
- **Data correctness:** pre-market math, UTC to ET conversion, session edges.
- **Packet against prompt:** every field the prompt names exists in the packet.
- **Workflow guards:** a preview never publishes, commits, checks in or notifies, and the scheduled main path is unchanged apart from the artifact.

Fix confirmed findings with tests.

- [ ] **Step 6: Merge after the owner's OK**

Merge `feat/premarket-data-fixes` into `main` when no MWTC run is in progress. Not between 8:35 and 8:55 AM or 4:25 and 4:45 PM ET on a weekday. Push.

On the next scheduled Pre-Market run:
- check the log for `pre-market movers:` and `trace saved`;
- check the artifact;
- read the published page at `/pre-market`.

---

## Self-review notes

- **Spec coverage:**

  | Spec component | Plan task |
  |---|---|
  | 1. Pre-market prices | Tasks 3–4 |
  | 2. Overnight window | Task 1 |
  | 3. Earnings figures in code | Task 2 (wired in Task 4) |
  | 4. No direction without data | Task 5 |
  | 5. Movers and Market Wrap wording | Task 5, Step 4 |
  | 6. Keep each run's data | Tasks 6–7 |
  | 7. Preview mode | Tasks 6–8 |
  | Testing | each task, plus Task 8 (historical replay in Task 1's tests and Task 3, Step 5) |

- **Changes from the spec found while planning.** The first two were approved by the owner on 2026-10-26:
  1. **Liquidity filter.** Yahoo has no pre-market volume (every bar is 0), so "$1M traded" is replaced by "trades in at least 6 separate 5-minute windows". The measurements behind this are in Global Constraints.
  2. **Run data.** The repo is public, so the artifact keeps only the earnings and movers slice, not the full packet.
  3. **Smaller fixes, no decision needed:**
     - The previous-trading-day helper is a new `mwtc/sources/sessions.py`, because `fmp.py` needs it too.
     - The download covers 5 days, not 2, so a Monday after a holiday still finds the prior close.
     - A second, daily download provides the official prior close. The spec's 10/22 META "−2.5%" figure used the bar *labeled* 8:35, which isn't known until 8:40, against the 15:55 bar. Done correctly it is −2.38% at 8:40.
     - The universe is about 520 unique names, not 600.
- **Name consistency.**
  - `premarket_reaction_pct`, `premarket_as_of_et` and `result_line` are spelled identically in Tasks 2, 4, 5 and 6.
  - The mover keys (`ticker`, `pct_change`, `last_print_et`, `trade_windows`) and the movers dict keys (`as_of_et`, `unavailable`, `priced`, `qualified`, `universe_size`) are consistent across Tasks 3–6.
