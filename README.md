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
| `build.py` (top) | Config knobs: `MODEL` (Sonnet ↔ Opus), `KEEP` (archive depth = 5), `MAX_SEARCHES` (web-search cap = 15), `MAX_TOKENS`. |
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
6. Run once manually (Actions → "Build sector reports" → Run workflow) and
   confirm the hub and one sector render correctly.
