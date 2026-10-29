# Pre-Market report: real pre-market data and earnings that can't be misread — design

Date: 2026-10-24. Status: approved by the owner; two decisions revised on
2026-10-26 after measuring the data (mover thresholds, run data — see the
table). Implementation plan: `docs/superpowers/plans/2026-10-26-premarket-data-fixes.md`.

## Goal

The 8:40 AM Pre-Market report must never present old news as fresh, and must
never get an earnings result backwards:

- movers and earnings reactions are real **pre-market** moves from this
  morning;
- "Reported Since Last Close" means exactly that;
- beat/miss can't come out reversed;
- the report does not predict a direction ("will gap up") when it has no
  pre-market data to back it;
- every run keeps its input data, so any wrong number can be traced to the
  data or to the AI.

## What went wrong (evidence)

- **The "Pre-Market Movers" list is the previous session's.** It comes from
  FMP's biggest-gainers/losers lists, which rank the last regular session. On
  2026-10-20 the report showed CLF +16.34%, IRON +38.86% and SLN +125.58%
  "pre-market"; all three were Monday's close-to-close moves to the decimal.
  On 2026-10-19 RPGL −48.31% and RTSN +15.23% were Friday's moves.
- **The earnings "reaction" comes from that same stale list,** so it is
  missing or a day old. On 2026-10-22 the report said META "will gap up on
  the earnings beat". META gapped down about 3% and closed −7%. At 8:35 AM it
  was trading −2.5% pre-market, which the report could not see.
- **"Reported since last close" counts any report dated yesterday,** including
  reports before yesterday's open. On 2026-10-20, CLF and FITB (reported Monday
  morning) were presented as overnight news. On Mondays the window is Sunday,
  so Friday after-close reporters are missed.
- **Revenue actual and estimate were swapped** on 2026-10-22. The report said
  META revenue was $63.1B against a $64.6B estimate (a miss). Meta's 10-Q shows
  $64.6B actual (a beat). The input data was not saved, so whether the feed or
  the AI did the swapping cannot be traced.
- **EPS was the data provider's figure** ($7.40, likely adjusted for a one-time
  charge), presented with no label. Meta's GAAP diluted EPS was $3.83.

## Decisions

| Decision | Value | Why |
|---|---|---|
| Pre-market price source | yfinance 5-minute bars with pre/post-market (`prepost=True`) | Free, already a dependency, verified: META $680.90 at 8:35 AM on 2026-10-22, −2.5% vs the $698.43 close |
| Pre-Market Movers universe | S&P 500 ∪ Nasdaq-100 (about 520 unique names; the lists overlap) — option A, chosen by the owner 2026-10-24 | Real, liquid, optionable names; drops thin micro-caps with "no catalyst" |
| Mover thresholds | trades in at least 6 separate 5-minute windows since 4:00 AM and a print within the last 30 minutes; top 10 up, top 10 down — revised 2026-10-26, owner approved | Keeps out stale or one-print moves. Yahoo reports volume 0 on every pre-market bar (measured on 503 S&P names), so trade activity replaces the original $1M dollar-volume floor |
| Prior close | the official close from daily bars; the last regular 5-minute bar only as a fallback | The closing auction can differ from the 15:55 bar (META 10/21: $697.52 vs $698.43) |
| Overnight window | previous NYSE trading day after-close + today before-open (and today's prints with no time) | The real "since last close"; Monday includes Friday after-close; holidays handled |
| Trading calendar | `pandas_market_calendars` (NYSE) — already installed on the runner | Correct holidays and early closes without a hand-kept list |
| EPS label | "EPS (provider figure — may be adjusted)" | Data feeds often report adjusted EPS; GAAP can differ widely |
| Direction rule | no "will gap up/down" for a name without pre-market data; enforced by the existing output check | Prompt asks, code guarantees |
| Run data | the earnings + movers slice of each run, saved as a GitHub Actions artifact, kept 14 days — revised 2026-10-26, owner approved | Traceability. The repo is public, so its artifacts can be downloaded by any signed-in GitHub user: paid-feed data (Unusual Whales, CME FedWatch, analyst/insider) stays out |

## Components

### 1. Pre-market prices — `mwtc/sources/premarket.py` (new)

One function does the download and is used twice:

- `snapshot(symbols)` downloads five days of 5-minute bars with pre/post
  market (plus daily bars for the official close) in one `yf.download`
  call each. For each symbol it returns:
  - prior official close;
  - last pre-market print and its ET time;
  - % change;
  - how many 5-minute windows traded pre-market.
- `movers(universe)` = `snapshot` over the universe, filtered by the
  thresholds above, top 10 gainers and losers. Each row has ticker, company
  (when known), %, last price, time of the print and the number of 5-minute
  windows that traded.
- `reactions(symbols)` = `snapshot` for the overnight earnings names plus the
  focus tickers.

The universe is the S&P 500 list the bot already fetches from FMP
(`/sp500-constituent`), plus the Nasdaq-100 (`/nasdaq-constituent`). It falls
back to a committed list (`mwtc/data/universe.txt`) when FMP does not answer,
so the movers never depend on one feed.

Failure is non-fatal. If the download fails, the packet carries
`premarket: null` with a reason, and the report says pre-market data is
unavailable this run (see component 4). It never falls back to yesterday's
list under a "pre-market" heading.

Runtime: measured 32 s for 503 names (5-minute bars).

### 2. Overnight earnings window — `mwtc/sources/macro.py`

`_enrich_earnings` marks a row as an overnight result when:

- it is reported and dated the **previous NYSE trading day** with hour
  `amc`; or
- it is dated **today** with hour `bmo`, or today with no hour but already
  reported.

The Finnhub request window starts at the previous trading day, not calendar
yesterday. A previous-day report with no time given is not treated as
overnight news. It is listed with "timing unconfirmed — verify", because its
reaction may already have happened. The FMP cross-check (`earnings.reconcile`)
is unchanged.

### 3. Earnings figures computed in code — `macro.py`, `earnings.py`

For every reported row, code computes both surprises and builds a ready-made
line. The AI is told to quote that line for figures and never to restate them
from the raw fields:

    META — EPS (provider figure, may be adjusted) $7.40 vs est. $5.79: BEAT +27.8%
           · revenue $64.6B vs est. $63.1B: BEAT +2.4%
           · pre-market −2.5% at 8:35 AM ET

- Revenue gets `revenue_surprise_pct` and `revenue_beat_miss`, as EPS already
  has.
- When Finnhub and FMP disagree on revenue actual by more than 1%, the row
  gets a `verify_note`, as EPS mismatches already do.
- The reaction comes from component 1, replacing today's join with the stale
  movers list.

### 4. No direction without data — `mwtc/report/prompt.py`, `mwtc/report/generator.py`

- **Prompt:** for a name whose result line has no pre-market figure, write
  "pre-market reaction not available — verify live" and make no gap or
  direction call. When the reaction is present, the read follows the
  reaction: a beat trading down is a negative catalyst (this rule exists
  already and now has real data behind it).
- **Code:** `_coverage_gaps` (already used for one targeted retry) gains a
  check on the visible text. For each overnight name without a pre-market
  figure, any sentence naming the ticker together with a direction call
  ("gap up/down", "will open higher/lower", "will rally/fall at the open",
  "gap-up/gap-down") is a gap and triggers the single existing retry. A name
  whose reaction is below −1% but is described as gapping up, or above +1%
  but described as gapping down, is also a gap.
- The retry is one extra AI call (about $1.40) and happens only on a run that
  breaks the rule. If the retry still breaks it, the report publishes
  (fail-open, as today) and the run log says so.

### 5. Movers section and Market Wrap wording — `mwtc/report/prompt.py`

- **Pre-Market Movers:** the section reads from the new `premarket.movers`. Its
  subtitle says "S&P 500 and Nasdaq-100 names moving before the open, as of
  <time> ET". Each line shows the % and the time of the last print.
- **Market Wrap:** drop the instruction to group movers into "day session vs
  after-hours"; the bot has no after-hours data. The section stays "Post-Market
  Movers" from that day's session.

### 6. Keep each run's input data — `mwtc/publish.py`, `.github/workflows/mwtc.yml`

- `publish.run` writes the earnings and movers slice of the collected packet
  (movers, pre-market reactions, the reconciled earnings calendar with its
  result lines, notes) to `mwtc_out/<mode>-<date>.json`. No Unusual Whales,
  CME FedWatch, analyst or insider payloads: the repo is public.
- A new workflow step uploads `mwtc_out/` as an artifact named
  `mwtc-<mode>-packet`, kept 14 days, `if: always()`. A failed run keeps its
  data too.
- Nothing goes to the site. Artifacts live in GitHub Actions only.

### 7. A preview mode for the bot reports — `mwtc/publish.py`, `.github/workflows/mwtc.yml`

- `python -m mwtc.publish --mode premarket --preview` collects data,
  generates the report and writes the HTML and packet to `mwtc_out/`. It does
  not publish, notify or check in.
- `mwtc.yml` gets a `preview` input:
  - when set, the guard is skipped, the build runs on the branch, and
    `mwtc_out/` is uploaded;
  - the commit, push, health and notification steps are skipped;
  - a non-`main` run without preview is refused, as `report.yml` already
    does.
- Used once before going live, to show the owner a real morning report (one
  paid run, about $1.40).

## What it does not cover

- **GAAP vs adjusted EPS.** The feeds give one EPS number. The report labels
  it as the provider's figure and does not look up GAAP (that needs the SEC
  filing, which comes out at a different time).
- **Thin pre-market liquidity.** A $1M pre-market dollar-volume floor and a
  30-minute freshness rule keep out most noise. A large but news-free move in
  a big name still shows up, as it should.
- **The Gap Scout and the house reports.** They use different data paths and
  are not touched.

## Testing

- **Offline unit tests ($0):**
  - `premarket.snapshot` on recorded bars, including no pre-market prints,
    stale prints and missing symbols;
  - the overnight window on Monday, a Tuesday, the day after a holiday and
    the day after the 2026-11-27 early close;
  - revenue and EPS surprise signs, so a swap is impossible;
  - the direction check on short sample reports (with and without a
    reaction, and with contradicting directions).
- **Historical replay ($0):** run `snapshot` for 2026-10-22 08:35 ET and
  confirm META −2.5%. Run the 2026-10-20 window and confirm CLF and FITB are
  not overnight results, while STLD, AERO, WRB and CCK are.
- **On GitHub ($0):** the existing check-keys probe (`--dry-run`) also prints
  the movers, the reactions and the overnight list, to confirm Yahoo and FMP
  answer from the runner.
- **Preview (one paid run, about $1.40):** a full Pre-Market report built in
  preview mode, published as an unlisted preview page for the owner to read
  before anything goes live.
- **Merge** only after the owner approves the preview.

## Files touched

| File | Change |
|---|---|
| `mwtc/sources/premarket.py` | new: pre-market snapshot, movers, reactions |
| `mwtc/data/universe.txt` | new: fallback S&P 500 ∪ Nasdaq-100 list |
| `mwtc/sources/macro.py` | overnight window by trading day; revenue surprise |
| `mwtc/sources/earnings.py` | pass revenue fields through; revenue mismatch note; result line |
| `mwtc/sources/fmp.py` | Nasdaq-100 constituents |
| `mwtc/main.py` | wire premarket movers/reactions into the packet; drop the stale join |
| `mwtc/report/prompt.py` | result-line, no-direction, movers wording; Market Wrap after-hours line removed |
| `mwtc/report/generator.py` | direction check in `_coverage_gaps` |
| `mwtc/publish.py` | packet dump; `--preview` |
| `.github/workflows/mwtc.yml` | packet artifact; `preview` input and guards |
