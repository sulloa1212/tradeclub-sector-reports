# Automatic retry for failed report runs — design

Date: 2026-10-11. Status: draft for the owner's review. Nothing in this
document is built yet.

## Goal

When a report run fails, it starts again by itself and the owner is told. No
one should have to notice a missing report hours later and ask for a re-run.

It must never publish a report twice, loop without end, or spend money
without a limit.

## Background

- Five reports run on weekdays from two GitHub workflows:
  `report.yml` ("Build report(s) (manual / scheduled)": Gap Scout, Sector
  Intelligence, Mean Reversion) and `mwtc.yml` ("Build MWTC report
  (Pre-Market / Market Wrap)").
- Each workflow failed outright 4 times in its last 100 runs. Causes vary: an
  unusable AI reply, an API outage, an install problem.
- On 2026-10-09 the 9:00 AM Gap Scout failed and nothing retried it. The
  owner found out at 1:37 PM. No alert was sent.
- The GitHub `schedule` backups in `report.yml` have been starting about five
  hours late, so they do not rescue a failed morning run in time.
- Both workflows already refuse to build a report that exists for today (the
  "already built" guards). A retry goes through the same guards.

## What it does

A new workflow, `.github/workflows/retry-failed.yml`, runs whenever a run of
either report workflow finishes.

1. It ignores the run unless all of these hold:
   - the run was on `main`;
   - it ended as `failure` or `timed_out` (not cancelled, not success);
     a `timed_out` run gets the "gave up" alert and no retry (see
     "Decisions");
   - its title does not mark it as a preview or a key check (see "Run
     titles").
2. It reads the failed run's steps. If the step "Commit & push the rebuilt
   site (race-safe)" succeeded, the report is already published and only a
   later step failed (for example the health check-in). It sends one alert
   and does **not** retry, because a retry would rebuild and bill again.
3. Otherwise it looks at the attempt number GitHub keeps for the run:
   - attempt 1 or 2: send the "failed, retrying" alert, wait 3 minutes,
     re-run the same run (same inputs, same version of the code);
   - attempt 3: send the "gave up" alert and stop.

So one failed run gets at most 2 retries. When a retry itself fails, GitHub
reports that attempt as finished and the same workflow handles it again with
the next attempt number.

## Decisions

| Decision | Value | Why |
|---|---|---|
| Retries per failed run | 2 (3 attempts in total) | Covers temporary problems; bounds the cost on a day when every attempt fails |
| Wait before a retry | 3 minutes | Lets an API outage or a bad network moment pass |
| Outcomes retried | `failure` only | A failure shows within minutes, so both retries finish about 20 minutes after the scheduled time. A run that hit the 60-minute timeout would retry after the market opens, and a late "Pre-Market" report misleads; it gets an alert instead. A cancelled run was stopped on purpose |
| Branch | `main` only | Preview runs on feature branches are manual and paid |
| Alerts | Telegram, the chat that gets "report is live" | The owner confirmed on 2026-10-11 that only they see that chat |
| How the retry starts | Re-run of the failed run | Keeps the exact inputs (report, window, mode) with no extra plumbing |

## Components

### 1. `retry-failed.yml` (new)

- Trigger: `workflow_run`, `types: [completed]`, for the two report
  workflows by name.
- Permissions: `actions: write` (to re-run a run) and nothing else. It does
  not check out the repository and cannot push.
- Secrets used: `TELEGRAM_BOT_TOKEN` (secret) and `TELEGRAM_CHAT_ID`
  (variable), both already used by the report workflows.
- One job: decide → alert → wait → `gh run rerun <run id>`. Each report
  workflow has a single job, so re-running the run re-runs everything, and it
  also covers a run that failed before any step started.
- `concurrency: retry-<run id>` so two events for the same run never act at
  the same time.
- If the Telegram call fails, the retry still happens. The alert is not
  allowed to block the retry.

### 2. Run titles (two one-line edits)

Add a `run-name` line to `report.yml` and `mwtc.yml`. It only changes how
a run is titled in GitHub; it does not change what the run does.

- `report.yml`: the report list, the window when set, and "PREVIEW" for a
  preview run. Example: `gap-risk · pm`, `sector-intelligence,mean-reversion`,
  `gap-risk · PREVIEW`. A scheduled backup run has no inputs and is titled
  `gap-risk · backup`.
- `mwtc.yml`: the mode. Example: `MWTC premarket`, `MWTC check-keys`.

The retry workflow uses the title for two things: naming the report in the
alert, and skipping runs titled PREVIEW or check-keys.

### 3. `retry-selftest.yml` (new, kept)

A manual, $0 workflow with three modes, used to prove the retry works and
kept for re-checking it later:

- `fail-once`: fails on attempt 1 and passes on attempt 2.
- `always-fail`: fails every time.
- `fail-after-publish`: a step with the exact publish-step name succeeds,
  then a later step fails.

It builds nothing, calls no paid service and touches no report.

## Alerts

All start with the report name from the run title.

- Retrying: `⚠️ gap-risk · pm failed (attempt 1 of 3). Retrying in 3 minutes.`
  plus a link to the failed run.
- Gave up: `🛑 gap-risk · pm failed 3 times. No report was published. Needs a
  look.` plus the link. A timed-out run gets the same alert, worded "timed
  out after 60 minutes. Not retried."
- Published but failed later: `ℹ️ gap-risk · pm was published, but a later
  step failed (no retry needed).` plus the link.
- A retry that succeeds sends nothing extra: the normal "report is live"
  message arrives as usual.

Nothing is sent on a normal day.

## Safety

- **No double publishing or double billing.** A retry runs the workflow's own
  "already built" guard first. The publish-step check (step 2 above) covers
  the one case that guard cannot see: a run that failed after it had pushed
  the report.
- **No endless loop.** The cap uses GitHub's own attempt counter, which the
  workflow cannot change.
- **Cost is bounded.** A normal day costs $0. One incident costs at most two
  extra attempts: about $0.70–$1.40 for Gap Scout or the sector reports,
  about $2.50–$3 for Pre-Market or Market Wrap. At the current failure rate
  that is roughly $1–3 a month.
- **Small permissions.** The retry workflow can re-run runs and send a
  Telegram message. It cannot edit the site or the code.

## What it does not cover

- **A real bug.** A re-run uses the same code as the failed run, so it fixes
  temporary problems only. The cap and the "gave up" alert are for this case.
- **A run that never starts** (the scheduler's call to GitHub fails, as on
  2026-08-06). That needs a separate timed check about 15 minutes after each
  report. It is a possible follow-up and is not part of this design.
- **A failure of the retry workflow itself.** It would show as a failed run
  in GitHub and nothing else. It is small enough that this is accepted.
- **A manual run squeezed into the 3-minute wait.** If someone publishes the
  same report by hand while a retry is waiting, the retry can build it a
  second time (one extra paid run, the later page wins). Accepted as rare.

## Risk to verify first

GitHub does not start new workflows from most actions taken with a
workflow's own token. The design relies on GitHub still reporting a re-run's
completion to `retry-failed.yml`, so that a failed retry gets its second
retry. The `always-fail` self-test checks exactly this.

If GitHub does not report it, the fallback is to keep both retries inside
one retry job: re-run, wait for the result, and re-run once more if it failed
again. The behaviour the owner sees stays the same.

## Rollout and test plan

`workflow_run` only fires for a workflow file that is on `main`, so the
retry cannot be tested on a branch. It goes to `main` in two stages.

**Stage 1 — self-test only ($0).** Merge `retry-failed.yml` watching only
`retry-selftest.yml`, plus the self-test itself. The real report workflows
are not watched yet. Run:

| Test | Expected |
|---|---|
| `fail-once` | one "retrying" alert, one retry, the retry passes, no "gave up" |
| `always-fail` | two "retrying" alerts, two retries, then one "gave up"; no fourth attempt |
| `fail-after-publish` | one "published, but a later step failed" alert, no retry |
| a self-test run that passes | no alert, no retry |
| a failed self-test run on a branch other than `main` | ignored |

The owner will receive about five Telegram messages marked TEST during this
stage.

**Stage 2 — the real reports.** After the owner has seen the stage 1
results and approved: add the two report workflows to the watch list and add
the `run-name` lines. Check that the next scheduled runs are titled
correctly and trigger nothing when they succeed. The first real failure
after that is watched by hand.

## Files touched

| File | Change |
|---|---|
| `.github/workflows/retry-failed.yml` | new |
| `.github/workflows/retry-selftest.yml` | new |
| `.github/workflows/report.yml` | one `run-name` line |
| `.github/workflows/mwtc.yml` | one `run-name` line |

No change to `build.py`, the engines, the prompts or the site.
