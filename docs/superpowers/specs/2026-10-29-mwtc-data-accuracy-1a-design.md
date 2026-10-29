# Pre-Market / Market Wrap: correct the numbers the code already computes (wave 1a) — design

Date: 2026-10-29. Status: the owner approved the design in chat on 2026-10-29
("yes"). Three independent reviewers then checked this spec against the code
and live data, and their fixes are folded in. It is waiting for the owner's
review.

The baseline is merge commit 0caf84b (feat/premarket-data-fixes into main). It
lands on main after the 10/29 Market Wrap. Line numbers below refer to that
tree.

Wave 1b is a separate spec. It covers release results, Fed facts, after-hours
prices, stock identity and sector, analyst and insider detail, the options
summary, and hub cards from the report's own bias.

## Goal

Every number the MWTC bot computes and hands to the AI must meet three rules:

- it is right;
- it says what it is and when it is from;
- any time in it is in Eastern Time.

The AI never converts, ranks or labels those numbers. When the code cannot be
sure of a number, it withholds it and says why. It never prints a wrong one.

## Evidence (2026-10-29 audit, skeptic-confirmed)

The full list is in `scratchpad/audit29/audit-2026-10-29.md`. This wave removes
about 50 of the roughly 125 confirmed Pre-Market and Wrap errors.

- **Fake natural-gas moves.**
  - The Wrap of 10/28 said "+12.24%". Pre-Market of 10/29 said "+4.61%".
  - Cause: Yahoo's continuous `NG=F` switched from the November to the
    December contract, and the code compared December's price with
    November's close.
  - Like for like, gas was about −2.7% (2.95 vs December's own 3.032).
  - The AI then invented causes, such as EU winter demand.
  - Silver and copper rolled the same day by less than 1%. That was invisible,
    but still mixed two contracts.
- **RSI is not the standard.**
  - `_rsi` takes a simple average of the last 14 changes. It printed the Dow at
    68.1, "Overbought". Wilder's RSI, which brokers use, gives 55.8.
  - The AI picks its own labels, such as "Overbought", "Weak" and "near
    overbought".
- **"52-week" means 6 months.** `_quote` fetches `period='6mo'`, then calls
  the results `high_252`/`low_252`. Example: VIX shown as 14.21–22.22, true
  13.47–31.05. Silver, Bitcoin and gold show the same error.
- **The wrong index sits under "Nasdaq 100".** The NDX row and dial are fed the
  Nasdaq Composite (^IXIC, 26,550). NDX itself closed at 29,813.
- **UTC times are printed as ET.**
  - FOMC "6:00 PM ET": true 2:00 PM.
  - EIA "2:30 PM": true 10:30 AM.
  - 7-year auction "5:00 PM": true 1:00 PM.
  - Musalem "4:30 PM today": true Friday at 12:30 PM.
  - A dark-pool print "~8:30 PM ET": true 4:30 PM.
  - On top of that, "today" comes from the runner's UTC clock.
- **Data carries no "as of".** Prior-session breadth, IV and sector moves are
  narrated as reactions to news that came later.
- **The AI ranks sectors itself.** Its lists contradict the code's 1-month
  returns:
  - "Best: Energy, Financials, Health Care". Financials is actually 7th of 11.
  - "Weakest: XLC, XLK, XLU". XLU is actually 3rd best over the month.
  - The chart order breaks the report's own ranking rule.
- **Fear & Greed is truncated.**
  - 35.9 is shown as "35".
  - The Neutral band is stated freehand as "50". CNN's band is 45–55.

## Design

### 1. Futures contract roll (`macro._quote`; the 9 `=F` symbols in `config` FUTURES/COMMODITIES)

Covers ES, NQ, YM, RTY, CL, NG, GC, SI and HG.

**Steps for each `=F` symbol:**

- `last` always comes from the continuous series. A dead contract's price is
  never shown.
- Resolve the contract Yahoo is quoting:
  - primary: `.info['underlyingSymbol']` (e.g. `NGZ26.NYM`);
  - `contract = underlyingSymbol.split('.')[0]`;
  - check: `.info['expireDate']` must not be before today ET.
  - Verified live on 10/29 for all 9 symbols. NG, SI and HG rolled that day.
- Fetch that contract's own daily bars with `history(period='5d')`.
- **Always** compute `prev_close`, `change` and `pct_change` from the
  contract's own bars, roll or not. A 0.4% roll gap mixes contracts just as
  much as a 7% one.
- Never use `fast_info.regularMarketPreviousClose` for futures.

**The contract must match the continuous series.** If the contract's last bar
is missing, is dated differently from the continuous last bar, or differs from
the continuous `last` by more than 0.5%, Yahoo is not quoting that contract.
That triggers the fail-safe.

**Fallback when `.info` fails:**

- Use the continuous chart's own metadata (`history_metadata`):
  - `previousClose` and `regularMarketPrice` give the change;
  - `shortName` gives the contract label.
- If both sources are available and disagree by more than 0.25%, use the
  fail-safe.

**Fail-safe** (no reliable contract):

- `prev_close`, `change` and `pct_change` are all `None`.
- `roll_note = "contract roll — change withheld"`.
- Only `last` and `roll_note` remain. The AI then cannot compute the fake move
  itself.

**Roll-day information:** when the continuous prior close differs from the
contract's prior close by more than 0.1%, set:

- `rolled_today: true`;
- `roll_note = "front month rolled to NGZ26; change is vs NGZ26's prior
  close"`.

The change is still shown.

**52-week fields for futures:** high and low stay on the continuous series,
labeled `continuous_front_month: true`. `pct_from_52w_*` is `null` for futures
(see §3). Futures have no SMA fields; only indices get technicals.

**Prompt rules:**

- When `pct_change` is `None`, give no change and no direction for that
  contract, and never explain a withheld move.
- When `rolled_today` is set, say "vs the new front month".

**Cost:** 9 `.info` calls plus 9 contract `history` calls, measured at about 10
s total. Each call is wrapped in try/except, falls back, and never crashes.

**Safety:** only the AI reads futures and commodity changes. VIX `last`, which
`gauges` uses, is not a future.

### 2. Wilder RSI with a code-assigned zone (`macro._rsi`, `_quote`, `_technical_levels`, `gauges`)

- **Wilder RSI:**
  - seed with the simple average of the first 14 gains and losses;
  - then update with `avg = (avg*13 + x)/14`;
  - compute over the full fetched history (2 years; see §3).
- **No trim.** Stop trimming closes to 60 (macro.py:48). Slices elsewhere stay
  as they are, and `_strip_closes` still removes closes from the packet.
- **One threshold set.** `RSI_OVERBOUGHT = 70` and `RSI_OVERSOLD = 30` are
  defined once, in `macro`:
  - `rsi_zone` is `"overbought"` when RSI ≥ 70, `"oversold"` when RSI ≤ 30,
    else `"neutral"`;
  - `gauges` uses the same constants and the same ≥/≤ comparisons. It is
    strict `>`/`<` today, so at exactly 70.0 the text and the dials agree.
- **Prompt rule, both modes:** "The RSI label is `rsi_zone` verbatim. Use no
  other RSI adjective (weak, strong, near or approaching overbought or
  oversold, stretched)."
- **Expected:** the Dow to 10/28 goes from 68.1 "Overbought" to 55.8
  "neutral". The trend dials move by a few points. No test pins their values.

### 3. True 52-week range (`macro._quote`)

- Fetch `period='2y'`. yfinance's `1y` counts back from the request time, so on
  a pre-market run the window would miss 1–3 days.
- The extremes come from a **calendar cut**, `closes[index >= last_date − 365
  days]`, applied to every group. Crypto (366 rows a year) and FX (about 260)
  break a row count such as 252.
- If the first fetched bar is later than `last_date − 365 days`, set
  `range_note: "less than 52 weeks of data"`.
- Field changes:
  - rename to `high_52w_close` and `low_52w_close`;
  - add `pct_from_52w_high` and `pct_from_52w_low`, computed in code and
    `null` for futures.
  - No code reads the old names (git grep).
- **Prompt rule:** call these closing highs and lows. Say "near" only when the
  code's `pct_from_52w_*` is within 3%.

### 4. Real Nasdaq-100 (`config.INDICES`, `gauges`, `publish.derive_sidecar`, `prompt.py`)

- In `config.INDICES`, add `'Nasdaq 100': '^NDX'`. Keep `'Nasdaq Composite':
  '^IXIC'` as its own row. Both get technicals.
- `gauges.render_dashboard` (133), `render_index_bias_dials` (262) and
  `publish.derive_sidecar` (145) read `'Nasdaq 100'`.
- **Prompt:** in premarket §12 and postmarket §4 and §15, name the index
  exactly ("Nasdaq-100 (NDX)" or "Nasdaq Composite"). Never write a bare
  "Nasdaq" next to a number.

### 5. Eastern Time everywhere

**One normalizer**, `econ_rows_et(rows, now_et)`, in `mwtc/sources/sessions.py`:

- **Input shapes:**
  - FMP rows carry `date`;
  - Finnhub rows carry `time`;
  - both use `'YYYY-MM-DD HH:MM:SS'` in UTC with no offset.
- **Parsing:**
  - `strptime(...).replace(tzinfo=timezone.utc)`;
  - never a naive `astimezone`;
  - never `fromisoformat` (the local venv is Python 3.9).
- **Output per row:**
  - `time_et` such as `"10:30 AM"`: no leading zero, no "ET" suffix;
  - `date_et`;
  - `day_et` such as `"Fri 10/30"`;
  - `is_today_et`;
  - `is_next_session_et`;
  - the raw timestamp is dropped.
- **Untimed rows:** a bare date, or a time of exactly `00:00:00`, means no time
  was given:
  - `time_et: null`;
  - `time_note: "time not scheduled"`;
  - `date_et` is the raw date, not converted.
- **Unparsable rows:** keep the raw value and set `time_et: "time
  unconverted"`.
- **Whole-list cases:**
  - an empty list gives `[]`;
  - a non-list or error body gives `None`;
  - the result also carries `today_count`.
- **DST** ends Sun 2026-11-01. The code never hard-codes −4.

**Where it runs:** once, in `main.collect_data`, after the existing
FMP-or-Finnhub choice (main.py:104-106). Rows are never converted twice.

**Next session:** add `sessions.next_trading_day(d)`, the mirror of
`previous_trading_day`: NYSE calendar, with a weekday fallback.

**"Today" comes from ET, not the runner:**

- These functions take a `today: dt.date` argument and pass it to their
  calendar calls:
  - `macro.collect` and `fmp.collect`;
  - `macro.economic_calendar` and `fmp.economic_calendar`.
- `main.collect_data` passes `report_dt.date()`.
- `checks._fmp_econ` computes its own ET date.
- `earnings._selftest` is an offline self-test and is left alone.
- This matters for a run after UTC midnight (8 PM EDT / 7 PM EST), such as a
  late GitHub backup of the 4:30 PM Wrap. Those backups have fired about 5
  hours late.

**Other timestamps the AI sees:** each gets an `_et` twin, and the raw UTC
field is removed from the packet copy. Every rule here uses the same parser
and accepts `Z`, `+00:00`, fractional seconds and epoch values.

| Packet location | New field | Source field |
|---|---|---|
| `dark_pool_recent`, `per_ticker.*.dark_pool` | `executed_et` | `executed_at` |
| `flow_alerts_market`, `per_ticker.*.flow_alerts` | `created_et` | `created_at` or epoch `start_time` |
| `news_headlines` | `when_et` | `created_at` |
| `market_tide` rows | `time_et` | row timestamp |
| `macro.economic_news` | `when_et` | epoch `datetime` |

- The parser tolerates missing fields: it tries the candidate names and skips
  anything that is not a list.

**Public repo:** the normalizer logs counts only, never row values.
`dump_trace` and `_packet_probe` must not output any of these fields.

**Prompt rules, both modes:**

- "Only fields ending in `_et` are times. Print them verbatim. Never convert or
  derive a time. Never put a time on a row whose `time_et` is null."
- "Today's events are `is_today_et` rows only. If `today_count` is 0, write
  'No scheduled US releases today'. Never move another day's row under today."
- Postmarket "Tomorrow's Setup" and the Monday Weekly Outlook list only
  `is_next_session_et` rows as the next session.

### 6. "As of" stamps

- **Price bars:** `_quote` adds `as_of_date` (the date of the last daily bar)
  and `bar_complete`.
  - `bar_complete` is false when the bar is dated today ET and that market has
    not closed: futures, FX and crypto always; indices and ETFs between 9:30
    and 4:00.
  - Futures, FX and crypto also get `as_of_et` from
    `history_metadata['regularMarketTime']`.
  - Crypto gets `day_basis: "UTC day (starts 8 PM ET)"`.
- **Sectors and breadth:** `sector_rotation` carries `as_of_date`.
  `sector_breadth` and the FMP breadth path are stamped in `main.collect_data`
  with the latest bar date.
- **Options:** `options_intel` is stamped in `main.collect_data`, which knows
  the mode:
  - premarket: `as_of_et = "2026-10-28 regular-session close (before that
    day's after-close reports)"`;
  - postmarket: `"today's session, through ~4:30 PM ET"`.
  - This stays in the packet only.
- **Earnings:** in postmarket, `main._earnings_view` (the branch with
  `reactions is None`) flags today's `amc` rows `after_close: true`.
- **Prompt rules:**
  - Premarket: "A figure whose `as_of_date` is before today is that day's
    close. Call it 'Wednesday's close', never 'today' or 'intraday'."
  - Both modes: "Data stamped before an event cannot show the reaction to it.
    An `after_close` result cannot explain today's regular session."

### 7. Code-ranked sectors (`macro.sector_rotation`)

- **Per sector:** `rank_1mo`, `rank_1d`, and `rank` (1-month return first,
  1-day as tiebreak, which is the rule the report prints).
- **Top level:**
  - `order_by_rank`;
  - `top3_1mo` and `bottom3_1mo`;
  - `leaders_1d` and `laggards_1d` (top and bottom 3);
  - `as_of_date`.
- **Prompt rules:**
  - List sectors, and fill the chart, in `order_by_rank` order.
  - Every "best", "weakest", "led" or "lagged" claim comes from these lists,
    and says which window it means: 1-day or 1-month.
  - Never re-rank.

### 8. Fear & Greed (`sentiment`, `gauges`)

- Add `score_display = floor(score + 0.5)` (half up: 35.9 → 36, 36.5 → 37).
- Add `band` on CNN's scale: 0–24 Extreme Fear, 25–44 Fear, 45–55 Neutral,
  56–75 Greed, 76–100 Extreme Greed.
- Add `points_to_neutral`: 45 − score when below 45, and 0 otherwise.
- The gauge shows `score_display`.
- **Prompt rule:** print `score_display` and `band` only. Never state another
  threshold.

## What does not change

- The report's look, its sections, the model, and the cost: one model call per
  run.
- The options-implied dashboard dials. Reconciling them with the trend dials
  is wave 1b.
- Unusual Whales data in logs, traces or artifacts: there is still none. The
  new fields stay in the in-memory packet, and a test enforces this.

## Testing

All tests are offline and free. They follow the mock style of
`test_premarket.py` and `test_overnight.py`. Run them with the local 3.9 venv.
The parsers avoid version-specific behavior, so the 3.11 used in Actions
behaves the same.

| Fix | Tests |
|---|---|
| Roll | Continuous `[2.697, 2.817, 2.95]` with contract `[3.004, 3.032, 2.95]` → about −2.7%, `contract`, `rolled_today`, `last` 2.95. Contract `last` 1% off the continuous → withheld, `prev_close None`. Contract last bar a day older → withheld. Expired `expireDate` → withheld. `.info` raises → metadata fallback used. Both sources fail → withheld. A 0.4% prior-close gap (SI-like) still uses the contract's prior close. A non-roll day → unchanged. |
| RSI | A hand-computed Wilder value on a fixed vector. A 250-close fixture within 0.1 of pandas `ewm(alpha=1/14, adjust=False)`. `rsi_zone` at 69.9, 70.0, 30.0, 30.1. `gauges` applies its overbought fade at exactly 70.0. |
| 52-week | (a) 260 business days, maximum at day 30 (about 322 days back) → that maximum. A 126-row tail would miss it, which is the audited bug. (b) 300 business days, maximum at day 5 → excluded; the next-highest close inside the cut is used. (c) A 270-business-day 5-day-week (FX-like) series, minimum at day 5 → excluded; at day 15 → included. (d) A 400-day 7-day series → the 365-day cut, not 252 rows. (e) Short history → `range_note`. |
| NDX | Every technicals key read by `render_dashboard`, `render_index_bias_dials` and `derive_sidecar` exists in `config.INDICES`. A packet keyed `'Nasdaq 100'` never falls back to 0 or 50. The prompt names both indices in both modes. |
| ET | UTC on 10/29: 12:30 → "8:30 AM", 14:30 → "10:30 AM", 17:00 → "1:00 PM", 18:00 → "2:00 PM". `2026-10-30 16:30` with now 10/29 → not today. `2026-11-05 13:30` → "8:30 AM" (after DST). `'2026-10-29 00:00:00'` → `time_et None`, `date_et 2026-10-29`, today. Finnhub `{'time': '2026-10-29 14:30:00'}` → "10:30 AM". `[]` → `[]`; an error dict → `None`; `today_count`. UW-shaped `…20:30:00Z`, `…20:30:00.12345Z`, `…+00:00` and an epoch-ms value → "4:30 PM", with fixtures containing no real UW values. A run under `TZ=America/Chicago` gives the same results. A Friday 10/30 4:30 PM run marks Mon 11/02 rows `is_next_session_et`. Clock frozen at 2026-10-30 01:00 UTC: `collect_data` passes `date(2026,10,29)` to `macro.collect` and `fmp.collect`. |
| As-of | An index whose last bar is 10/28, at 8:40 AM on 10/29 → `as_of_date` 10/28, `bar_complete` True. A futures bar dated 10/29 → `bar_complete` False plus `as_of_et`. `options_intel` stamp: premarket says "2026-10-28 regular-session close"; postmarket says "today's session". Postmarket `amc` row dated today → `after_close`. |
| Sectors | With 10/28 closes, the rank order is XLE, XLP, XLU, XLV, XLB, XLY, XLF, XLRE, XLI, XLK, XLC. Top and bottom lists match. |
| Fear & Greed | 35.9 → 36, 36.5 → 37, band(35.9) is "Fear", `points_to_neutral` is 9.1. |
| Public repo | A packet with the new `_et`, `as_of` and `options_intel` stamps, passed through `dump_trace`, has none of those keys and no `institutional` or `fed` key in its JSON. |
| Prompt | Each new rule is present in the right mode or modes (`test_prompt_rules` style). |

The existing 69 tests must still pass.

**Free live check** (collectors only, no model):

- With closes cut to 10/28:
  - Dow RSI is 55.8 ± 0.2;
  - NDX close is 29,813 ± 1;
  - VIX 52-week range is 13.47 / 31.05.
- On current data:
  - RSI matches a pandas `ewm` reference on the same closes;
  - NDX `last` is within 0.5% of the live ^NDX quote;
  - each of the 9 futures shows a contract and either a change or `roll_note`.

**Paid preview:** one Pre-Market preview run (`preview=true`, one model call)
on the unlisted preview page, for the owner to compare with the live report. A
Wrap preview runs only if the owner asks.

## Rollout

1. Build on branch `feat/mwtc-data-accuracy-1a`, tests first.
2. Run the free live check.
3. Run one paid preview and send the owner the link.
4. Merge only after the owner's OK, outside run windows: not 8:30–9:00 AM ET
   and not 4:20–4:50 PM ET.
5. Check the first live run against the audit's cases.

## Risks

- **Throttled Yahoo calls.** The metadata fallback covers most cases. If both
  sources fail, that run withholds the futures changes. Prices and the report
  still publish.
- **Dial shifts.** The RSI fix moves the trend dials slightly. That is expected,
  and they become correct.
- **Report wording changes.** Readers see "Nasdaq-100 (NDX)" and "Nasdaq
  Composite" spelled out, sectors listed in a fixed rank order, and RSI words
  limited to three labels.
- **Futures 52-week ranges.** They still come from the continuous series. They
  are labeled, and no "% from high" is computed for futures.
