# Multi-Sector Swing-Trade Report Site

A fully-automated static site. Every weekday pre-market, a GitHub Action runs
`build.py`, which calls the Anthropic API once per sector (with live web search),
gets back a finished HTML report + a small JSON sidecar, archives it, prunes old
copies, rebuilds the hub, and commits. Cloudflare Pages then publishes `site/`.
No human is in the loop.

This is a standalone project — it does not touch `sniper-dashboard`.

## Files you might edit

| File | What it controls |
|---|---|
| **`sectors.json`** | The list of sectors. **Edit this one file to change what gets reported.** |
| `build.py` (top) | Config knobs: `MODEL` (Sonnet ↔ Opus), `KEEP` (archive depth = 5), `MAX_SEARCHES` (web-search cap = 15), `MAX_TOKENS`, `EMAIL_TO` (notification recipients), `PRICING` (cost-report rates). |
| `master_prompt.md` | The swing-trade master prompt + the required JSON sidecar instruction. |
| `templates/hub.html.j2` | The landing page (sector cards). |
| `templates/report.html.j2` | The navigation bar injected into each report. |

Slugs are the lowercased sector name with spaces → hyphens
(e.g. `"Health Care"` → `health-care`). Reports live at `/<slug>/`.

## How a report is produced

1. `build.py` sends `master_prompt.md` + a final `SECTOR: <name>` line to the
   model, with web search enabled (capped at `MAX_SEARCHES`) and prompt caching
   on the master-prompt block (cached after the first sector).
2. The model returns a self-contained HTML report **plus** a fenced ```json
   sidecar (`direction_score`, `label`, `conviction`, `tldr`, `top_stocks`).
3. `build.py` saves `site/<slug>/<today>.html`, copies it to
   `site/<slug>/index.html`, keeps the newest 5 dated files, rewrites
   `site/<slug>/index.json`, and injects the nav bar (hub link + "previous
   reports" menu) into every kept report.
4. After all sectors, it rebuilds `site/index.html` (the hub) from each
   sector's latest sidecar entry.

If a sector's API call fails or the sidecar is missing/invalid, that sector is
**skipped** and yesterday's files are left untouched — a broken report never
gets published.

## Cost report + notification

Every run prints a **cost report** to the Actions log — per-sector token usage,
web-search count, and estimated dollar cost (including any retries), plus a
daily total. Costs are estimated from the API `usage` at the list prices in the
`PRICING` table at the top of `build.py`.

When the reports are published, `build.py` sends **one notification** with: which
sectors refreshed vs. kept yesterday's, each sector's call + TL;DR, a link to the
reports, and today's full cost breakdown. The channel is chosen by which
credentials are set (the build still succeeds if none are):

1. **Telegram** (preferred — needs no domain or DNS): set `TELEGRAM_BOT_TOKEN`
   and `TELEGRAM_CHAT_ID`.
2. **Email via Resend** (fallback — needs a DNS-verified sending domain): set
   `RESEND_API_KEY`; recipients are `EMAIL_TO` at the top of `build.py`.

## Run it locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...
python build.py
# open site/index.html
```

## Deploy checklist (do this once, with a human)

1. Push this repo to a **new** GitHub repository.
2. Add `ANTHROPIC_API_KEY` as a GitHub Actions **secret**
   (Settings → Secrets and variables → Actions).
3. Enable **web search** in the Anthropic / Claude Console (admin toggle).
4. Create a **new Cloudflare Pages** project connected to the repo, with the
   build output directory set to **`site/`** (no build command needed — the
   Action commits the built files).
5. Point a **cron-job.org** job at this workflow's `workflow_dispatch` API
   endpoint, weekdays pre-market (same fine-grained-PAT approach as Sniper):
   `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/build.yml/dispatches`
   with body `{"ref":"main"}`.
6. **Set up notifications — Telegram (recommended, no DNS needed):**
   - In Telegram, message **@BotFather**, send `/newbot`, follow the prompts, and
     copy the **bot token** it gives you.
   - Decide who gets notified. Easiest: create a Telegram **group**, add your bot
     to it, and have each person join. (Or notify people individually.)
   - Get the **chat id**: send any message in that group (or DM the bot), then open
     `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the
     `chat.id` (group ids are negative, e.g. `-1001234567890`).
   - Add the GitHub **secret** `TELEGRAM_BOT_TOKEN`, and the repo **Variable**
     `TELEGRAM_CHAT_ID` (one id, or several comma-separated for multiple chats).
   - Add the repo **Variable** `SITE_URL` (your Cloudflare Pages URL, e.g.
     `https://tradeclub-sector-reports.pages.dev`) so the message links work.

   *(Alternative — email via Resend, only if you have DNS access:* verify a
   sending domain in [resend.com](https://resend.com), add the `RESEND_API_KEY`
   secret and the `EMAIL_FROM` variable; recipients are `EMAIL_TO` in `build.py`.)*
7. Run once manually (Actions → "Build sector reports" → Run workflow) and
   confirm the hub renders, a sector report opens, the cost report appears in the
   log, and the notification arrives.
