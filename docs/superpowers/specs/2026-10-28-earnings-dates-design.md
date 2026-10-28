# Earnings dates you can trust — design

Date: 2026-10-28. Status: draft for the owner's review. Nothing here is built.
Branch: `feat/earnings-calendar`, built on `feat/premarket-data-fixes` (both
change the MWTC earnings code; that branch merges first).

## Goal

Every earnings date printed in any report is either exactly right or
honestly labelled as not yet known. In practice:

- **An exact day** ("Thu Oct 29, after close") is printed only when the
  company itself has confirmed it, and no other source disagrees.
- **A name that has already reported** is shown as reported, with the real
  day, taken from its SEC filing.
- **A date the company has not announced** is printed as "not announced yet —
  expected ~early Nov", never as a specific day or weekday.
- **No big reporter is missing** from a calendar that claims to cover a day.
- **The AI never writes an earnings date itself.** Code builds every date
  string; the AI only places it. A date the AI writes anyway is replaced by
  code before publishing; it is never published as written.

This is option A, chosen by the owner on 2026-10-28. No feed can know an
unannounced date, so "exact" can only be guaranteed for confirmed dates.

## What is wrong today (audit, 2026-10-28)

The audit checked the last week of reports against company announcements and
SEC filings. Every listed company files its results with the SEC the day it
releases them: U.S. companies on Form 8-K (Item 2.02), foreign companies on
Form 6-K.

**Pre-Market and Market Wrap** (dates from Finnhub, cross-checked with FMP):
356 rows checked; printed date and timing were right on 230 of 236 rows
(97%). The problems are elsewhere:

- **Big reporters missing.** The calendar is cut to 40 rows. On 4 of the
  last 5 days the Market Wrap said there were no major earnings the next
  morning, while big names were reporting: PG, UNP, LMT, VLO (10/22); AXP,
  SLB (10/23); KO, UPS, SPGI, Visa (10/27); GEV, APH, ADP, PSX (10/28). The
  10/27 wrap never mentioned Microsoft the night before its report. About 70
  names over $40B were affected, some because the feeds lacked them (NOW,
  TMUS, ORLY on 10/28).
- **Before-open/after-close mix-ups: 30 rows.** Once a name reports, the
  code drops its timing and the AI guesses. AMT reported at 7:03 AM but was
  listed after the close; VLO, SPOT, SNA and NVR the same.
- **Companies that no longer trade:** Kellanova, EA, Confluent, Helix and
  Leggett. Leggett was printed as "Verified, High-Confidence".
- **"Verified" only means Finnhub and FMP agree.** Neither says whether a
  date is confirmed, so two matching estimates pass. Finnhub alone had the
  wrong date for GD, JBLU, OKE, BKR and CWK. SCCO stayed listed as upcoming
  after it had reported.
- **Reported names called "still pending"** at 4:30 PM, because the feeds
  lag the 4:00–4:20 PM releases (PFG, SSD, GBX, SUI, PRK).

**Sector Intelligence and Mean Reversion** (dates from the AI's web search):
about 20% of dated claims are wrong, plus 29 confirmed dates hidden as
"unconfirmed":

- INTC "unconfirmed" the day before it reported (it announced its date on
  Oct 6). ORLY, XOM, CAT, COP, AMD, AMAT, SO, DIS, LLY and LMT the same.
- Last year's dates reused: XOM and CVX "Oct 31", which is a Saturday in
  2026.
- NVDA "Nov 19 — confirmed", but NVIDIA has not announced a date.
- Weekdays from 2025: AAPL was listed with 2025's days of the week.
- The same company with different dates in the two reports on the same day.

**Sector deep-dive pages** (Energy, Financials, Health Care, Semiconductors,
Technology) were last built 2026-06-23 to 06-26 and still serve June's dates.

## Decisions

| Decision | Value | Why |
|---|---|---|
| What "exact" means | Option A: an exact day only when company-confirmed and uncontradicted; otherwise "not announced yet — expected ~<span>" | Owner's choice 2026-10-28; the only version that can be guaranteed |
| Confirmed-date source | Unusual Whales earnings endpoints: `source` = company \| estimation, `report_time` = premarket \| postmarket \| unknown (UW OpenAPI, checked 2026-10-28) | Already paid; the only paid feed with an explicit company-confirmed flag |
| UW licensing | **The owner confirms before Phase 0** that the current UW plan allows showing derived earnings dates on the public site. UW includes commercial use from its Startup tier; redistribution is an enterprise license. | Hold UW to the same licensing standard used to rule out other sources |
| Second source | FMP `/stable/earnings-calendar?includeReportTimes=true` (if our plan answers it) | Already paid; second opinion on date and timing |
| "Has reported" source | SEC EDGAR 8-K Item 2.02 (U.S.) and 6-K (foreign) filings: the event date (`reportDate`) for the day, the acceptance time only as a timing hint | Public-domain U.S. government data |
| Figures (EPS, revenue) | Finnhub, cross-checked with FMP (unchanged, incl. `result_line`) | Already built on the pre-market branch |
| Not used | Earnings Whispers (terms forbid republishing without a separate license; internal-use license lists at $2,495/month); Nasdaq's back-end API (undocumented, terms restrict commercial use); Yahoo's estimate flag (wrong for NVDA, CSCO, AVGO, ORCL, MNST); the AI's web search | Licensing or reliability |
| Nasdaq | Allowed only in a manual accuracy spot-check tool and the Phase 0 / shadow-run grading, never in a report | Its confirmed rows agreed with Yahoo on all 60 checked, and with company/SEC truth on every name checked by hand (about 17); not licensed for publication |
| Universe | S&P 500 ∪ Nasdaq-100 (`mwtc/data/universe.txt`, 518 names), plus every name UW lists with market cap ≥ $10B, plus any ticker a report mentions (looked up on demand) | Covers every name the calendars should list, incl. large ADRs (NVS) |
| Big name | Universe member, or UW market cap ≥ $10B | Used for "never truncated" |
| Horizon | Previous NYSE trading day through +45 days ahead; SEC lookback 30 calendar days | Covers "next earnings" on swing cards, Mean Reversion's ~30 days, and recent reporters |
| Built how often | Once per process, shared by every report in a batch | Same strings in every report on the same morning |
| Legacy sector pages | All dated pages and index pages get an "Archived" banner; the legacy builder refuses to run without `--legacy-force` | Reversible; stops web-searched dates coming back |

## Components

### 1. The shared earnings calendar — `earnings_dates.py` (new, repo root)

One module, used by both the MWTC bot and the house-report builder (the same
pattern as `gap_feed.py`). Code builds it each run; the AI only reads it.

#### Inputs

- **Unusual Whales** — `/api/earnings/premarket?date=D` and
  `/api/earnings/afterhours?date=D` for each NYSE session in the horizon.
  - Each call uses `limit=100` and pages through `page=0,1,…` until a page
    returns fewer than 100 rows. That is about 80–100 calls per run in peak
    season, on top of the ~90 UW calls MWTC already makes.
  - Calls are throttled using UW's `x-uw-req-per-minute-remaining` header.
    The daily count (`x-uw-daily-req-count`, `x-uw-token-req-limit`) is
    logged.
  - A session with any failed page is marked **incomplete**.
  - `/api/earnings/{symbol}` is used for on-demand tickers and for confirmed
    weekend dates (BRK.B). Whether it carries future rows is a Phase 0
    question.
  - Fields: `symbol`, `report_date`, `report_time`, `source`, `marketcap`,
    `ending_fiscal_quarter`.
- **FMP** — `/stable/earnings-calendar` with `includeReportTimes=true`,
  fetched in 7-day chunks. A chunk that returns the 4,000-row cap is treated
  as truncated and logged. The exact field names for period, confirmed and
  time come from the Phase 0 probe.
- **SEC EDGAR**, with a descriptive User-Agent and at most 8 requests per
  second:
  - ticker → CIK from `company_tickers.json`;
  - the `data.sec.gov/submissions/CIK##########.json` record for every
    universe name, every name with a provider row anywhere in the horizon,
    and every on-demand ticker. That is about 520–700 requests, about 1.5
    minutes at 8 per second.

All ticker formats go through one normalizer: SEC and FMP use `-`
(`BRK-B`); the universe and Finnhub use `.` (`BRK.B`); UW's form is a
Phase 0 question.

#### Which names count, before any status rule

- **Dropped as no longer trading:** a ticker that is not in SEC's current
  `company_tickers.json` after normalizing. That one check covers every
  audit case: K, EA, CFLT, LEG, and HLX (now HOS). It does not look at
  deregistration forms, which companies also file when a single bond or
  preferred series is delisted. It does not drop share classes either:
  GOOG and GOOGL, FOX and FOXA, NWS and NWSA are all listed and share one
  CIK, and each ticker keeps its own row.
- **Stale rows dropped.** A provider row is stale when SEC already shows a
  results release for that row's fiscal quarter. The quarter comes from UW's
  `ending_fiscal_quarter` or FMP's period. Without a quarter, rows dated
  within 45 days after the latest results release are dropped. This catches
  SCCO.
- **Same symbol listed more than once.**
  - One company-sourced UW date → use it.
  - Two different company-sourced dates → rule 4 (company date unverified).
  - The same date in both the before-open and after-close lists → session
    unknown.
- **Missing from UW.** A universe name with no UW row but an FMP or Finnhub
  row → estimated from that row. It is never dropped.
- **Funds and indices** (ETFs, commodities in Mean Reversion) → "no earnings
  (fund)".

#### What counts as a results release (SEC)

- **The filing:** the earliest original filing for the event, never an
  amendment (`8-K/A`, `6-K/A`):
  - U.S. companies: Form 8-K whose `items` include `2.02`;
  - foreign companies: Form 6-K, which has no item codes. These include
    ASML, ARM, PDD, CCEP, TRI, FER, NBIS and SPOT.
- **An 8-K with Item 2.02 is the quarterly release when** its `reportDate`
  is within 3 trading days of a UW or FMP date for that ticker's current
  fiscal quarter.
- **With no provider date,** the filing's index page
  (`{accession}-index.htm`) must list an `EX-99` exhibit. Item 9.01 is not
  required: BKR files 2.02 + 7.01.
- **A 6-K is the quarterly release only when both hold:**
  - its index page lists an `EX-99` exhibit;
  - its `reportDate` is within 1 trading day of UW's company-confirmed date
    for the current quarter, or Finnhub/FMP actuals for that quarter are
    present.

  Foreign companies file many 6-Ks (buybacks, meetings), so date proximity
  alone is not enough.
- **Anything else is logged** as "Item 2.02 — not counted" and changes
  nothing. Examples: eBay's 10/22 pre-announcement (2.02 + 2.05, no
  EX-99); Darling's 10/22 joint-venture statement.

#### Status rules (in order)

1. **Reported (SEC).** A results release inside the window. The day is
   always the filing's `reportDate`, never the acceptance date. The
   acceptance time is converted from UTC to New York time; daylight saving
   time ends Nov 1. Session:
   - accepted on a day before `reportDate` (filed the evening before and
     released by SEC the next morning — CNC, DHR, PCG) → before open;
   - accepted on `reportDate` before 9:30 AM → before open;
   - accepted on `reportDate` between that day's close and 5:30 PM, with
     UW's time for that date not "premarket" → after close. The close comes
     from the NYSE calendar: 1 PM on Nov 27 and Dec 24.
   - any other case (accepted during market hours, after 5:30 PM, or on a
     later day — late filers such as NVR at 12:07, SCHW at 4:18 PM for a
     morning release, WAFD, FFIN, CASS) → UW's `report_time` for
     `reportDate` when it is premarket or postmarket, otherwise "time not
     confirmed".

   Late filers are listed by `reportDate` ("reported Tue Oct 20, after
   close"), never in "since last close" unless their `reportDate` is.
2. **Reported (provider data).** Applies to a name with:
   - a company-confirmed date of today, before open (or a confirmed date
     already in the past);
   - no SEC filing yet;
   - Finnhub or FMP actuals present.

   It becomes reported, labelled "(provider data)"; APH's 8-K came after
   the 8:40 AM run. The next run replaces the label once the filing
   arrives. A confirmed past date with neither a filing nor actuals shows
   "scheduled Tue Oct 27, after close — results not yet found", never in an
   upcoming group.
3. **Confirmed.** A confirming source gives a date for the current fiscal
   quarter, and no other source has a different date for the same quarter.
   - The confirming source is UW `source = company`.
   - When UW is down or lacks the name, FMP's confirmed flag can confirm
     instead, but only if FMP passed the Phase 0 accuracy gate.
   - FMP rows flagged or unflagged both count as "a different date".
   - FMP dates from a truncated chunk count as missing, not as agreeing.
   - **Session** is printed only when UW's `report_time` is premarket or
     postmarket and FMP's time, if present, agrees. Otherwise the display
     reads "time not announced". A session disagreement removes the session,
     never the date.
   - **Weekday** comes from the date, using the New York calendar.
   - **A confirmed non-trading day** (a Berkshire Saturday) shows its own
     weekday and is grouped with the next session's before-open names.
4. **Company date unverified.** The company has announced, but the sources
   disagree on the *date* for that quarter:
   - UW says company and FMP gives a different date; or
   - UW lists two different company-sourced dates.

   The display does not say "not announced". Its span runs from the earliest
   to the latest of the disagreeing dates, bucketed as in rule 5. The run log
   records only "sources differ by N days", never the dates. A *session*
   disagreement is not this rule; it stays under rule 3.
5. **Estimated.** Only estimates exist. The display shows a span running
   from the earliest to the latest estimate across sources:
   - one bucket when both fall in it ("mid-Dec");
   - otherwise a range ("mid–late Dec", "late Oct–early Nov").

   Buckets: early = days 1–10, mid = 11–20, late = 21–31. An estimate that
   has already passed with no filing shows "date not available".
6. **Unknown.** No source has the name → "date not available".

#### Display strings (exact)

| Case | Text |
|---|---|
| Confirmed with session | `Thu Oct 29, after close` · `Mon Nov 2, before open` |
| Confirmed, no session | `Thu Oct 29, time not announced` |
| Confirmed weekend | `Sat Nov 7, before open` |
| Reported (SEC) | `reported Tue Oct 27, before open` |
| Reported, time unknown | `reported Tue Oct 27, time not confirmed` |
| Reported (provider data) | `reported today, before open (provider data)` · `reported Tue Oct 27, after close (provider data)` |
| Reported, figures pending | `results out (SEC filing) — figures not yet in the data feeds` |
| Reported, next quarter known | `reported Tue Oct 27, after close; next: not announced yet — expected ~late Jan` |
| Reported 30–60 days ago, nothing in horizon | `next report not expected before Dec 12` |
| Scheduled, past, nothing found | `scheduled Tue Oct 27, after close — results not yet found` |
| Company date unverified | `company date unverified this run — ~late Oct` |
| Estimated | `not announced yet — expected ~early Nov` · `~mid-Nov` · `~late Nov` · `~mid–late Dec` |
| Source failure (per name) | `date not confirmed this run` |
| Source failure (whole block) | `Earnings dates could not be confirmed this run.` |
| Possible but unconfirmed (group label) | `Possible, not confirmed:` followed by each name's estimated display |
| Window flag, confirmed | `⚠ earnings inside window` |
| Window flag, estimated | `⚠ earnings expected inside window — not announced` |
| Unknown | `date not available` |
| Fund | `no earnings (fund)` |
| Session not fully loaded | `earnings list for Thu Oct 29 could not be loaded this run` |

#### What the record carries

`symbol`, `status`, `date`, `session`, `display`, `quarter`, and
`sources_agree` (for each source: agree, disagree or missing).

`date`, `session` and `display` are the published result: what the report
prints, whichever source it came from. **Raw source rows never leave
memory.** Neither the packet, the run trace nor the logs carry:
- UW's `source`, `report_time`, `marketcap` or `expected_move`;
- any UW date that is not the published one;
- any per-source date in a disagreement.

Disagreements are logged as counts and day differences only.

### 2. Pre-Market and Market Wrap — `mwtc/`

- **Dates come from the shared calendar.** Finnhub and FMP supply only the
  figures.
- **Figures join the SEC event** by ticker, with the provider's date within
  ±1 trading day of the SEC day. For figures, "verified" now means Finnhub
  and FMP agree on the figures, not on the date. A name with no provider
  figures yet reads "results out (SEC filing) — figures not yet
  in the data feeds" (no time: an acceptance time is not the release time).
- **Groups built by code, with no cap on big names:**
  - Reported since last close (SEC-backed), each with its `result_line`.
  - Today before open / today after close.
  - **Next session**, before open and after close. This list feeds the
    Market Wrap's "Tomorrow's Setup" and the Pre-Market's "after the close
    tonight".
  - The 5 trading sessions after the next session.

  In every group, every big name is listed and smaller names appear as a
  count. Big names whose estimate falls on today or the next session are
  listed separately as "possible, not confirmed". An incomplete session says
  so ("could not be loaded this run"), and nobody may call it quiet.
- **Code renders the Earnings Calendar section and Tomorrow's Setup earnings
  list** as HTML at markers (`<!--EARNINGS-CALENDAR-->`,
  `<!--NEXT-SESSION-EARNINGS-->`), the same way the dashboard dials are
  injected. If the AI leaves a marker out, it counts as a coverage gap. If
  the marker is still missing after the retry, code inserts the block, in
  this order of preference, at the end of the section whose heading contains
  "Earnings", else before the Fed/macro section, else before the footer
  disclaimer, and logs where.
- **The AI's text is checked against the calendar** on its raw reply, before
  injection, as part of the existing coverage check, sharing its one retry.
  The check flags:
  - any earnings date or status for a calendar name that differs from the
    calendar;
  - a session called quiet (or "no major earnings") while it has big
    reporters or is incomplete;
  - a next-session name of $40B or more left out.

  If a mismatch survives the retry, code rewrites it to the calendar's
  display text, or removes the date, and appends any missing big names. It
  logs every change. A date mismatch is never published as written.
- **The calendar reaches the AI as compact rows** (symbol, group, display)
  under a key on `_cap_packet`'s protected list, so the size limit can never
  drop names. A peak-day test (300+ names) proves it.
- **New York clock.** Every earnings window uses the report's New York date,
  passed in explicitly. Today it uses the runner's UTC clock.

### 3. Sector Intelligence, Mean Reversion and on-demand deep dives — `build.py`, `prompts/`

- **The AI never writes an earnings date.** The prompts tell it to write
  `[[EARN:XOM]]` wherever a stock's next earnings date goes, and to stop
  searching the web for dates.
- **A helper, `fill_earnings(html, sidecar, slug)`, runs inside `attempt()`**
  of both `build_report` and `build_report_templated`, right after the reply
  is parsed. It does not run for the Gap Scout. In order:
  1. **Check the AI's raw text and the sidecar's text fields for
     hand-written earnings dates.**
     - **A hit is** a date token or a scheduling phrase:
       - a date token: a month name, month-day, weekday or relative day
         (today, tonight, tomorrow, this/next week);
       - a scheduling phrase: "reports on", "reports before/after", "due",
         "scheduled", "confirmed for", "unconfirmed", "date TBD", "verify
         date", "before the open", "after the bell".

       Either kind counts only in the same sentence or table row as:
       - an earnings word (earnings, reports, results, EPS, print, call), and
       - a ticker or company name (from UW's full name, SEC's title, or a
         short-name list).
     - **Not a hit:** macro events (FOMC, CPI, PCE, payrolls/jobs, auctions,
       OPEC), and commentary on results already out ("reported strong
       results", "EPS beat") with no date token or scheduling phrase.
     - **Earnings tables:** any table the AI drew with earnings dates counts
       as hand-written.
     - **On a hit:** it raises inside `attempt()`, so it uses the existing
       single retry and never causes a third model call. After the last
       attempt, code replaces the hit's date token or scheduling phrase with
       the calendar's display text for that name, or deletes it if no name
       can be matched, and logs it.
  2. **Fill every placeholder** (tolerant of spaces and case; tickers with
     dots allowed). In page text it becomes a
     `<span class="tc-earn">display</span>`; in HTML attributes and sidecar
     text, plain text. Tickers outside the calendar use the on-demand lookup.
     Any leftover `[[EARN` is removed and logged.
  3. **The amber ⚠ is added by code:**
     - when a confirmed date falls inside the report's window: "⚠ earnings
       inside window";
     - when an estimated span, widened by 7 days each side, overlaps the
       window: "⚠ earnings expected inside window — not announced".

     The windows are Sector Intelligence's swing window (6 weeks by default,
     or the run's `HORIZON:` modifier) and Mean Reversion's 30 days. The
     picks table's earnings-window column comes from the same code.
  4. **Recent reporters:** a results release within the last 30 days
     displays "reported Tue Oct 27, after close". If a next-quarter row
     exists, it adds "; next: not announced yet — expected ~late Jan". A
     release 30–60 days ago with no row inside the horizon displays "next
     report not expected before <horizon end>"; older than that with no row,
     "date not available".
- **The event calendar's earnings rows** are rendered by code at a marker:
  every confirmed big name in the next 10 trading sessions. If the AI leaves
  the marker out, code inserts the block at the end of the event-calendar
  section, else before the footer disclaimer, and logs it.
- **Hub cards and Telegram** take their text from the filled sidecar, so no
  placeholder or hand-written date reaches them.
- Web searches no longer spent on dates go back to news and catalysts.

### 4. Legacy sector pages

- **The banner:** every page under `site/{energy,financials,health-care,
  semiconductors,technology}/` (the June 23–26 dated pages and each
  `index.html`, 20 pages) gets a fixed "Archived — June 2026; dates and
  prices are out of date" banner. The pages stay online.
- **The legacy builder:** `build_sector` (via `build.py main()`) refuses to
  run unless `--legacy-force` is passed, and injects the same banner when
  it does.
- **New deep dives built on demand** (Mode A) use component 3.

### 5. Failure handling

A missing source never brings back guessing:

- **UW down** → FMP's confirmed flag alone. If that is missing too, no exact
  day is printed for upcoming names: the calendar says "dates could not be
  confirmed this run", and each name shows "date not confirmed this run".
- **One UW session incomplete** → only that session says "could not be
  loaded this run".
- **SEC down** → reported status falls back to the providers' actuals,
  labelled "(provider data)".
- **Every source down** → the calendar block says so; reports still
  publish.
- Nothing ever falls back to the AI's web search for a date.

### 6. Changes between runs

Each run saves its derived records (symbol, status, date, session — no raw
UW fields) in the run trace and compares them with the previous run's. A
confirmed date that changes, or loses confirmation without a filing, is
logged as a "calendar change" and listed in the owner's Telegram
notification.

### 7. Public-repo safety

The repo is public, so its CI logs and artifacts are too.

- The probe and the shadow run print only shapes: field names, row counts,
  agreement counts, pass/fail.
- The trace carries only derived records.
- A test asserts that no packet, trace or log line contains UW fields
  (`marketcap`, `expected_move`, `street_mean_est`, `source`).

This is the same rule as the pending UW-logs fix.

## Phase 0 — prove the sources before building on them ($0)

The owner first confirms the UW licensing point in Decisions. Then one
check-keys run on the branch, printing counts and pass/fail only, answers:

1. **UW by-date.** Do `/api/earnings/premarket|afterhours` answer for future
   dates up to +45 days, with `source` and `report_time`? Rows and pages are
   counted per date, and rows by `report_time` and `source`.
2. **Unknown-time rows.** Do rows with `report_time = unknown` appear in
   either list?
3. **UW per ticker.** Does `/api/earnings/{symbol}` return a future row for
   5 names with known upcoming dates, and in what symbol format (BRK.B vs
   BRK-B)?
4. **Coverage.** How many universe names have an FMP row in the next 10
   sessions but no UW row?
5. **FMP** `includeReportTimes=true`: does our plan answer it, and with which
   field names?
6. **SEC** from GitHub's runners: do submissions and filing indexes answer
   with our User-Agent?
7. **Accuracy gate.** The probe carries a truth table from the audit.
   - Company-announced names still ahead on the probe day, for example: AMD
     11/03 after close; AAPL 11/02 after close; ABBV, XOM, CVX, GD 10/30
     before open; MAR 11/02 before open; COP, SO 11/05 before open; DVN
     11/05 after close; CTVA 11/11 after close; AMAT 11/12 after close; DIS
     11/17 before open.
   - Unannounced names: NVDA, CSCO, MNST, WMT, HD, LOW.

   **Gate:**
   - every announced name has UW `source = company` with the company's date,
     and the company's session wherever UW gives one;
   - no unannounced name has `source = company`;
   - the same test, run on FMP's confirmed flag, decides whether FMP may
     confirm on its own when UW is down (rule 3).

   On the probe day the table is refreshed against the companies' own
   announcements. `tools/earnings_spotcheck.py` is built as the plan's first
   task, before the probe. The gate runs again in the grading of each
   shadow-run day.

**Fallbacks:**
- No future rows per ticker → on-demand lookups use FMP by ticker. A
  weekend reporter (Berkshire) is then confirmed only if FMP passed the gate;
  otherwise it shows its estimate. That is a known, rare limit.
- Unknown-time rows absent → a per-ticker sweep for universe names missing
  from both lists.
- More than 2% of universe names that FMP lists in the next 10 sessions are
  missing from UW → FMP rows are added as estimated.
- **The accuracy gate fails, or UW has no `source` field on our plan** → the
  plan stops and the owner decides between a licensed paid source and
  publishing estimates only.

## Testing

- **Unit tests, offline ($0)**, on recorded and synthetic payloads:
  - each status rule, the dedupe and stale-row rules, delisted names;
  - the SEC session rule: CNC (19:05 evening before), DHR (17:43), NVR
    (12:07), SCHW (4:18 PM), C (10:09), WAFD/FFIN (next day), CASS (two days
    later), one filing on each side of the Nov 1 time change, a half day;
  - 6-K filers (ASML, SPOT); eBay and Darling (not results releases);
  - UW pagination (250 rows over 3 pages arrive intact; a failure on page 2
    marks the session incomplete); FMP chunk truncation;
  - every display string in the table;
  - the hand-written-date check and placeholder filling on sample report
    HTML and sidecars, including macro-date negatives (CPI, FOMC);
  - the size limit leaving a 300-name calendar intact;
  - no UW fields in packet, trace or logs;
  - the MWTC renderer and the house placeholder filler giving identical
    strings from one fixture.
- **Regression cases from the audit**, each with its run time:
  - the 10/26 wrap's next session: KO, UPS, SPGI before open and Visa after
    close;
  - the 10/27 wrap's next session: GEV, APH, ADP, PSX before open and MSFT
    after close;
  - MSFT is "Wed Oct 28, after close" up to and including the 10/28
    Pre-Market, and "reported Wed Oct 28, after close" from the 10/28
    4:30 PM wrap on (INTC likewise for 10/29);
  - VLO, SNA, NVR are "reported Thu Oct 22, before open" in the 10/23 runs
    (NVR's session from UW), and SPOT is reported via its 6-K;
  - in every run from 10/22 to 10/28: NVDA is "not announced yet"; XOM and
    CVX are never "Oct 31" (and read "Fri Oct 30, before open"); K, EA, CFLT,
    LEG and HLX never appear; GD is "Fri Oct 30, before open", never Oct 22;
  - SCCO is "reported Wed Oct 21, …", not upcoming, in the 10/26 runs;
  - PFG, SSD, GBX, SUI, PRK in the 4:31 PM wrap on their report days are
    "reported", not pending;
  - Visa sits in "Reported since last close" in the 10/28 Pre-Market;
  - NOW, TMUS and ORLY appear in their sessions on 10/28 even though Finnhub
    lacked them (from UW);
  - ISTR, where both Finnhub and FMP were wrong, gets no exact day unless UW
    has a company date;
  - a synthetic Berkshire Saturday date shows "Sat Nov 7, before open" and
    groups with Monday's before-open names;
  - HOOD's 8-K/A is ignored in favour of its original 8-K; APH on 10/28 at
    8:40 AM is "reported today, before open (provider data)".
- **Shadow run ($0 AI cost)**, an acceptance gate before any preview.
  `earnings_dates.py` runs in CI on 5 consecutive trading days, including
  Mon Nov 2 (the first day after the time change) and one peak day. The next
  day, every row is graded against SEC filings and the spot-check tool.
  - **Pass:**
    - no exact day that differs from the company's date;
    - no reported row with the wrong day or session;
    - no universe name whose results filing has a `reportDate` since the
      last close missing from "Reported since last close" (late filers
      appear by their `reportDate` and are graded there);
    - no big name with a company-confirmed date missing from its session's
      group.
  - **Also reported to the owner:** how many confirmed names still showed
    "not announced yet" (UW lagging the company).
- **Previews** after the shadow run passes: one Pre-Market, one Market Wrap,
  and one Sector Intelligence + Mean Reversion run, each published as an
  unlisted preview page. That is about $1.40 per MWTC report, plus the normal
  cost of each house report.
- **Merge** only after the owner approves the previews.

## Not covered

- **Dates a company has not announced** stay estimates; that is the
  design.
- **EPS/revenue basis** (GAAP vs adjusted, REIT FFO vs EPS, currency, e.g.
  SPOT in EUR): partly handled by the pre-market branch's "sources disagree"
  flag; not fixed here.
- **The economic calendar's UTC times** (FOMC printed as "6 PM ET") are a
  separate bug found in the audit; separate fix.
- **The Gap Scout** uses earnings only as narrative context; unchanged.

## Files touched

| File | Change |
|---|---|
| `earnings_dates.py` | New: shared calendar (UW + FMP + SEC), name rules, status rules, display strings, change detection |
| `tools/earnings_spotcheck.py` | New: manual Nasdaq/company comparison and shadow-run grader; never used by a report |
| `mwtc/sources/macro.py`, `fmp.py`, `earnings.py` | Dates from the shared calendar; figures only from Finnhub/FMP, joined to SEC events; New York date passed in |
| `mwtc/sources/unusual_whales.py` | Docstring: `/api/earnings/*` endpoints are an exception to the skill.md whitelist (UW OpenAPI and earnings skill) |
| `mwtc/main.py` | Calendar groups (incl. next session) in the packet |
| `mwtc/report/prompt.py`, `generator.py` | Code-rendered calendar and next-session blocks with fallbacks; date-consistency check with code rewrite; protected calendar key |
| `mwtc/publish.py` | Trace keeps derived calendar records only; change detection; notification lines |
| `build.py` | `fill_earnings` inside `attempt()`; event-calendar block; legacy builder refuses without `--legacy-force` |
| `prompts/sector-intelligence.md`, `mean-reversion.md`, `_house.md` | Placeholders instead of dates; no date searching |
| `site/{energy,financials,health-care,semiconductors,technology}/*.html` | Archived banner (20 pages) |
| `.github/workflows/mwtc.yml`, `report.yml`, `build.yml` | SEC User-Agent env; `FMP_API_KEY` (and `UW_API_KEY` for `build.yml`) in the build steps; shadow-run job; probe output |
