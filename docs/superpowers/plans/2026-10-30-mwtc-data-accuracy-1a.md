# MWTC Data Accuracy, Wave 1a — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every number the MWTC bot (Pre-Market and Market Wrap) computes for the AI correct, labeled with its basis and date, and stated in Eastern Time. The AI must never convert, rank or label these numbers itself.

**Architecture:** All changes are in the existing `mwtc/` collectors and prompt. No new service is added.
- `macro._quote` gains a `kind` argument: index, futures, fx, crypto or global. It returns a true 52-week range, as-of stamps and, for futures, a change measured against the contract Yahoo is actually quoting.
- Time handling moves into `mwtc/sources/sessions.py`: UTC→ET conversion, the next trading day, and economic-calendar rows.
- `main.collect_data` threads the ET date through to every collector. It also stamps UW rows and options data with ET times and an as-of.
- The prompt tells the AI to copy the code's fields verbatim.

**Tech Stack:** Python 3.9 (local venv) / 3.11 (Actions), yfinance 1.2.0, pandas, pandas_market_calendars, zoneinfo, unittest.

**Spec:** `docs/superpowers/specs/2026-10-29-mwtc-data-accuracy-1a-design.md` (commit 2ca489e; owner "OK" 2026-10-30).

## Global Constraints

- The repo is PUBLIC. No Unusual Whales, CME FedWatch or other paid-feed value may go into logs, traces or artifacts. New code logs counts only, never row values.
- Never read `.env` or `.uw_token`. API keys live only in GitHub secrets.
- `last` for futures always comes from the continuous `=F` series. `prev_close`, `change` and `pct_change` come from the contract's own bars, roll or not. Never use `fast_info.regularMarketPreviousClose`.
- Fail-safe output for futures is exactly `prev_close=None, change=None, pct_change=None, roll_note="contract roll — change withheld"`.
- RSI thresholds are defined once, as `config.RSI_OVERBOUGHT = 70.0` and `config.RSI_OVERSOLD = 30.0`. Comparisons are `>=` and `<=` everywhere, including gauges.
- The 52-week window is a calendar cut, `index >= last_date − 365 days`. Never a row count.
- ET only: zoneinfo `America/New_York`, never a hard-coded −4 (DST ends Sun 2026-11-01).
- `time_et` format is `"8:30 AM"`: no leading zero and no "ET" suffix. Row stamps that include a day use the format `"Wed 4:30 PM ET"`.
- Never use `datetime.fromisoformat` on feed strings (Python 3.9 rejects `Z` and odd fractions).
- Exactly one model call per run. Report sections, look and cost do not change.
- Merge only after the owner's OK, outside 8:30–9:00 AM ET and 4:20–4:50 PM ET.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

These are the five failure modes most likely to bite readers that no single task's tests fully exercise. Each one has a pinned test in the owning task.

1. **A roll day where Yahoo's chart `previousClose` is still the old contract's settle.** Expected: the change is withheld, never shown against the old contract. Task 3 test `test_metadata_disagrees_with_contract_withholds`.
2. **A Wrap run that fires after UTC midnight** (a late GitHub backup at 9 PM ET). Expected: "today" stays the ET date for every calendar. Task 7 test `test_late_run_keeps_et_today`.
3. **A Friday Wrap** whose "Tomorrow's Setup" must point to Monday, across the DST change. Expected: Monday rows are `is_next_session_et` and Saturday rows are not. Task 6 test `test_friday_next_session_is_monday_across_dst`.
4. **An Unusual Whales row with an unparsable time.** Expected: the raw value never reaches a log, and the row keeps no UTC field the AI could misread. Task 10 test `test_unparsable_uw_time_is_not_logged`.
5. **A Pre-Market run during the session (the preview).** Expected: today's index bar is `bar_complete False`, while yesterday's is complete. Task 2 test `test_index_bar_during_session_is_incomplete`.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `mwtc/config.py` | modify | `INDICES` adds `"Nasdaq 100": "^NDX"`; RSI threshold constants |
| `mwtc/sources/macro.py` | modify | Wilder `_rsi` + `rsi_zone`; `_quote(kind, now_et)` with 52w/as-of; `_futures_fields`; `rank_sectors`; `economic_calendar(today)`; `economic_news` `when_et`; `collect(today, now_et)` |
| `mwtc/sources/sessions.py` | modify | `ET`, `next_trading_day`, `parse_utc`, `fmt_time_et`, `fmt_stamp_et`, `econ_rows_et`, `add_et_twins` |
| `mwtc/sources/fmp.py` | modify | `economic_calendar(days, today)`; `collect(today)` |
| `mwtc/sources/sentiment.py` | modify | Fear & Greed `score_display`, `band`, `points_to_neutral` |
| `mwtc/checks.py` | modify | ET date in `_fmp_econ` |
| `mwtc/main.py` | modify | `collect_data(mode, now_et=None)`; ET plumbing; econ normalization; UW ET twins; options/breadth as-of; `after_close` |
| `mwtc/report/gauges.py` | modify | thresholds from config; `"Nasdaq 100"` keys; F&G `score_display` |
| `mwtc/publish.py` | modify | `derive_sidecar` key `"Nasdaq 100"` |
| `mwtc/report/prompt.py` | modify | rule 7 "CODE-COMPUTED FIELDS ARE FINAL" + section edits |
| `mwtc/tests/test_rsi.py` | create | Task 1 |
| `mwtc/tests/test_quote.py` | create | Task 2 |
| `mwtc/tests/test_futures_roll.py` | create | Task 3 |
| `mwtc/tests/test_ndx_keys.py` | create | Task 4 |
| `mwtc/tests/test_sector_ranks.py` | create | Task 5 |
| `mwtc/tests/test_et_times.py` | create | Task 6 |
| `mwtc/tests/test_collect_et.py` | create | Tasks 7–8 |
| `mwtc/tests/test_fear_greed.py` | create | Task 9 |
| `mwtc/tests/test_public_trace.py` | create | Task 10 |
| `mwtc/tests/test_prompt_rules.py` | modify | Task 11 |

**Run all tests**, from the worktree root:
`PYTHONDONTWRITEBYTECODE=1 /Users/admin/Desktop/Trae-Projects/TradeClub-AIReports-Sectors/.venv/bin/python -m unittest discover -s mwtc/tests -t .`

**Run one module:** same command with `-m unittest mwtc.tests.test_rsi -v`.

Below, `PY` means `PYTHONDONTWRITEBYTECODE=1 /Users/admin/Desktop/Trae-Projects/TradeClub-AIReports-Sectors/.venv/bin/python`.

---

### Task 1: Wilder RSI, code-assigned zone, shared thresholds

**Files:**
- Modify: `mwtc/config.py` (append after `SECTOR_ETFS`)
- Modify: `mwtc/sources/macro.py:71-106` (`_technical_levels`, `_rsi`)
- Modify: `mwtc/report/gauges.py:44-54` (`directional_lean`) and `:188-197` (`directional_bias`)
- Test: `mwtc/tests/test_rsi.py`

**Interfaces:**
- Produces: `config.RSI_OVERBOUGHT: float = 70.0`, `config.RSI_OVERSOLD: float = 30.0`. Also `macro._rsi(closes: list[float], period: int = 14) -> Optional[float]` (Wilder, rounded to 1 dp) and `macro.rsi_zone(rsi: Optional[float]) -> Optional[str]` (`"overbought"`, `"oversold"`, `"neutral"` or None). `_technical_levels` output gains `"rsi_zone"`.

- [ ] **Step 1: Write the failing test**

```python
# mwtc/tests/test_rsi.py
import unittest

import pandas as pd

from mwtc import config
from mwtc.sources import macro
from mwtc.report import gauges

# Wilder's own worked example (StockCharts "RSI" article): 70.53 then … 57.97.
WILDER = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
          45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]


def ewm_rsi(closes, period=14):
    s = pd.Series(closes).diff().dropna()
    up = s.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    dn = (-s.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    return 100 - 100 / (1 + up / dn)


class Rsi(unittest.TestCase):
    def test_wilder_worked_example(self):
        self.assertEqual(macro._rsi(WILDER[:15]), 70.5)
        self.assertEqual(macro._rsi(WILDER), 57.9)

    def test_matches_ewm_reference_on_long_series(self):
        import math
        closes = [100 + 10 * math.sin(i / 7) + i * 0.05 for i in range(250)]
        self.assertAlmostEqual(macro._rsi(closes), ewm_rsi(closes), delta=0.1)

    def test_not_the_old_simple_average(self):
        # The old _rsi averaged only the last 14 changes (Cutler): on this series
        # the two methods differ by more than 5 points.
        closes = [100.0] * 30 + [100 + i for i in range(1, 15)] + [114 - i * 0.5 for i in range(1, 15)]
        gains = [max(closes[i] - closes[i - 1], 0) for i in range(len(closes) - 14, len(closes))]
        losses = [max(closes[i - 1] - closes[i], 0) for i in range(len(closes) - 14, len(closes))]
        cutler = 100 - 100 / (1 + (sum(gains) / 14) / (sum(losses) / 14))
        self.assertGreater(abs(macro._rsi(closes) - cutler), 5)

    def test_too_short(self):
        self.assertIsNone(macro._rsi([1.0] * 14))

    def test_zone_edges(self):
        self.assertEqual(macro.rsi_zone(69.9), "neutral")
        self.assertEqual(macro.rsi_zone(70.0), "overbought")
        self.assertEqual(macro.rsi_zone(30.0), "oversold")
        self.assertEqual(macro.rsi_zone(30.1), "neutral")
        self.assertIsNone(macro.rsi_zone(None))

    def test_thresholds_live_in_config(self):
        self.assertEqual((config.RSI_OVERBOUGHT, config.RSI_OVERSOLD), (70.0, 30.0))

    def test_gauges_fade_applies_at_exactly_70(self):
        # 50 +4 (rsi>55) -3 (overbought fade) = 51; the old strict '>' gave 54.
        self.assertEqual(gauges.directional_lean({"trend": "flat", "rsi14": 70.0}, None), 51)
        self.assertEqual(gauges.directional_lean({"trend": "flat", "rsi14": 69.9}, None), 54)
        # bias: +12 (>55) -8 (fade) = 4
        self.assertEqual(gauges.directional_bias({"trend": "flat", "rsi14": 70.0}, None), 4)
        self.assertEqual(gauges.directional_bias({"trend": "flat", "rsi14": 30.0}, None), -4)

    def test_technicals_carry_zone(self):
        closes = [100 + (i % 5) for i in range(80)]
        t = macro._technical_levels({"closes": closes})
        self.assertIn(t["rsi_zone"], ("overbought", "oversold", "neutral"))
        self.assertEqual(t["rsi_zone"], macro.rsi_zone(t["rsi14"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `PY -m unittest mwtc.tests.test_rsi -v`
Expected: FAIL. `test_wilder_worked_example` returns the Cutler values; `rsi_zone` and `config.RSI_OVERBOUGHT` raise AttributeError.

- [ ] **Step 3: Implement**

Append to `mwtc/config.py`:

```python
# RSI zones — the ONE definition used by the collectors (rsi_zone) and the
# dials (gauges). Wilder's RSI; >= overbought, <= oversold.
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
```

Replace `_rsi` in `mwtc/sources/macro.py` and add `rsi_zone` after it:

```python
def _rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Wilder's RSI (what brokers show): seed with the simple average of the
    first `period` gains/losses, then smooth avg = (avg*(period-1) + x)/period
    over the WHOLE history. The old version averaged only the last 14 changes
    and printed the Dow at 68.1 "overbought" on 2026-10-28 (Wilder: 55.8)."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def rsi_zone(rsi: Optional[float]) -> Optional[str]:
    """The only RSI label the report may print (code-assigned, never the AI's)."""
    if rsi is None:
        return None
    if rsi >= config.RSI_OVERBOUGHT:
        return "overbought"
    if rsi <= config.RSI_OVERSOLD:
        return "oversold"
    return "neutral"
```

In `_technical_levels`, replace the return dict's `"rsi14": _rsi(closes, 14),` with a computed value plus its zone:

```python
    rsi = _rsi(closes, 14)
    return {
        "last": last, "ma20": ma20, "ma50": ma50, "rsi14": rsi, "rsi_zone": rsi_zone(rsi),
        "pivot": pivot, "resistance_1": r1, "support_1": s1,
        "swing_high_60d": hi, "swing_low_60d": lo, "trend": trend,
    }
```

Also change the 60-day swing lines so they don't depend on a 60-close trim. The closes list becomes the full history in Task 2:

```python
    hi = max(closes[-60:])
    lo = min(closes[-60:])
```

These lines are unchanged; they already slice. Confirm nothing else in `_technical_levels` uses `len(closes)` as "60 days". The `ma50` guard `len(closes) >= 50` stays.

In `mwtc/report/gauges.py`, add `from .. import config` under the imports. Then in `directional_lean` replace:

```python
        if rsi > 70:
            p -= 3          # overbought caution
        elif rsi < 30:
            p += 3          # oversold bounce
```
with
```python
        if rsi >= config.RSI_OVERBOUGHT:
            p -= 3          # overbought caution
        elif rsi <= config.RSI_OVERSOLD:
            p += 3          # oversold bounce
```
and in `directional_bias` replace:
```python
        if rsi > 70:
            b -= 8          # overbought fade
        elif rsi < 30:
            b += 8          # oversold bounce
```
with
```python
        if rsi >= config.RSI_OVERBOUGHT:
            b -= 8          # overbought fade
        elif rsi <= config.RSI_OVERSOLD:
            b += 8          # oversold bounce
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `PY -m unittest mwtc.tests.test_rsi -v`, then the full suite.
Expected: PASS, and all 69 existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add mwtc/config.py mwtc/sources/macro.py mwtc/report/gauges.py mwtc/tests/test_rsi.py
git commit -m "MWTC: Wilder RSI with a code-assigned zone; one overbought/oversold threshold for text and dials

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `_quote` — true 52-week range and as-of stamps

**Files:**
- Modify: `mwtc/sources/macro.py:1-56` (imports, `_quote`, `_quote_group`)
- Modify: `mwtc/sources/sessions.py` (add `ET`, `fmt_time_et`, `fmt_stamp_et`; Task 6 adds the rest)
- Test: `mwtc/tests/test_quote.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `sessions.ET` (`ZoneInfo("America/New_York")`);
  - `sessions.fmt_time_et(t: datetime) -> str` (`"8:30 AM"`);
  - `sessions.fmt_stamp_et(t: datetime) -> str` (`"Wed 4:30 PM ET"`);
  - `macro._now_et() -> datetime`;
  - `macro._quote(ticker: str, kind: str = "index", now_et: Optional[datetime] = None) -> Optional[dict]`, where kind is one of `index|futures|fx|crypto|global`;
  - `macro._quote_group(mapping: dict, kind: str = "index", now_et=None) -> dict`.
- Quote dict keys:
  - always: `ticker`, `last`, `prev_close`, `change`, `pct_change`, `high_52w_close`, `low_52w_close`, `pct_from_52w_high`, `pct_from_52w_low`, `as_of_date` (ISO), `bar_complete` (bool), `closes` (full list);
  - optional: `range_note`, `as_of_et` (futures/fx/crypto), `day_basis` (crypto).
- `high_252` and `low_252` are removed (no code reads them).

- [ ] **Step 1: Write the failing test**

```python
# mwtc/tests/test_quote.py
import datetime as dt
import unittest
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from mwtc.sources import macro, sessions

ET = sessions.ET


def frame(values, end="2026-10-28", freq="B"):
    idx = pd.date_range(end=end, periods=len(values), freq=freq)
    return pd.DataFrame({"Close": values}, index=idx)


class FakeTicker:
    def __init__(self, df, info=None, meta=None):
        self._df, self.info, self.history_metadata = df, info or {}, meta or {}

    def history(self, period=None, interval=None):
        return self._df


def fake_yf(table):
    return SimpleNamespace(Ticker=lambda sym: table[sym])


AM = dt.datetime(2026, 10, 29, 8, 40, tzinfo=ET)   # Thursday pre-market


class Quote(unittest.TestCase):
    def q(self, df, kind="index", now=AM, **kw):
        with mock.patch.object(macro, "yf", fake_yf({"X": FakeTicker(df, **kw)})):
            return macro._quote("X", kind=kind, now_et=now)

    def test_52w_high_from_9_months_ago_is_found(self):
        vals = [100.0] * 260
        vals[30] = 150.0                        # ~322 calendar days back
        out = self.q(frame(vals))
        self.assertEqual(out["high_52w_close"], 150.0)
        self.assertNotIn("high_252", out)

    def test_52w_cut_excludes_older_than_365_days(self):
        vals = [100.0 + (i % 3) for i in range(300)]
        vals[5] = 500.0                          # ~412 days back: outside
        out = self.q(frame(vals))
        self.assertEqual(out["high_52w_close"], 102.0)

    def test_fx_five_day_week_uses_calendar_cut(self):
        vals = [1.10] * 270
        vals[5] = 0.90                           # outside 365 days
        vals[15] = 0.95                          # inside
        out = self.q(frame(vals), kind="fx")
        self.assertEqual(out["low_52w_close"], 0.95)

    def test_seven_day_series_uses_calendar_cut_not_252_rows(self):
        vals = [50000.0] * 400
        vals[100] = 90000.0                      # 299 days back: inside the cut
        out = self.q(frame(vals, freq="D"), kind="crypto")
        self.assertEqual(out["high_52w_close"], 90000.0)
        self.assertEqual(out["day_basis"], "UTC day (starts 8 PM ET)")

    def test_short_history_gets_range_note(self):
        out = self.q(frame([10.0 + i for i in range(100)]))
        self.assertEqual(out["range_note"], "less than 52 weeks of data")

    def test_pct_from_52w(self):
        vals = [100.0] * 259 + [90.0]
        vals[200] = 120.0
        out = self.q(frame(vals))
        self.assertEqual(out["pct_from_52w_high"], -25.0)
        self.assertEqual(out["pct_from_52w_low"], 0.0)

    def test_prior_session_bar_is_complete(self):
        out = self.q(frame([1.0] * 300, end="2026-10-28"))
        self.assertEqual(out["as_of_date"], "2026-10-28")
        self.assertTrue(out["bar_complete"])

    def test_index_bar_during_session_is_incomplete(self):
        noon = dt.datetime(2026, 10, 29, 12, 0, tzinfo=ET)
        out = self.q(frame([1.0] * 300, end="2026-10-29"), now=noon)
        self.assertFalse(out["bar_complete"])
        after = dt.datetime(2026, 10, 29, 16, 30, tzinfo=ET)
        self.assertTrue(self.q(frame([1.0] * 300, end="2026-10-29"), now=after)["bar_complete"])

    def test_24h_bar_today_is_incomplete_with_as_of_et(self):
        meta = {"regularMarketTime": int(dt.datetime(2026, 10, 29, 8, 35, tzinfo=ET).timestamp())}
        out = self.q(frame([1.1] * 300, end="2026-10-29"), kind="fx", meta=meta)
        self.assertFalse(out["bar_complete"])
        self.assertEqual(out["as_of_et"], "Thu 8:35 AM ET")

    def test_full_history_kept_for_technicals(self):
        out = self.q(frame([1.0] * 300))
        self.assertEqual(len(out["closes"]), 300)

    def test_stamp_formats(self):
        t = dt.datetime(2026, 10, 28, 16, 30, tzinfo=ET)
        self.assertEqual(sessions.fmt_time_et(t), "4:30 PM")
        self.assertEqual(sessions.fmt_stamp_et(t), "Wed 4:30 PM ET")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `PY -m unittest mwtc.tests.test_quote -v`
Expected: FAIL. `_quote()` gets an unexpected keyword `kind`, and `sessions.ET`/`fmt_time_et` are missing.

- [ ] **Step 3: Implement**

At the top of `mwtc/sources/sessions.py`, after `import logging`, add:

```python
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def fmt_time_et(t: dt.datetime) -> str:
    """'8:30 AM' — hour without a leading zero, no 'ET' suffix."""
    t = t.astimezone(ET)
    return f"{t.strftime('%I').lstrip('0') or '12'}:{t.strftime('%M %p')}"


def fmt_stamp_et(t: dt.datetime) -> str:
    """'Wed 4:30 PM ET' — for row stamps that need the day."""
    t = t.astimezone(ET)
    return f"{t.strftime('%a')} {fmt_time_et(t)} ET"
```

In `mwtc/sources/macro.py` replace `_quote` and `_quote_group` with:

```python
_KIND_24H = ("futures", "fx", "crypto")


def _now_et() -> dt.datetime:
    return dt.datetime.now(sessions.ET)


def _bar_complete(bar_date: dt.date, kind: str, now_et: dt.datetime) -> bool:
    """Is the last daily bar a finished session? A bar dated before today (ET)
    is. Today's bar: never for 24h markets, after 4 PM ET for US cash, after
    11:35 AM ET (Europe's close) for the global group."""
    if bar_date < now_et.date():
        return True
    if kind in _KIND_24H:
        return False
    if kind == "global":
        return now_et.time() >= dt.time(11, 35)
    return now_et.time() >= dt.time(16, 0)


def _range_52w(closes) -> tuple[float, float, Optional[str]]:
    """Closing high/low over the 365 calendar days ending at the last bar — a
    calendar cut, never a row count (crypto has 366 rows a year, FX ~260)."""
    last_date = closes.index[-1]
    cut = last_date - dt.timedelta(days=365)
    win = closes[closes.index >= cut]
    note = None if closes.index[0] <= cut else "less than 52 weeks of data"
    return float(win.max()), float(win.min()), note


def _as_of_et(tk, closes) -> Optional[str]:
    try:
        ts = (getattr(tk, "history_metadata", None) or {}).get("regularMarketTime")
        if ts:
            return sessions.fmt_stamp_et(dt.datetime.fromtimestamp(int(ts), sessions.ET))
    except Exception:  # noqa: BLE001
        pass
    return None


def _quote(ticker: str, kind: str = "index", now_et: Optional[dt.datetime] = None) -> Optional[dict]:
    """Last, prior close, change, TRUE 52-week closing range and as-of stamps for
    one ticker. 2 years of daily bars: yfinance's '1y' counts back from the
    request, so on a pre-market run it would miss the first days of the window.
    `kind` = index | futures | fx | crypto | global."""
    if yf is None:
        return None
    now_et = now_et or _now_et()
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="2y", interval="1d")
        if hist is None or hist.empty or len(hist) < 2:
            return None
        closes = hist["Close"].dropna()
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2])
        chg = last - prev
        pct = (chg / prev * 100) if prev else 0.0
        hi, lo, note = _range_52w(closes)
        bar_date = closes.index[-1].date()
        out = {
            "ticker": ticker,
            "last": round(last, 2),
            "prev_close": round(prev, 2),
            "change": round(chg, 2),
            "pct_change": round(pct, 2),
            "high_52w_close": round(hi, 2),
            "low_52w_close": round(lo, 2),
            "pct_from_52w_high": round((last / hi - 1) * 100, 2) if hi else None,
            "pct_from_52w_low": round((last / lo - 1) * 100, 2) if lo else None,
            "as_of_date": bar_date.isoformat(),
            "bar_complete": _bar_complete(bar_date, kind, now_et),
            "closes": [round(float(c), 2) for c in closes],
        }
        if note:
            out["range_note"] = note
        if kind in _KIND_24H:
            out["as_of_et"] = _as_of_et(tk, closes)
        if kind == "crypto":
            out["day_basis"] = "UTC day (starts 8 PM ET)"
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("quote failed for %s: %s", ticker, e)
        return None


def _quote_group(mapping: dict[str, str], kind: str = "index",
                 now_et: Optional[dt.datetime] = None) -> dict[str, Optional[dict]]:
    return {name: _quote(tk, kind=kind, now_et=now_et) for name, tk in mapping.items()}
```

Update `_strip_closes`'s docstring: "Drop the closes array (full history) from a quote group …".

In `collect()`, pass kinds. Task 7 adds `today/now_et`; for now, keep the signature and pass `kind`:

```python
    indices = _quote_group(config.INDICES, "index")
    ...
        "futures": _strip_closes(_quote_group(config.FUTURES, "futures")),
        "rates_vol": _strip_closes(_quote_group(config.RATES_VOL, "index")),
        "commodities": _strip_closes(_quote_group(config.COMMODITIES, "futures")),
        "fx": _strip_closes(_quote_group(config.FX, "fx")),
        "crypto": _strip_closes(_quote_group(config.CRYPTO, "crypto")),
        "indices": _strip_closes(indices),
        "global_indices": _strip_closes(_quote_group(config.GLOBAL_INDICES, "global")),
```

`sector_rotation` and the `etf_spots` call keep `_quote(tk)` with the default `"index"`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `PY -m unittest mwtc.tests.test_quote -v`, then the full suite.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mwtc/sources/macro.py mwtc/sources/sessions.py mwtc/tests/test_quote.py
git commit -m "MWTC: true 52-week closing range (calendar cut, 2y history) and as-of stamps on every quote

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Futures change vs the contract actually quoted

**Files:**
- Modify: `mwtc/sources/macro.py` (add `_futures_fields`; call it from `_quote` when `kind == "futures"`)
- Test: `mwtc/tests/test_futures_roll.py`

**Interfaces:**
- Consumes: `_quote(ticker, kind, now_et)` from Task 2, and `sessions.ET`.
- Produces: `macro._futures_fields(sym: str, tk, closes: pandas.Series, now_et: datetime) -> dict`.
- The returned dict overrides `prev_close`, `change`, `pct_change` and adds `contract`, plus optionally `rolled_today` and `roll_note`. For futures, `_quote` also sets `continuous_front_month: True` and nulls `pct_from_52w_*`.

- [ ] **Step 1: Write the failing test**

```python
# mwtc/tests/test_futures_roll.py
import datetime as dt
import unittest
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from mwtc.sources import macro, sessions

ET = sessions.ET
AM = dt.datetime(2026, 10, 29, 8, 40, tzinfo=ET)
EXP = int(dt.datetime(2026, 11, 25, tzinfo=ET).timestamp())


def frame(values, end="2026-10-29"):
    idx = pd.date_range(end=end, periods=len(values), freq="B")
    return pd.DataFrame({"Close": values}, index=idx)


class FakeTicker:
    def __init__(self, df=None, info=None, meta=None, info_raises=False):
        self._df, self._info, self.history_metadata = df, info or {}, meta or {}
        self._raise = info_raises

    @property
    def info(self):
        if self._raise:
            raise RuntimeError("quoteSummary throttled")
        return self._info

    def history(self, period=None, interval=None):
        return self._df


def run(table, sym="NG=F"):
    with mock.patch.object(macro, "yf", SimpleNamespace(Ticker=lambda s: table[s])):
        return macro._quote(sym, kind="futures", now_et=AM)


# 2026-10-28/29 natural gas: continuous NG=F rolled Nov -> Dec.
CONT = frame([2.5] * 297 + [2.697, 2.817, 2.95])
DEC = frame([3.004, 3.032, 2.95])
INFO = {"underlyingSymbol": "NGZ26.NYM", "expireDate": EXP}


class Roll(unittest.TestCase):
    def test_audited_case_uses_contract_prior_close(self):
        out = run({"NG=F": FakeTicker(CONT, INFO, {"previousClose": 3.032}),
                   "NGZ26.NYM": FakeTicker(DEC)})
        self.assertEqual(out["contract"], "NGZ26")
        self.assertEqual(out["last"], 2.95)                 # continuous last
        self.assertEqual(out["prev_close"], 3.03)
        self.assertAlmostEqual(out["pct_change"], -2.70, places=2)
        self.assertTrue(out["rolled_today"])
        self.assertIn("rolled to NGZ26", out["roll_note"])
        self.assertTrue(out["continuous_front_month"])
        self.assertIsNone(out["pct_from_52w_high"])

    def test_small_roll_gap_still_uses_contract(self):
        cont = frame([50.0] * 297 + [53.0, 53.457, 54.0])
        dec = frame([53.2, 53.666, 54.0])
        out = run({"SI=F": FakeTicker(cont, {"underlyingSymbol": "SIZ26.CMX", "expireDate": EXP}),
                   "SIZ26.CMX": FakeTicker(dec)}, sym="SI=F")
        self.assertEqual(out["prev_close"], 53.67)
        self.assertAlmostEqual(out["pct_change"], (54.0 / 53.666 - 1) * 100, places=2)

    def test_contract_last_off_by_1pct_withholds(self):
        out = run({"NG=F": FakeTicker(CONT, INFO),
                   "NGZ26.NYM": FakeTicker(frame([3.004, 3.032, 2.98]))})
        self.assertWithheld(out)

    def test_contract_bars_a_day_old_withhold(self):
        out = run({"NG=F": FakeTicker(CONT, INFO),
                   "NGZ26.NYM": FakeTicker(frame([3.004, 2.95], end="2026-10-28"))})
        self.assertWithheld(out)

    def test_expired_contract_withholds(self):
        old = {"underlyingSymbol": "NGX26.NYM",
               "expireDate": int(dt.datetime(2026, 10, 28, tzinfo=ET).timestamp())}
        out = run({"NG=F": FakeTicker(CONT, old), "NGX26.NYM": FakeTicker(frame([2.697, 2.817, 2.95]))})
        self.assertWithheld(out)

    def test_info_fails_uses_chart_metadata(self):
        out = run({"NG=F": FakeTicker(CONT, info_raises=True,
                                      meta={"previousClose": 3.032, "shortName": "Natural Gas Dec 26"})})
        self.assertAlmostEqual(out["pct_change"], -2.70, places=2)
        self.assertEqual(out["contract"], "Natural Gas Dec 26")

    def test_metadata_disagrees_with_contract_withholds(self):
        # Review Focus 1: chart metadata still on the old contract's settle.
        out = run({"NG=F": FakeTicker(CONT, INFO, {"previousClose": 2.817}),
                   "NGZ26.NYM": FakeTicker(DEC)})
        self.assertWithheld(out)

    def test_both_sources_fail_withholds(self):
        out = run({"NG=F": FakeTicker(CONT, info_raises=True)})
        self.assertWithheld(out)

    def test_non_roll_day_unchanged(self):
        cont = frame([60.0] * 297 + [61.0, 62.0, 63.0])
        out = run({"CL=F": FakeTicker(cont, {"underlyingSymbol": "CLZ26.NYM", "expireDate": EXP},
                                      {"previousClose": 62.0}),
                   "CLZ26.NYM": FakeTicker(frame([61.0, 62.0, 63.0]))}, sym="CL=F")
        self.assertAlmostEqual(out["pct_change"], (63 / 62 - 1) * 100, places=2)
        self.assertNotIn("rolled_today", out)
        self.assertNotIn("roll_note", out)

    def assertWithheld(self, out):
        self.assertEqual(out["last"], 2.95)
        for k in ("prev_close", "change", "pct_change"):
            self.assertIsNone(out[k], k)
        self.assertEqual(out["roll_note"], "contract roll — change withheld")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `PY -m unittest mwtc.tests.test_futures_roll -v`
Expected: FAIL. There is no `contract` key, and `pct_change` is the continuous +4.72%.

- [ ] **Step 3: Implement**

Add above `_quote` in `mwtc/sources/macro.py`:

```python
# Futures roll guards (spec §1).
ROLL_MATCH_TOL = 0.005    # contract last vs continuous last: is Yahoo quoting it?
META_TOL = 0.0025         # .info contract prior close vs chart-metadata previousClose
ROLL_INFO_TOL = 0.001     # continuous vs contract prior close: today is a roll day
_WITHHELD = {"prev_close": None, "change": None, "pct_change": None,
             "roll_note": "contract roll — change withheld"}


def _contract_prev(tk, closes, now_et) -> tuple[Optional[str], Optional[float]]:
    """(contract, prior close) from the contract Yahoo says it is quoting, or
    (None, None) when it can't be confirmed: lookup failed, contract expired,
    its last bar is not the continuous series' last bar, or its last price is
    more than 0.5% away (then Yahoo is quoting a different month)."""
    try:
        info = tk.info or {}
        und = info.get("underlyingSymbol")
        exp = info.get("expireDate")
        if not und:
            return None, None
        if exp and dt.datetime.fromtimestamp(int(exp), sessions.ET).date() < now_et.date():
            return None, None
        ch = yf.Ticker(und).history(period="5d", interval="1d")
        cc = ch["Close"].dropna() if ch is not None and not ch.empty else None
        if cc is None or len(cc) < 2:
            return None, None
        if cc.index[-1].date() != closes.index[-1].date():
            return None, None
        if abs(float(cc.iloc[-1]) / float(closes.iloc[-1]) - 1) > ROLL_MATCH_TOL:
            return None, None
        return und.split(".")[0], float(cc.iloc[-2])
    except Exception as e:  # noqa: BLE001 — throttled quoteSummary etc.
        log.warning("futures contract lookup failed (%s) — chart metadata fallback", type(e).__name__)
        return None, None


def _futures_fields(sym: str, tk, closes, now_et: dt.datetime) -> dict:
    """Change vs the prior close of the contract Yahoo is actually quoting —
    never vs the continuous series' prior close, which belongs to the expiring
    month on a roll day (NG=F 2026-10-29: +4.61% printed, -2.7% real)."""
    last = float(closes.iloc[-1])
    cont_prev = float(closes.iloc[-2])
    contract, c_prev = _contract_prev(tk, closes, now_et)
    meta = getattr(tk, "history_metadata", None) or {}
    m_prev = meta.get("previousClose")
    m_prev = float(m_prev) if isinstance(m_prev, (int, float)) and m_prev > 0 else None

    if c_prev is not None and m_prev is not None and abs(c_prev / m_prev - 1) > META_TOL:
        log.warning("%s: contract and chart prior close disagree — change withheld", sym)
        return dict(_WITHHELD)
    if c_prev is None and m_prev is not None:
        contract, c_prev = meta.get("shortName") or sym, m_prev
    if c_prev is None:
        return dict(_WITHHELD)

    chg = last - c_prev
    out = {"contract": contract, "prev_close": round(c_prev, 2), "change": round(chg, 2),
           "pct_change": round(chg / c_prev * 100, 2)}
    if abs(cont_prev / c_prev - 1) > ROLL_INFO_TOL:
        out["rolled_today"] = True
        out["roll_note"] = f"front month rolled to {contract}; change is vs {contract}'s prior close"
    return out
```

In `_quote`, before `return out`, add:

```python
        if kind == "futures":
            out.update(_futures_fields(ticker, tk, closes, now_et))
            out["continuous_front_month"] = True
            out["pct_from_52w_high"] = out["pct_from_52w_low"] = None
```

The warning messages contain only the symbol and the exception type. Futures prices are not paid-feed data, but keep logs lean anyway.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `PY -m unittest mwtc.tests.test_futures_roll -v`, then the full suite.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mwtc/sources/macro.py mwtc/tests/test_futures_roll.py
git commit -m "MWTC: futures change vs the contract Yahoo actually quotes; withhold when it can't be confirmed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Real Nasdaq-100 for the NDX dials and card

**Files:**
- Modify: `mwtc/config.py:89-94` (`INDICES`)
- Modify: `mwtc/report/gauges.py:133`, `:262` (keys `"Nasdaq Composite"` → `"Nasdaq 100"`)
- Modify: `mwtc/publish.py:145` (`derive_sidecar` keys)
- Test: `mwtc/tests/test_ndx_keys.py`

**Interfaces:**
- Produces: `config.INDICES` = `{"S&P 500": "^GSPC", "Nasdaq 100": "^NDX", "Nasdaq Composite": "^IXIC", "Dow Jones": "^DJI", "Russell 2000": "^RUT"}`. The NDX dials and the hub card read `technicals["Nasdaq 100"]`.

- [ ] **Step 1: Write the failing test**

```python
# mwtc/tests/test_ndx_keys.py
import inspect
import re
import unittest

from mwtc import config, publish
from mwtc.report import gauges

UP = {"trend": "up", "rsi14": 60.0, "last": 110.0, "ma20": 100.0, "ma50": 95.0}


class NdxKeys(unittest.TestCase):
    def test_ndx_is_the_nasdaq_100(self):
        self.assertEqual(config.INDICES["Nasdaq 100"], "^NDX")
        self.assertEqual(config.INDICES["Nasdaq Composite"], "^IXIC")

    def test_every_technicals_key_read_by_dials_and_card_exists(self):
        src = inspect.getsource(gauges.render_dashboard) + inspect.getsource(
            gauges.render_index_bias_dials) + inspect.getsource(publish.derive_sidecar)
        for key in set(re.findall(r'"(S&P 500|Nasdaq [A-Za-z0-9 ]+|Dow Jones|Russell 2000)"', src)):
            self.assertIn(key, config.INDICES, key)
        self.assertNotIn('"Nasdaq Composite"', src)

    def test_ndx_dial_reads_nasdaq_100_row(self):
        packet = {"macro": {"technicals": {"Nasdaq 100": UP}}}
        html = gauges.render_index_bias_dials(packet)
        # NDX card is the second; with only that row present it must not be 0.
        vals = re.findall(r'class="dial-val"[^>]*>([+-]?\d+)<', html)
        self.assertNotEqual(vals[1], "0")
        side = publish.derive_sidecar(packet, "premarket")
        self.assertNotEqual(side["metric"]["value"], 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `PY -m unittest mwtc.tests.test_ndx_keys -v`
Expected: FAIL with `KeyError: 'Nasdaq 100'`.

- [ ] **Step 3: Implement**

`mwtc/config.py`:

```python
# Cash indices (for the technical picture). "Nasdaq 100" (^NDX) drives the NDX
# dials and hub card; the Composite stays as its own, clearly named row.
INDICES = {
    "S&P 500": "^GSPC",
    "Nasdaq 100": "^NDX",
    "Nasdaq Composite": "^IXIC",
    "Dow Jones": "^DJI",
    "Russell 2000": "^RUT",
}
```

In `mwtc/report/gauges.py`, `render_dashboard`: change `("Nasdaq 100", "NDX", "Nasdaq Composite", "QQQ"),` to `("Nasdaq 100", "NDX", "Nasdaq 100", "QQQ"),`. In `render_index_bias_dials`: change `("Nasdaq 100", "NDX", "Nasdaq Composite"),` to `("Nasdaq 100", "NDX", "Nasdaq 100"),`.

In `mwtc/publish.py` `derive_sidecar`: change `keys = ["S&P 500", "Nasdaq Composite", "Dow Jones", "Russell 2000"]` to `keys = ["S&P 500", "Nasdaq 100", "Dow Jones", "Russell 2000"]`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `PY -m unittest mwtc.tests.test_ndx_keys -v`, then the full suite.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mwtc/config.py mwtc/report/gauges.py mwtc/publish.py mwtc/tests/test_ndx_keys.py
git commit -m "MWTC: NDX dials and hub card read the real Nasdaq-100 (^NDX); Composite kept as its own row

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Code-ranked sectors

**Files:**
- Modify: `mwtc/sources/macro.py:109-123` (`sector_rotation`; add `rank_sectors`)
- Test: `mwtc/tests/test_sector_ranks.py`

**Interfaces:**
- Consumes: `_quote` (Task 2), which provides `as_of_date`.
- Produces:
  - `macro.rank_sectors(rows: dict[str, Optional[dict]]) -> dict`. It mutates each row, adding `rank`, `rank_1mo` and `rank_1d`, and returns the summary `{"order_by_rank": [...names], "top3_1mo": [...], "bottom3_1mo": [...], "leaders_1d": [...], "laggards_1d": [...], "as_of_date": "YYYY-MM-DD" | None}`.
  - Each `sector_rotation()` row gains `as_of_date`.
  - The summary lives at a NEW sibling packet key, `macro["sector_ranking"]`. The `sector_rotation` dict stays name→row, so nothing that iterates over it changes.

- [ ] **Step 1: Write the failing test**

```python
# mwtc/tests/test_sector_ranks.py
import unittest

from mwtc.sources import macro

# 1-month and 1-day returns to the 2026-10-28 close (audit, yfinance).
ROWS = {
    "Energy": (11.30, 0.10), "Consumer Staples": (7.74, -0.20), "Utilities": (7.73, -0.84),
    "Health Care": (6.71, 0.30), "Materials": (6.49, 0.05), "Consumer Discretionary": (5.96, 0.40),
    "Financials": (5.11, 0.20), "Real Estate": (2.98, -0.73), "Industrials": (0.90, 0.15),
    "Technology": (-2.43, 0.80), "Communication Services": (-7.06, -0.42),
}


def rows():
    return {n: {"etf": n[:3], "pct_change_1mo": m, "pct_change_1d": d, "as_of_date": "2026-10-28"}
            for n, (m, d) in ROWS.items()}


class SectorRanks(unittest.TestCase):
    def test_order_follows_1_month_then_1_day(self):
        r = rows()
        s = macro.rank_sectors(r)
        self.assertEqual(s["order_by_rank"], list(ROWS))
        self.assertEqual(s["top3_1mo"], ["Energy", "Consumer Staples", "Utilities"])
        self.assertEqual(s["bottom3_1mo"], ["Industrials", "Technology", "Communication Services"])
        self.assertEqual(r["Financials"]["rank"], 7)
        self.assertEqual(s["as_of_date"], "2026-10-28")

    def test_1_day_leaders_and_laggards(self):
        s = macro.rank_sectors(rows())
        self.assertEqual(s["leaders_1d"], ["Technology", "Consumer Discretionary", "Health Care"])
        self.assertEqual(s["laggards_1d"], ["Utilities", "Real Estate", "Communication Services"])  # worst first

    def test_tie_on_1_month_broken_by_1_day(self):
        r = {"A": {"pct_change_1mo": 5.0, "pct_change_1d": -1.0},
             "B": {"pct_change_1mo": 5.0, "pct_change_1d": 2.0}}
        self.assertEqual(macro.rank_sectors(r)["order_by_rank"], ["B", "A"])

    def test_missing_rows_skipped(self):
        r = rows()
        r["Energy"] = None
        r["Utilities"]["pct_change_1mo"] = None
        s = macro.rank_sectors(r)
        self.assertNotIn("Energy", s["order_by_rank"])
        self.assertNotIn("Utilities", s["order_by_rank"])
        self.assertEqual(len(s["order_by_rank"]), 9)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `PY -m unittest mwtc.tests.test_sector_ranks -v`
Expected: FAIL with `AttributeError: rank_sectors`.

- [ ] **Step 3: Implement**

In `mwtc/sources/macro.py`, change `sector_rotation` so each row carries `as_of_date`, and add `rank_sectors`:

```python
def sector_rotation() -> dict:
    """11 S&P sectors: 1-day and ~1-month (20-session) % change, as of the last bar."""
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
                     "pct_change_1mo": mo, "last": q["last"], "as_of_date": q.get("as_of_date")}
    return out


def rank_sectors(rows: dict) -> dict:
    """The ONE sector ranking the report may print: 1-month return first, 1-day
    as tiebreak (the rule the report states). On 2026-10-29 the AI ranked
    Financials among the 'best' (7th of 11) and XLU among the 'weakest' (3rd)."""
    valid = {n: r for n, r in rows.items()
             if isinstance(r, dict) and r.get("pct_change_1mo") is not None}
    order = sorted(valid, key=lambda n: (-valid[n]["pct_change_1mo"],
                                         -(valid[n].get("pct_change_1d") or 0.0)))
    by_1d = sorted((n for n in valid if valid[n].get("pct_change_1d") is not None),
                   key=lambda n: -valid[n]["pct_change_1d"])
    for i, n in enumerate(order, 1):
        valid[n]["rank"] = valid[n]["rank_1mo"] = i
    for i, n in enumerate(by_1d, 1):
        valid[n]["rank_1d"] = i
    dates = [r.get("as_of_date") for r in valid.values() if r.get("as_of_date")]
    return {
        "order_by_rank": order,
        "top3_1mo": order[:3],
        "bottom3_1mo": order[-3:],
        "leaders_1d": by_1d[:3],
        "laggards_1d": list(reversed(by_1d[-3:])),
        "as_of_date": max(dates) if dates else None,
    }
```

In `collect()`, compute once and add the sibling key:

```python
    sectors = sector_rotation()
    ...
        "sector_rotation": sectors,
        "sector_ranking": rank_sectors(sectors),
```

Replace the existing `"sector_rotation": sector_rotation(),` line with these two lines, and add `sectors = sector_rotation()` above the `return`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `PY -m unittest mwtc.tests.test_sector_ranks -v`, then the full suite.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mwtc/sources/macro.py mwtc/tests/test_sector_ranks.py
git commit -m "MWTC: code-ranked sectors (1-month, 1-day tiebreak) with leaders/laggards lists

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: ET time helpers — parser, next session, calendar rows

**Files:**
- Modify: `mwtc/sources/sessions.py` (append)
- Test: `mwtc/tests/test_et_times.py`

**Interfaces:**
- Consumes: `sessions.ET`, `fmt_time_et`, `fmt_stamp_et` (Task 2).
- Produces:
  - `sessions.next_trading_day(d: date) -> date`.
  - `sessions.parse_utc(v) -> Optional[datetime]`. It accepts `'YYYY-MM-DD HH:MM:SS'`, ISO `T`, `Z`, `±HH:MM`, fractional seconds, epoch seconds or milliseconds (number or digit string), and a bare date. A bare date returns None, because it carries no time; callers detect it with `is_untimed`.
  - `sessions.is_untimed(v) -> bool`.
  - `sessions.econ_rows_et(rows, now_et: datetime) -> Optional[list[dict]]`.
  - `sessions.add_et_twins(rows, src_fields: tuple[str, ...], dst: str) -> int`. It mutates a list of dicts, pops every `src_fields` key, sets `dst` to `fmt_stamp_et(...)` or `"time unconverted"`, and returns the number converted.

- [ ] **Step 1: Write the failing test**

```python
# mwtc/tests/test_et_times.py
import datetime as dt
import os
import time
import unittest

from mwtc.sources import sessions

ET = sessions.ET
THU = dt.datetime(2026, 10, 29, 9, 0, tzinfo=ET)
FRI_WRAP = dt.datetime(2026, 10, 30, 16, 30, tzinfo=ET)


def row(ts, **kw):
    return {"date": ts, "event": "X", **kw}


class EtTimes(unittest.TestCase):
    def conv(self, ts, now=THU):
        return sessions.econ_rows_et([row(ts)], now)[0]

    def test_audited_utc_times(self):
        self.assertEqual(self.conv("2026-10-29 12:30:00")["time_et"], "8:30 AM")
        self.assertEqual(self.conv("2026-10-29 14:30:00")["time_et"], "10:30 AM")   # EIA
        self.assertEqual(self.conv("2026-10-29 17:00:00")["time_et"], "1:00 PM")    # 7-yr
        self.assertEqual(self.conv("2026-10-29 18:00:00")["time_et"], "2:00 PM")    # FOMC

    def test_friday_row_is_not_today(self):
        r = self.conv("2026-10-30 16:30:00")                                       # Musalem
        self.assertFalse(r["is_today_et"])
        self.assertTrue(r["is_next_session_et"])
        self.assertEqual(r["day_et"], "Fri 10/30")
        self.assertEqual(r["time_et"], "12:30 PM")

    def test_after_dst_ends(self):
        self.assertEqual(self.conv("2026-11-05 13:30:00")["time_et"], "8:30 AM")

    def test_untimed_row_keeps_its_date(self):
        r = self.conv("2026-10-29 00:00:00")
        self.assertIsNone(r["time_et"])
        self.assertEqual(r["time_note"], "time not scheduled")
        self.assertEqual(r["date_et"], "2026-10-29")
        self.assertTrue(r["is_today_et"])
        self.assertFalse(self.conv("2026-10-30")["is_today_et"])

    def test_raw_timestamp_dropped(self):
        r = self.conv("2026-10-29 12:30:00")
        self.assertNotIn("date", r)
        self.assertEqual(r["date_et"], "2026-10-29")

    def test_finnhub_shape(self):
        r = sessions.econ_rows_et([{"time": "2026-10-29 14:30:00", "event": "EIA Natural Gas"}], THU)[0]
        self.assertEqual(r["time_et"], "10:30 AM")
        self.assertNotIn("time", r)

    def test_unparsable_and_empty(self):
        r = self.conv("not a time")
        self.assertEqual(r["time_et"], "time unconverted")
        self.assertFalse(r["is_today_et"])
        self.assertEqual(sessions.econ_rows_et([], THU), [])
        self.assertIsNone(sessions.econ_rows_et({"error": "plan"}, THU))
        self.assertIsNone(sessions.econ_rows_et(None, THU))

    def test_friday_next_session_is_monday_across_dst(self):
        mon = self.conv("2026-11-02 15:00:00", now=FRI_WRAP)
        sat = self.conv("2026-10-31 15:00:00", now=FRI_WRAP)
        self.assertTrue(mon["is_next_session_et"])
        self.assertEqual(mon["time_et"], "10:00 AM")          # EST after Nov 1
        self.assertFalse(sat["is_next_session_et"])
        self.assertEqual(sessions.next_trading_day(dt.date(2026, 10, 30)), dt.date(2026, 11, 2))

    def test_parse_utc_shapes(self):
        want = dt.datetime(2026, 10, 28, 20, 30, tzinfo=dt.timezone.utc)
        for v in ("2026-10-28T20:30:00Z", "2026-10-28T20:30:00.12345Z",
                  "2026-10-28T20:30:00+00:00", "2026-10-28 20:30:00",
                  int(want.timestamp()), int(want.timestamp() * 1000), str(int(want.timestamp()))):
            self.assertEqual(sessions.parse_utc(v), want, v)
        self.assertEqual(sessions.parse_utc("2026-10-28T16:30:00-04:00"), want)
        self.assertIsNone(sessions.parse_utc("2026-10-28"))
        self.assertIsNone(sessions.parse_utc(None))

    def test_add_et_twins(self):
        rows = [{"executed_at": "2026-10-28T20:30:00Z", "size": 1},
                {"executed_at": "garbage"}, "not a dict"]
        n = sessions.add_et_twins(rows, ("executed_at",), "executed_et")
        self.assertEqual(n, 1)
        self.assertEqual(rows[0]["executed_et"], "Wed 4:30 PM ET")
        self.assertNotIn("executed_at", rows[0])
        self.assertEqual(rows[1]["executed_et"], "time unconverted")
        self.assertEqual(sessions.add_et_twins(None, ("x",), "y"), 0)

    def test_independent_of_machine_timezone(self):
        old = os.environ.get("TZ")
        os.environ["TZ"] = "America/Chicago"
        time.tzset()
        try:
            self.assertEqual(self.conv("2026-10-29 12:30:00")["time_et"], "8:30 AM")
        finally:
            if old is None:
                os.environ.pop("TZ")
            else:
                os.environ["TZ"] = old
            time.tzset()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `PY -m unittest mwtc.tests.test_et_times -v`
Expected: FAIL with `AttributeError: econ_rows_et`.

- [ ] **Step 3: Implement**

Append to `mwtc/sources/sessions.py`:

```python
import re
from typing import Optional

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?"
                    r"\s*(Z|[+-]\d{2}:?\d{2})?$")


def next_trading_day(d: dt.date) -> dt.date:
    """The NYSE session after `d` (weekends/holidays skipped); weekday fallback."""
    try:
        days = _nyse_sessions(d + dt.timedelta(days=1), d + dt.timedelta(days=14))
        if days:
            return days[0]
    except Exception as e:  # noqa: BLE001
        log.warning("NYSE calendar unavailable (%s) — weekday fallback", e)
    n = d + dt.timedelta(days=1)
    while n.weekday() >= 5:
        n += dt.timedelta(days=1)
    return n


def is_untimed(v) -> bool:
    """A bare date, or a 00:00:00 placeholder: the feed gave no time of day."""
    if not isinstance(v, str):
        return False
    s = v.strip()
    return bool(_DATE_ONLY.match(s)) or bool(re.match(r"^\d{4}-\d{2}-\d{2}[T ]00:00(:00)?$", s))


def parse_utc(v) -> Optional[dt.datetime]:
    """Feed timestamp -> aware UTC datetime. Never datetime.fromisoformat (the
    local venv is Python 3.9: no 'Z', no odd fractions). Naive strings are UTC
    (FMP/Finnhub calendars). Epoch seconds or milliseconds accepted. A bare
    date has no time of day -> None (see is_untimed)."""
    try:
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit()):
            x = float(v)
            if x > 1e12:
                x /= 1000.0
            return dt.datetime.fromtimestamp(x, dt.timezone.utc)
        if not isinstance(v, str):
            return None
        m = _STAMP.match(v.strip())
        if not m:
            return None
        y, mo, d, h, mi, s, off = m.groups()
        t = dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), tzinfo=dt.timezone.utc)
        if off and off != "Z":
            sign = 1 if off[0] == "+" else -1
            hh, mm = int(off[1:3]), int(off[-2:])
            t -= sign * dt.timedelta(hours=hh, minutes=mm)
        return t
    except (ValueError, OverflowError, OSError):
        return None


def econ_rows_et(rows, now_et: dt.datetime) -> Optional[list]:
    """Economic-calendar rows (FMP 'date' / Finnhub 'time', UTC) -> ET fields:
    time_et ('10:30 AM' or None when untimed), date_et, day_et ('Fri 10/30'),
    is_today_et, is_next_session_et. The raw timestamp is dropped so the AI
    can't print it (2026-10-28: FOMC 18:00 UTC printed as '6:00 PM ET').
    Logs counts only — never row values."""
    if not isinstance(rows, list):
        return None
    today = now_et.astimezone(ET).date()
    nxt = next_trading_day(today)
    out, unparsed = [], 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        raw = r.get("date") if r.get("date") is not None else r.get("time")
        row = {k: v for k, v in r.items() if k not in ("date", "time")}
        if is_untimed(raw):
            d = dt.date(int(raw[:4]), int(raw[5:7]), int(raw[8:10]))
            row.update(time_et=None, time_note="time not scheduled")
        else:
            t = parse_utc(raw)
            if t is None:
                unparsed += 1
                row.update(time_et="time unconverted", date_et=None, day_et=None,
                           is_today_et=False, is_next_session_et=False)
                out.append(row)
                continue
            te = t.astimezone(ET)
            d = te.date()
            row["time_et"] = fmt_time_et(te)
        row.update(date_et=d.isoformat(), day_et=f"{d.strftime('%a')} {d.month}/{d.day}",
                   is_today_et=(d == today), is_next_session_et=(d == nxt))
        out.append(row)
    if unparsed:
        log.info("economic calendar: %d of %d rows had an unparsable time", unparsed, len(rows))
    return out


def add_et_twins(rows, src_fields: tuple, dst: str) -> int:
    """For each dict row: pop every src field, set dst = 'Wed 4:30 PM ET' (or
    'time unconverted'). The raw UTC value leaves the packet so the AI can only
    print the ET one. Non-lists are left alone. Returns rows converted."""
    if not isinstance(rows, list):
        return 0
    n = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        raw = None
        for f in src_fields:
            if f in r:
                v = r.pop(f)
                raw = v if raw is None else raw
        if raw is None:
            continue
        t = parse_utc(raw)
        r[dst] = fmt_stamp_et(t) if t else "time unconverted"
        n += t is not None
    return n
```

Move `import re` and `from typing import Optional` up to the module's import block, with the existing imports.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `PY -m unittest mwtc.tests.test_et_times -v`, then the full suite.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mwtc/sources/sessions.py mwtc/tests/test_et_times.py
git commit -m "MWTC: one UTC->ET parser, next trading day, and ET economic-calendar rows (untimed rows keep their date)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Thread the ET date and convert every packet timestamp

**Files:**
- Modify: `mwtc/sources/macro.py` (`economic_calendar(today)`, `economic_news` `when_et`, `collect(today, now_et)`)
- Modify: `mwtc/sources/fmp.py:114-124` (`economic_calendar(days, today)`), `:298-308` (`collect(today)`)
- Modify: `mwtc/checks.py:31-36` (`_fmp_econ` ET date)
- Modify: `mwtc/main.py:90-181` (`collect_data(mode, now_et=None)`)
- Test: `mwtc/tests/test_collect_et.py`

**Interfaces:**
- Consumes: `sessions.econ_rows_et` and `sessions.add_et_twins` (Task 6), `_quote_group(..., now_et)` (Task 2).
- Produces:
  - `macro.collect(today: Optional[date] = None, now_et: Optional[datetime] = None)`;
  - `fmp.collect(today: Optional[date] = None)`;
  - `macro.economic_calendar(today: Optional[date] = None)`;
  - `fmp.economic_calendar(days: int = 7, today: Optional[date] = None)`;
  - `main.collect_data(mode: str = "premarket", now_et: Optional[datetime] = None)`.
- Packet changes:
  - `macro.economic_calendar` holds ET rows;
  - new `macro.economic_calendar_today_count: int | None`;
  - UW rows carry `executed_et`, `created_et`, `when_et` or `time_et`, with the raw fields removed;
  - `macro.economic_news[*].when_et` replaces `datetime`.

- [ ] **Step 1: Write the failing test**

```python
# mwtc/tests/test_collect_et.py
import datetime as dt
import json
import unittest
from unittest import mock

from mwtc import main
from mwtc.sources import sessions

ET = sessions.ET
LATE = dt.datetime(2026, 10, 29, 21, 0, tzinfo=ET)     # 01:00 UTC on 10/30
AM = dt.datetime(2026, 10, 29, 8, 40, tzinfo=ET)


def uw_packet():
    # Shapes only — no real Unusual Whales values.
    return {"available": True,
            "dark_pool_recent": [{"executed_at": "2026-10-28T20:30:00Z", "ticker": "AAA"}],
            "flow_alerts_market": [{"created_at": "2026-10-28T19:00:00Z"}],
            "news_headlines": [{"created_at": "2026-10-29T11:00:00Z", "headline": "h"}],
            "market_tide": [{"timestamp": "2026-10-28T19:55:00Z"}],
            "options_intel": {"iv_rank": []},
            "per_ticker": {"SPY": {"dark_pool": [{"executed_at": "2026-10-28T20:00:00Z"}],
                                   "flow_alerts": [{"start_time": 1793217600000}]}}}


class CollectEt(unittest.TestCase):
    def run_collect(self, mode="premarket", now=AM, econ=None):
        macro_p = {"earnings_calendar": [], "etf_spots": {}, "economic_calendar": None,
                   "sector_ranking": {"as_of_date": "2026-10-28"}, "sector_rotation": {}}
        fmp_p = {"available": True, "gainers": [], "losers": [], "earnings_calendar": [],
                 "analyst_actions": [], "insider": [], "breadth": None,
                 "economic_calendar": econ}
        self.mc = mock.MagicMock(return_value=macro_p)
        self.fc = mock.MagicMock(return_value=fmp_p)
        with mock.patch.object(main.macro, "collect", self.mc), \
             mock.patch.object(main.macro, "sector_breadth", return_value={"count": 11}), \
             mock.patch.object(main.unusual_whales, "collect", return_value=uw_packet()), \
             mock.patch.object(main.unusual_whales, "earnings", return_value=None), \
             mock.patch.object(main.fmp, "collect", self.fc), \
             mock.patch.object(main.fmp, "index_universe", return_value=[]), \
             mock.patch.object(main.sentiment, "collect", return_value={}), \
             mock.patch.object(main.fedwatch, "probabilities", return_value=None), \
             mock.patch.object(main.kalshi, "fed_odds", return_value=None), \
             mock.patch.object(main.premarket, "movers", return_value={"gainers": [], "losers": []}), \
             mock.patch.object(main.premarket, "reactions", return_value={}):
            return main.collect_data(mode=mode, now_et=now)

    def test_late_run_keeps_et_today(self):
        self.run_collect("postmarket", now=LATE)
        self.assertEqual(self.mc.call_args.kwargs["today"], dt.date(2026, 10, 29))
        self.assertEqual(self.mc.call_args.kwargs["now_et"], LATE)
        self.assertEqual(self.fc.call_args.kwargs["today"], dt.date(2026, 10, 29))

    def test_econ_rows_converted_once_with_today_count(self):
        econ = [{"date": "2026-10-29 12:30:00", "event": "GDP"},
                {"date": "2026-10-30 16:30:00", "event": "Musalem"}]
        d = self.run_collect(econ=econ)
        rows = d["macro"]["economic_calendar"]
        self.assertEqual([r["time_et"] for r in rows], ["8:30 AM", "12:30 PM"])
        self.assertEqual(d["macro"]["economic_calendar_today_count"], 1)

    def test_uw_timestamps_become_et_only(self):
        d = self.run_collect()
        inst = d["institutional"]
        self.assertEqual(inst["dark_pool_recent"][0]["executed_et"], "Wed 4:30 PM ET")
        self.assertEqual(inst["flow_alerts_market"][0]["created_et"], "Wed 3:00 PM ET")
        self.assertEqual(inst["news_headlines"][0]["when_et"], "Thu 7:00 AM ET")
        self.assertEqual(inst["market_tide"][0]["time_et"], "Wed 3:55 PM ET")
        self.assertIn("executed_et", inst["per_ticker"]["SPY"]["dark_pool"][0])
        self.assertIn("created_et", inst["per_ticker"]["SPY"]["flow_alerts"][0])
        blob = json.dumps(inst)
        for raw in ("executed_at", "created_at", "start_time", '"timestamp"'):
            self.assertNotIn(raw, blob)


if __name__ == "__main__":
    unittest.main()
```

Also add this check of `economic_news` to the same file:

```python
class EconNews(unittest.TestCase):
    def test_epoch_becomes_when_et(self):
        from mwtc.sources import macro
        item = {"headline": "GDP rises 3.4%", "summary": "", "source": "s", "url": "u",
                "datetime": int(dt.datetime(2026, 10, 29, 8, 31, tzinfo=ET).timestamp())}
        resp = mock.Mock(status_code=200)
        resp.json.return_value = [item]
        resp.raise_for_status.return_value = None
        with mock.patch.object(macro.config, "FINNHUB_API_KEY", "k"), \
             mock.patch.object(macro.requests, "get", return_value=resp), \
             mock.patch("time.time", return_value=item["datetime"] + 60):
            out = macro.economic_news()
        self.assertEqual(out[0]["when_et"], "Thu 8:31 AM ET")
        self.assertNotIn("datetime", out[0])
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `PY -m unittest mwtc.tests.test_collect_et -v`
Expected: FAIL. `collect_data()` gets an unexpected keyword `now_et`, and `economic_news` keeps `datetime`.

- [ ] **Step 3: Implement**

`mwtc/sources/macro.py`, `economic_calendar`:

```python
def economic_calendar(today: Optional[dt.date] = None) -> Optional[list]:
    """US economic events for the next 7 days (Finnhub, optional). `today` is
    the ET date from main — never the runner's UTC clock."""
    today = today or _now_et().date()
```

(Keep the rest of the function as is.)

In `economic_news`, replace the `"datetime": it.get("datetime"),` entry with:

```python
                "when_et": (sessions.fmt_stamp_et(dt.datetime.fromtimestamp(it["datetime"], sessions.ET))
                            if isinstance(it.get("datetime"), (int, float)) else None),
```

In `collect`, change the signature and thread the values:

```python
def collect(today: Optional[dt.date] = None, now_et: Optional[dt.datetime] = None) -> dict:
    """Assemble the full free-macro packet. `today`/`now_et` come from main
    (ET), so a run after UTC midnight can't shift 'today'."""
    now_et = now_et or _now_et()
    today = today or now_et.date()
    indices = _quote_group(config.INDICES, "index", now_et)
    technicals = {name: _technical_levels(q) for name, q in indices.items()}
    etf_spots = {t: _quote(t, now_et=now_et) for t in config.UW_FOCUS_TICKERS}
    sectors = sector_rotation()
    return {
        "futures": _strip_closes(_quote_group(config.FUTURES, "futures", now_et)),
        "rates_vol": _strip_closes(_quote_group(config.RATES_VOL, "index", now_et)),
        "commodities": _strip_closes(_quote_group(config.COMMODITIES, "futures", now_et)),
        "fx": _strip_closes(_quote_group(config.FX, "fx", now_et)),
        "crypto": _strip_closes(_quote_group(config.CRYPTO, "crypto", now_et)),
        "indices": _strip_closes(indices),
        "global_indices": _strip_closes(_quote_group(config.GLOBAL_INDICES, "global", now_et)),
        "sector_rotation": sectors,
        "sector_ranking": rank_sectors(sectors),
        "technicals": technicals,
        "etf_spots": {t: (q or {}).get("last") for t, q in etf_spots.items()},
        "economic_calendar": economic_calendar(today),
        "earnings_calendar": earnings_calendar(today),
        "economic_news": economic_news(),
        "market_news": market_news(),
    }
```

`technicals` now also includes a `"Nasdaq Composite"` row; that is expected.

`mwtc/sources/fmp.py`:

```python
def economic_calendar(days: int = 7, today: Optional[dt.date] = None) -> Optional[list]:
    """US economic events (UTC 'date' strings; main converts them to ET).
    `today` is the ET date from main."""
    today = today or dt.date.today()
    rows = _get("/economic-calendar",
                {"from": today.isoformat(),
                 "to": (today + dt.timedelta(days=days)).isoformat()})
    if not isinstance(rows, list):
        return None
    us = [r for r in rows if str(r.get("country", "")).upper() in ("US", "USA")]
    return us or rows
```

```python
def collect(today: Optional[dt.date] = None) -> dict:
    return {
        "available": bool(config.FMP_API_KEY),
        "gainers": gainers(),
        "losers": losers(),
        "analyst_actions": analyst_actions(),
        "breadth": market_breadth(),
        "economic_calendar": economic_calendar(today=today),
        "earnings_calendar": earnings_calendar(today=today),
        "insider": insider_trading(),
    }
```

`mwtc/checks.py` `_fmp_econ`: replace `t = dt.date.today()` with:

```python
    from zoneinfo import ZoneInfo
    t = dt.datetime.now(ZoneInfo("America/New_York")).date()
```

`mwtc/main.py`:
- add `from .sources import sessions` (extend the existing `from .sources import …` line with `sessions`);
- add a helper above `collect_data`;
- change `collect_data`.

```python
# UW row lists whose timestamps the report prints -> (raw fields, ET field).
_UW_TIMES = (("dark_pool_recent", ("executed_at",), "executed_et"),
             ("flow_alerts_market", ("created_at", "start_time"), "created_et"),
             ("news_headlines", ("created_at",), "when_et"),
             ("market_tide", ("timestamp", "tape_time", "time", "date"), "time_et"))
_UW_TICKER_TIMES = (("dark_pool", ("executed_at",), "executed_et"),
                    ("flow_alerts", ("created_at", "start_time"), "created_et"))


def _uw_times_et(uw_packet: dict) -> None:
    """Replace every UW row timestamp the AI can see with an ET twin, in the
    in-memory packet only (2026-10-28: a 20:30Z print shown as '~8:30 PM ET').
    Logs nothing about rows."""
    for key, src, dst in _UW_TIMES:
        sessions.add_et_twins(uw_packet.get(key), src, dst)
    per = uw_packet.get("per_ticker")
    if isinstance(per, dict):
        for block in per.values():
            if isinstance(block, dict):
                for key, src, dst in _UW_TICKER_TIMES:
                    sessions.add_et_twins(block.get(key), src, dst)
```

In `collect_data`, change the signature and the start of the function body:

```python
def collect_data(mode: str = "premarket", now_et: Optional[dt.datetime] = None) -> dict:
    report_dt = now_et or dt.datetime.now(ET)
    today = report_dt.date()
    log.info("Collecting macro data (yfinance, free)...")
    macro_packet = macro.collect(today=today, now_et=report_dt)
    log.info("Collecting Unusual Whales institutional data (REST)...")
    uw_packet = unusual_whales.collect(config.UW_FOCUS_TICKERS,
                                       etf_spots=macro_packet.get("etf_spots"))
    _uw_times_et(uw_packet)
    log.info("Collecting FMP (movers, analyst actions, breadth)...")
    fmp_packet = fmp.collect(today=today)
```

Then replace the economic-calendar block with:

```python
    # Economic calendar: prefer FMP, Finnhub fallback — then ONE conversion to
    # ET (time_et/date_et/is_today_et/is_next_session_et), never twice.
    econ = sessions.econ_rows_et(
        fmp_packet.get("economic_calendar") or macro_packet.get("economic_calendar"), report_dt)
    macro_packet["economic_calendar"] = econ
    macro_packet["economic_calendar_today_count"] = (
        sum(1 for r in econ if r.get("is_today_et")) if isinstance(econ, list) else None)
```

Existing `test_collect_wiring.py` mocks `macro.collect` and `fmp.collect` with `return_value`, so the new keyword arguments are accepted. Its fake packets lack `sector_ranking`; Task 8 reads that key with `.get`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `PY -m unittest mwtc.tests.test_collect_et -v`, then the full suite.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mwtc/sources/macro.py mwtc/sources/fmp.py mwtc/checks.py mwtc/main.py mwtc/tests/test_collect_et.py
git commit -m "MWTC: ET 'today' threaded through every calendar; economic calendar, UW rows and Finnhub news carry ET times only

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: As-of stamps on options, breadth and after-close earnings

**Files:**
- Modify: `mwtc/main.py` (`collect_data`, `_earnings_view`)
- Test: `mwtc/tests/test_collect_et.py` (add the class below)

**Interfaces:**
- Consumes: `collect_data(mode, now_et)` (Task 7), `sessions.previous_trading_day`, `macro_packet["sector_ranking"]["as_of_date"]` (Task 5).
- Produces:
  - `institutional.options_intel.as_of_et`: `str`;
  - `breadth.as_of_date`: `str | None`;
  - `earnings.calendar[*].after_close: True` for postmarket rows where `hour == "amc"` and the date is today;
  - `_earnings_view(..., reactions=None, today_iso: Optional[str] = None)`.

- [ ] **Step 1: Write the failing test** (append to `mwtc/tests/test_collect_et.py`, above `if __name__`)

```python
class AsOf(CollectEt):  # reuses run_collect; inherited CollectEt tests re-run harmlessly
    def test_options_stamp_premarket(self):
        d = self.run_collect("premarket", now=AM)
        self.assertEqual(d["institutional"]["options_intel"]["as_of_et"],
                         "2026-10-28 regular-session close (before that day's after-close reports)")

    def test_options_stamp_postmarket(self):
        d = self.run_collect("postmarket", now=dt.datetime(2026, 10, 29, 16, 30, tzinfo=ET))
        self.assertEqual(d["institutional"]["options_intel"]["as_of_et"],
                         "today's session, through ~4:30 PM ET")

    def test_breadth_stamped(self):
        d = self.run_collect()
        self.assertEqual(d["breadth"]["as_of_date"], "2026-10-28")

    def test_postmarket_amc_today_flagged_after_close(self):
        cal = [{"symbol": "AAA", "date": "2026-10-29", "hour": "amc", "verified": True},
               {"symbol": "BBB", "date": "2026-10-29", "hour": "bmo", "verified": True}]
        with mock.patch.object(main.earnings, "build", return_value={"calendar": cal}), \
             mock.patch.object(main.earnings, "result_line", return_value=None):
            d = self.run_collect("postmarket", now=dt.datetime(2026, 10, 29, 16, 30, tzinfo=ET))
        rows = {r["symbol"]: r for r in d["earnings"]["calendar"]}
        self.assertTrue(rows["AAA"]["after_close"])
        self.assertNotIn("after_close", rows["BBB"])

    def test_premarket_never_flags_after_close(self):
        cal = [{"symbol": "AAA", "date": "2026-10-29", "hour": "amc", "verified": True}]
        with mock.patch.object(main.earnings, "build", return_value={"calendar": cal}), \
             mock.patch.object(main.earnings, "result_line", return_value=None):
            d = self.run_collect("premarket")
        self.assertNotIn("after_close", d["earnings"]["calendar"][0])
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `PY -m unittest mwtc.tests.test_collect_et -v`
Expected: FAIL with `KeyError: 'as_of_et'`.

- [ ] **Step 3: Implement**

In `mwtc/main.py` `_earnings_view`, add a `today_iso: Optional[str] = None` parameter at the end of the signature. Inside the `for r in cal:` loop, before the `try:`, add:

```python
        # Wrap: a result released after today's 4 PM close cannot explain
        # today's session (2026-10-28: KLAC/FTNT 'drove XLK').
        if reactions is None and today_iso and r.get("hour") == "amc" and r.get("date") == today_iso:
            r["after_close"] = True
```

In `collect_data`, after `_uw_times_et(uw_packet)`, add:

```python
    oi = uw_packet.get("options_intel")
    if isinstance(oi, dict):
        oi["as_of_et"] = (
            "today's session, through ~4:30 PM ET" if mode == "postmarket" else
            f"{sessions.previous_trading_day(today).isoformat()} regular-session close "
            "(before that day's after-close reports)")
```

After the `breadth = …` line:

```python
    if isinstance(breadth, dict):
        breadth["as_of_date"] = (macro_packet.get("sector_ranking") or {}).get("as_of_date")
```

Change the `_earnings_view` call to:

```python
    earnings_view = _earnings_view(macro_packet, uw_packet, fmp_packet, reactions,
                                   today_iso=today.isoformat())
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `PY -m unittest mwtc.tests.test_collect_et -v`, then the full suite.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mwtc/main.py mwtc/tests/test_collect_et.py
git commit -m "MWTC: as-of stamps on options and breadth by mode; Wrap flags after-close earnings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Fear & Greed display, band and distance to neutral

**Files:**
- Modify: `mwtc/sources/sentiment.py:23-41` (`fear_greed`)
- Modify: `mwtc/report/gauges.py:150-157` (F&G card)
- Test: `mwtc/tests/test_fear_greed.py`

**Interfaces:**
- Produces:
  - `sentiment.fg_fields(score: float) -> dict` with keys `score_display` (int, half-up), `band` (str) and `points_to_neutral` (float, 0 when at Neutral or above);
  - `fear_greed()` returns those keys too;
  - the gauge prints `score_display`.

- [ ] **Step 1: Write the failing test**

```python
# mwtc/tests/test_fear_greed.py
import unittest
from unittest import mock

from mwtc.sources import sentiment
from mwtc.report import gauges


class FearGreed(unittest.TestCase):
    def test_half_up_display(self):
        self.assertEqual(sentiment.fg_fields(35.9)["score_display"], 36)
        self.assertEqual(sentiment.fg_fields(36.5)["score_display"], 37)

    def test_band_and_distance(self):
        f = sentiment.fg_fields(35.9)
        self.assertEqual(f["band"], "Fear")
        self.assertEqual(f["points_to_neutral"], 9.1)
        self.assertEqual(sentiment.fg_fields(44.6)["band"], "Neutral")
        self.assertEqual(sentiment.fg_fields(44.6)["points_to_neutral"], 0)
        self.assertEqual(sentiment.fg_fields(10)["band"], "Extreme Fear")
        self.assertEqual(sentiment.fg_fields(60)["band"], "Greed")
        self.assertEqual(sentiment.fg_fields(80)["band"], "Extreme Greed")

    def test_fetch_includes_fields(self):
        resp = mock.Mock()
        resp.json.return_value = {"fear_and_greed": {"score": 35.93, "rating": "fear"}}
        resp.raise_for_status.return_value = None
        with mock.patch.object(sentiment.requests, "get", return_value=resp):
            fg = sentiment.fear_greed()
        self.assertEqual((fg["score"], fg["score_display"], fg["band"]), (35.9, 36, "Fear"))

    def test_gauge_shows_display_value(self):
        html = gauges.render_dashboard({"sentiment": {"fear_greed": {"score": 35.9, "score_display": 36,
                                                                     "band": "Fear"}}})
        self.assertIn(">36<", html)
        self.assertNotIn(">35<", html)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `PY -m unittest mwtc.tests.test_fear_greed -v`
Expected: FAIL with `AttributeError: fg_fields`.

- [ ] **Step 3: Implement**

`mwtc/sources/sentiment.py`: add `import math` to the imports, then:

```python
# CNN's published Fear & Greed bands (on the displayed, rounded score).
_FG_BANDS = ((24, "Extreme Fear"), (44, "Fear"), (55, "Neutral"), (75, "Greed"), (100, "Extreme Greed"))


def fg_fields(score: float) -> dict:
    """Display value (half-up, like CNN's page — Python's round() is
    half-to-even), band, and distance to the Neutral band. The AI prints these,
    never its own threshold (2026-10-28: 'has not crossed into neutral (50)')."""
    disp = int(math.floor(float(score) + 0.5))
    band = next(name for top, name in _FG_BANDS if disp <= top)
    below = band in ("Extreme Fear", "Fear")
    return {"score_display": disp, "band": band,
            "points_to_neutral": round(45 - float(score), 1) if below else 0}
```

In `fear_greed()`, change the return to:

```python
        return {
            "score": round(float(score), 1),
            **fg_fields(score),
            "rating": fg.get("rating"),
            "previous_close": fg.get("previous_close"),
            "previous_1_week": fg.get("previous_1_week"),
        }
```

`mwtc/report/gauges.py`, in `render_dashboard`'s Fear & Greed block, replace the `rating`, `fg_color` and gauge lines with:

```python
    if score is not None:
        disp = fg.get("score_display")
        if disp is None:
            disp = int(math.floor(float(score) + 0.5))
        rating = fg.get("band") or fg.get("rating") or (
            "Fear" if disp < 45 else "Greed" if disp > 55 else "Neutral")
        fg_color = "#ef4444" if disp < 45 else "#22c55e" if disp > 55 else "#f59e0b"
        cards += ('<div class="mcard" style="text-align:center">'
                  '<div class="k">Fear &amp; Greed</div>'
                  f'{gauge(disp, str(disp), rating, fg_color)}'
                  '<div class="d">0 = fear &middot; 100 = greed</div></div>')
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `PY -m unittest mwtc.tests.test_fear_greed -v`, then the full suite.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mwtc/sources/sentiment.py mwtc/report/gauges.py mwtc/tests/test_fear_greed.py
git commit -m "MWTC: Fear & Greed shows CNN's rounded score, band and distance to Neutral

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Public-repo guard for the new fields

**Files:**
- Test: `mwtc/tests/test_public_trace.py`
- Modify: none expected. `dump_trace` already keeps only `TRACE_KEYS`. If a test fails, fix the leak in `mwtc/publish.py` or `mwtc/sources/sessions.py`.

**Interfaces:**
- Consumes: `publish.dump_trace(data, mode) -> Path`, `publish.OUT`, `main._uw_times_et`, `sessions.add_et_twins`.

- [ ] **Step 1: Write the test**

```python
# mwtc/tests/test_public_trace.py
import datetime as dt
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mwtc import main, publish
from mwtc.sources import sessions


class PublicTrace(unittest.TestCase):
    def test_trace_has_no_paid_feed_or_new_uw_fields(self):
        data = {"mode": "premarket", "movers": {"gainers": []}, "notes": {},
                "earnings": {"calendar": [{"symbol": "AAA", "after_close": True}], "focus_detail": {"x": 1}},
                "institutional": {"options_intel": {"as_of_et": "SENTINEL-OI"},
                                  "dark_pool_recent": [{"executed_et": "SENTINEL-DP"}]},
                "fed": {"kalshi": "SENTINEL-FED"},
                "macro": {"economic_calendar": [{"time_et": "8:30 AM"}]}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(publish, "OUT", Path(tmp)):
            p = publish.dump_trace(data, "premarket")
            blob = p.read_text()
        for bad in ("SENTINEL", "institutional", '"fed"', "focus_detail",
                    "executed_et", "when_et", "created_et", "options_intel"):
            self.assertNotIn(bad, blob, bad)
        json.loads(blob)

    def test_unparsable_uw_time_is_not_logged(self):
        packet = {"dark_pool_recent": [{"executed_at": "SECRET-VALUE-123", "price": 999.99}],
                  "per_ticker": {}}
        with self.assertLogs(level=logging.DEBUG) as cm:
            logging.getLogger("probe").debug("start")
            main._uw_times_et(packet)
            sessions.econ_rows_et([{"date": "SECRET-VALUE-456"}],
                                  dt.datetime(2026, 10, 29, tzinfo=sessions.ET))
        text = "\n".join(cm.output)
        self.assertNotIn("SECRET-VALUE", text)
        self.assertNotIn("999.99", text)
        self.assertEqual(packet["dark_pool_recent"][0]["executed_et"], "time unconverted")
        self.assertNotIn("executed_at", packet["dark_pool_recent"][0])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it**

Run: `PY -m unittest mwtc.tests.test_public_trace -v`
Expected: PASS. Tasks 6–8 were written to satisfy it. If it FAILS, fix the leak: never log row values, and keep `dump_trace` to `TRACE_KEYS`. Do not weaken the test.

- [ ] **Step 3: Commit**

```bash
git add mwtc/tests/test_public_trace.py
git commit -m "MWTC tests: trace and logs never carry UW rows or the new ET/as-of fields

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Prompt rules — the AI copies the code's fields

**Files:**
- Modify: `mwtc/report/prompt.py` (SYSTEM item 7; premarket §4, §6, §7, §11, §12, §20, §22; postmarket §4, §7, §9, §14, §15)
- Test: `mwtc/tests/test_prompt_rules.py` (add tests)

**Interfaces:**
- Consumes these field names from Tasks 1–9:
  - `rsi_zone`;
  - `high_52w_close`, `low_52w_close`, `pct_from_52w_high`, `pct_from_52w_low`;
  - `roll_note`, `rolled_today`, `contract`;
  - `time_et`, `is_today_et`, `is_next_session_et`, `economic_calendar_today_count`;
  - `as_of_date`, `bar_complete`, `as_of_et`, `after_close`;
  - `sector_ranking` (`order_by_rank`, `top3_1mo`, `bottom3_1mo`, `leaders_1d`, `laggards_1d`);
  - `score_display`, `band`, `points_to_neutral`;
  - index names `"Nasdaq 100"` / `"Nasdaq Composite"`.

- [ ] **Step 1: Write the failing tests** (add to `PromptRules` in `mwtc/tests/test_prompt_rules.py`)

```python
    def both(self):
        return [prompt.build_messages(m, "{}", "<html></html>", "Thursday") for m in ("premarket", "postmarket")]

    def test_code_fields_rule_in_both_modes(self):
        for s, u in self.both():
            t = s + u
            self.assertIn("CODE-COMPUTED FIELDS ARE FINAL", s)
            self.assertIn("rsi_zone verbatim. Use no other RSI adjective", t)
            self.assertIn("only fields ending in _et are times", t)
            self.assertIn("when pct_change is null, give no change and no direction", t)
            self.assertIn("score_display", t)
            self.assertIn("order_by_rank", t)
            self.assertIn("Nasdaq-100 (NDX)", t)
            self.assertIn("Never write a bare \"Nasdaq\" next to a number", t)
            self.assertIn("pct_from_52w", t)

    def test_premarket_as_of_and_today_rules(self):
        s, u = prompt.build_messages("premarket", "{}", "<html></html>", "Thursday")
        t = s + u
        self.assertIn("is that day's close", t)
        self.assertIn("No scheduled US releases today", t)
        self.assertIn("is_today_et", t)

    def test_postmarket_next_session_and_after_close(self):
        s, u = prompt.build_messages("postmarket", "{}", "<html></html>", "Friday")
        t = s + u
        self.assertIn("is_next_session_et", t)
        self.assertIn("after_close", t)
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `PY -m unittest mwtc.tests.test_prompt_rules -v`
Expected: FAIL. The SYSTEM prompt does not contain "CODE-COMPUTED FIELDS ARE FINAL".

- [ ] **Step 3: Implement**

In `mwtc/report/prompt.py` `SYSTEM`, after item 6 (ending `…a beat trading down is a \
negative catalyst.`), add item 7:

```python
7. CODE-COMPUTED FIELDS ARE FINAL — copy them, never recompute, relabel or convert:
- RSI: the RSI label is rsi_zone verbatim. Use no other RSI adjective (weak, \
strong, near or approaching overbought or oversold, stretched).
- 52-week: high_52w_close / low_52w_close are CLOSING extremes — say so. Say \
"near" a 52-week high or low only when pct_from_52w_high / pct_from_52w_low is \
within 3%; for futures (continuous_front_month) never compute a distance.
- Futures: when pct_change is null, give no change and no direction for that \
contract and never explain a move — print its roll_note. When rolled_today is \
set, say the change is vs the new front month (contract).
- Times: only fields ending in _et are times. Print them verbatim; never convert \
or derive a time; never put a time on a row whose time_et is null.
- Dates: as_of_date / as_of_et say when a number is from; bar_complete=false \
means a live, unfinished bar. Data stamped before an event cannot show the \
reaction to it. An earnings row with after_close=true was released after the \
4 PM close and cannot explain that day's regular session.
- Sectors: list sectors and fill the ranked chart in macro.sector_ranking \
order_by_rank order. Every "best / weakest / led / lagged" claim comes from \
top3_1mo / bottom3_1mo (1-month) or leaders_1d / laggards_1d (1-day) and names \
its window. Never re-rank.
- Indices: name them exactly — "Nasdaq-100 (NDX)" or "Nasdaq Composite". Never \
write a bare "Nasdaq" next to a number.
- Fear & Greed: print score_display and band only (points_to_neutral for the \
distance to Neutral); never state another threshold.
```

Keep the SYSTEM string's existing backslash line-continuation style. The block above is shown already in that style.

Section edits:
- `SECTIONS_PREMARKET` §4: append ` Use each future's pct_change as given; if it is null print its roll_note instead (no direction).`
- §6: append ` 52-week figures: high_52w_close/low_52w_close (closing extremes) and pct_from_52w_*.`
- §7: replace `Time|Event|Consensus|Actual|read.` with `Time|Event|Consensus|Actual|read — Time is the row's time_et verbatim ("time not scheduled" when null). List ONLY rows with is_today_et=true; if macro.economic_calendar_today_count is 0 print "No scheduled US releases today" — never move another day's row under today.`
- §11: replace `rank 11 sectors; strength, trend, bull/bear, driver (drives the ranked chart).` with `11 sectors in macro.sector_ranking.order_by_rank order (1-month return, 1-day tiebreak); strength, trend, driver (drives the ranked chart). Index and sector closes here are as_of_date's close: a figure whose as_of_date is before today is that day's close — call it "Wednesday's close" (its weekday), never "today" or "intraday".`
- §12: replace `Then S&P/Nasdaq/Dow/Russell: trend, support, resistance, RSI, key MAs.` with `Then S&P 500, Nasdaq-100 (NDX), Dow, Russell 2000 (technicals keys "S&P 500", "Nasdaq 100", "Dow Jones", "Russell 2000"; "Nasdaq Composite" only if named as such): trend, support, resistance, RSI with its rsi_zone, key MAs.`
- §20: replace `Fear & Greed, AAII` with `Fear & Greed (score_display, band, points_to_neutral), AAII`.
- §22: append ` List each event with its day_et and time_et.`

`SECTIONS_POSTMARKET`:
- §4: replace `Then S&P/Nasdaq/Dow/Russell closing levels & % change;` with `Then S&P 500, Nasdaq-100 (NDX), Nasdaq Composite, Dow, Russell 2000 closing levels & % change (name each exactly);`.
- §7: append ` Futures/commodities: pct_change null → print roll_note, no move. 52-week: high_52w_close/low_52w_close, pct_from_52w_*.`
- §9: replace `how the 11 sectors finished today; leaders & laggards.` with `how the 11 sectors finished today in macro.sector_ranking.order_by_rank order; today's leaders and laggards are leaders_1d / laggards_1d. An earnings row with after_close=true cannot have moved today's sectors.`
- §14: replace `Fear & Greed close` with `Fear & Greed close (score_display, band)`.
- §15: replace `key events tomorrow,` with `key events for the next session — ONLY economic-calendar rows with is_next_session_et=true, each with day_et and time_et verbatim (a Friday's next session is Monday),`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `PY -m unittest mwtc.tests.test_prompt_rules -v`, then the full suite.
Expected: PASS. The 3 existing prompt tests must still pass.

- [ ] **Step 5: Commit**

```bash
git add mwtc/report/prompt.py mwtc/tests/test_prompt_rules.py
git commit -m "MWTC prompt: code-computed fields are final — RSI zone, 52-week, roll notes, ET times, as-of, sector ranks, NDX naming, F&G band

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Free live check, whole-branch review, paid preview, owner OK

**Files:**
- Create (scratch, not committed): `$SCRATCH/live_check_1a.py`, where `$SCRATCH` = `/private/tmp/claude-501/-Users-admin-Desktop-Trae-Projects-TradeClub-AIReports-Sectors/34e92c5a-f467-4313-ad3f-e40661c24fb7/scratchpad`.

- [ ] **Step 1: Write the live-check script** (calls yfinance only; no model, no paid feed)

```python
# live_check_1a.py — run from the worktree root with the venv python.
import datetime as dt
import sys
sys.path.insert(0, ".")
import pandas as pd
import yfinance as yf
from mwtc import config
from mwtc.sources import macro, sessions

now = dt.datetime.now(sessions.ET)
ok = True

def check(name, cond, detail):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + ": " + str(detail))

# 1) Truncated to 10/28: Dow Wilder RSI 55.8 ±0.2, NDX close 29,813 ±1, VIX 52w 13.47/31.05
dji = yf.Ticker("^DJI").history(period="2y")["Close"].dropna()
dji = dji[dji.index.date <= dt.date(2026, 10, 28)]
check("Dow RSI to 10/28", abs(macro._rsi([float(x) for x in dji]) - 55.8) <= 0.2, macro._rsi([float(x) for x in dji]))
ndx = yf.Ticker("^NDX").history(period="1mo")["Close"].dropna()
c = float(ndx[ndx.index.date == dt.date(2026, 10, 28)].iloc[0])
check("NDX 10/28 close", abs(c - 29813.16) <= 1, c)
vix = yf.Ticker("^VIX").history(period="2y")["Close"].dropna()
vix = vix[vix.index.date <= dt.date(2026, 10, 28)]
hi, lo, _ = macro._range_52w(vix)
check("VIX 52w", abs(hi - 31.05) < 0.05 and abs(lo - 13.47) < 0.05, (lo, hi))

# 2) Current data: RSI == pandas ewm reference, NDX last within 0.5% of quote
q = macro._quote("^DJI", now_et=now)
s = pd.Series(q["closes"]).diff().dropna()
up = s.clip(lower=0).ewm(alpha=1/14, adjust=False).mean().iloc[-1]
dn = (-s.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean().iloc[-1]
check("Dow RSI vs ewm", abs(macro._rsi(q["closes"]) - (100 - 100/(1+up/dn))) <= 0.1, macro._rsi(q["closes"]))
nq = macro._quote("^NDX", now_et=now)
live = float(yf.Ticker("^NDX").history(period="1d", interval="1m")["Close"].dropna().iloc[-1])
check("NDX last vs live", abs(nq["last"] / live - 1) <= 0.005, (nq["last"], live))

# 3) Every future: a contract and a change, or a roll_note
for name, sym in {**config.FUTURES, **config.COMMODITIES}.items():
    f = macro._quote(sym, kind="futures", now_et=now)
    good = f and (f.get("pct_change") is not None and f.get("contract")) or (f and f.get("roll_note"))
    check(f"future {sym}", good, {k: f.get(k) for k in ("contract", "prev_close", "pct_change", "roll_note")} if f else None)

# 4) Record (not a pass/fail): does chart metadata previousClose equal the contract prior close?
for sym in ("NG=F", "SI=F", "HG=F", "CL=F"):
    tk = yf.Ticker(sym)
    tk.history(period="5d")
    print("INFO", sym, "meta previousClose:", (tk.history_metadata or {}).get("previousClose"))

print("ALL PASS" if ok else "SOME FAILED")
```

- [ ] **Step 2: Run it**

Run: `cd $WT && PY $SCRATCH/live_check_1a.py`, where `$WT` is the worktree.
Expected: `ALL PASS`. If a check FAILS, find the cause before going on: the data or the code. Record the `INFO` lines in the ledger. If `meta previousClose` ≠ the contract's prior close on a symbol that rolled recently, note it: roll days will then withhold, per Review Focus 1.

- [ ] **Step 3: Full suite and push**

Run: `PY -m unittest discover -s mwtc/tests -t .`
Expected: all pass (69 existing + the new tests).
Then: `git push origin feat/mwtc-data-accuracy-1a`.

- [ ] **Step 4: Whole-branch review**

Request a review of `git diff origin/main...feat/mwtc-data-accuracy-1a` against the spec and this plan (superpowers:requesting-code-review). Fix the findings, then re-run Step 3.

- [ ] **Step 5: One paid Pre-Market preview**

Run it outside 8:30–9:00 AM ET and 4:20–4:50 PM ET:
`gh workflow run mwtc.yml --ref feat/mwtc-data-accuracy-1a -f mode=premarket -f preview=true`
Then `gh run watch <id> --exit-status` and download the `mwtc-premarket-packet` artifact.
- Publish its preview HTML to `site/preview/mwtc-1a/index.html` on main, with noindex and the PREVIEW ribbon, the same way as `site/preview/pre-market-fix/`. It needs `git add -f`.
- Check the page: natural gas, RSI labels, the 52-week wording, the NDX row and names, ET times, sector order, and the Fear & Greed card.
- Send the owner the link.

- [ ] **Step 6: Merge after the owner's OK only**

Outside the run windows:
1. In the worktree, `git fetch origin && git merge origin/main`.
2. Run the full suite.
3. `git push origin HEAD:main`.
4. Confirm with `git log origin/main -3`.
5. After the next live Pre-Market and Wrap, spot-check the audit's cases.
