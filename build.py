#!/usr/bin/env python3
"""
build.py — Multi-Sector Swing-Trade Report Site builder
=========================================================

What this does, in plain English:
  For each sector listed in `sectors.json`, it asks the Anthropic API (with live
  web search turned on) to run the swing-trade master prompt for that one sector.
  The model hands back a finished, self-contained HTML report plus a small JSON
  "sidecar" (score / label / TL;DR / top tickers). This script then:

    1. saves the report as site/<slug>/<today>.html
    2. copies it to site/<slug>/index.html  (so the folder root = latest report)
    3. keeps only the newest KEEP (=5) dated reports, deleting older ones
    4. rewrites site/<slug>/index.json with metadata for the kept reports
    5. drops a small navigation bar (back-to-hub link + "previous reports" menu)
       into every kept report
  Finally it rebuilds site/index.html (the hub) from every sector's latest entry.

Cloudflare Pages serves the `site/` folder, so once GitHub Actions commits the
changes the site updates automatically. No human is in the loop.

Safety rule: if a sector's API call fails or returns no valid sidecar, that
sector is SKIPPED and yesterday's files are left untouched — we never publish a
broken or empty page.

Requires the environment variable ANTHROPIC_API_KEY.
"""

import json
import os
import re
import sys
import datetime
import urllib.request
from pathlib import Path

import pandas_market_calendars as mcal
from anthropic import Anthropic
from jinja2 import Environment, FileSystemLoader, select_autoescape


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG KNOBS — the things you are most likely to change live here.
# ─────────────────────────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"   # the model that writes the reports.
                              # Swap to "claude-opus-4-8" for higher quality at
                              # higher cost — just change this one line.

KEEP = 5                      # how many dated reports to keep per sector.
MAX_SEARCHES = 15             # max web searches the model may run per sector
                              # (caps cost). Raise/lower as needed.
MAX_TOKENS = 32000            # max length of the report the model may write.
                              # Reports are large (full HTML + SVG). Too low and
                              # the model gets cut off before the JSON sidecar,
                              # which makes the sector skip. Sonnet 4.6 allows up
                              # to 64000 — raise this if sectors still truncate.

# Sectors are loaded from sectors.json so you can edit that ONE file to change
# what gets reported. (Edit sectors.json, not this list.)

# --- Email notification (sent via Resend when the reports are published) ---
EMAIL_TO = ["mike@mwtradecoach.com", "sulloa@treelink.lat"]   # who gets notified
EMAIL_FROM = (os.environ.get("EMAIL_FROM")
              or "MW Trade AI Reports <reports@mwtradecoach.com>")
# ^ the "from" address MUST be on a domain you've verified in Resend. Set it via
#   the EMAIL_FROM repo variable once your domain is verified.
SITE_URL = (os.environ.get("SITE_URL") or "").rstrip("/")     # public hub URL,
#   e.g. https://tradeclub-sector-reports.pages.dev — set via the SITE_URL repo
#   variable so the email can link to the reports. Empty = links omitted.
# RESEND_API_KEY is read from the environment (a GitHub secret). If it is unset,
# the email step is skipped (so local test runs don't error).

# --- Cost estimation (drives the per-run cost report + the email) ---
# Dollars per 1,000,000 tokens. cache_write = 1.25x input, cache_read = 0.1x
# input (Anthropic prompt-cache pricing). Web search is billed separately.
PRICING = {
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-opus-4-8":   {"in": 5.0, "out": 25.0, "cache_write": 6.25, "cache_read": 0.50},
}
WEB_SEARCH_COST_PER_1K = 10.0   # $10 per 1,000 web searches


# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"                 # build output Cloudflare serves
TEMPLATES = ROOT / "templates"
MASTER_PROMPT_PATH = ROOT / "master_prompt.md"
SECTORS_PATH = ROOT / "sectors.json"

# Markers wrapping the injected navigation bar. They let us strip and re-build
# the bar cleanly on every run without touching the model's report HTML.
NAV_START = "<!--TC_NAV_START-->"
NAV_END = "<!--TC_NAV_END-->"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES)),
    autoescape=select_autoescape(["html", "xml", "j2"]),
)


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

def today_str() -> str:
    """Today's date (UTC). GitHub Actions runs in UTC; the US pre-market run
    shares the same calendar date, so this is the right day to stamp."""
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


def now_stamp() -> str:
    """Human-readable generation timestamp for the hub footer."""
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


def market_open_today() -> bool:
    """True if the NYSE holds a regular session today.

    Uses the official NYSE calendar (via pandas_market_calendars), so weekends,
    public holidays and any other non-trading day are handled automatically:
    on a closed day the schedule for that single date comes back empty."""
    today = today_str()
    schedule = mcal.get_calendar("NYSE").schedule(start_date=today, end_date=today)
    return not schedule.empty


def slugify(name: str) -> str:
    """'Health Care' -> 'health-care' (lowercase, spaces -> hyphens)."""
    return re.sub(r"\s+", "-", name.strip().lower())


def load_sectors() -> list:
    return json.loads(SECTORS_PATH.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# Talking to the model
# ─────────────────────────────────────────────────────────────────────────────

# Appended to the prompt on a retry when the first reply couldn't be parsed.
RETRY_REMINDER = (
    "REMINDER — OUTPUT FORMAT IS MANDATORY: a previous attempt could not be "
    "parsed. Produce the full HTML report, then END your reply with a single "
    "fenced ```json code block containing the sidecar object exactly as "
    "specified (keys: sector, date, direction_score, label, conviction, tldr, "
    "top_stocks). That ```json block MUST be the very last thing in your "
    "response — write nothing after its closing ```."
)


def call_model(client: Anthropic, master_prompt: str, sector: str,
               extra_instruction: str = "") -> tuple:
    """Run the master prompt for ONE sector with live web search enabled.

    Returns (text, usage, stop_reason). We return usage even when the reply is
    unusable (e.g. truncated) so the caller can still bill the tokens it cost;
    the caller decides whether the reply is good enough to use.

    The master prompt is sent as its own content block with prompt caching on,
    so after the first sector it is served from cache (it's identical every
    time) — only the short 'SECTOR: <name>' line changes per call.

    `extra_instruction`, if given, is appended after the SECTOR line (used by the
    one-shot retry to force the required format). It stays OUT of the cached
    block so prompt caching on the master prompt still applies.
    """
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": MAX_SEARCHES,
        }],
        messages=[{
            "role": "user",
            "content": [
                # Big, identical-every-time block -> cache it.
                {"type": "text", "text": master_prompt,
                 "cache_control": {"type": "ephemeral"}},
                # The only part that changes per sector (plus an optional
                # one-shot reminder on a retry).
                {"type": "text",
                 "text": f"\n\nSECTOR: {sector}"
                         + (f"\n\n{extra_instruction}" if extra_instruction else "")},
            ],
        }],
    )
    # Stitch together the plain-text blocks (skip web-search tool blocks).
    text = "".join(
        block.text for block in resp.content
        if getattr(block, "type", None) == "text"
    )
    return text, resp.usage, getattr(resp, "stop_reason", None)


# ─────────────────────────────────────────────────────────────────────────────
# Parsing the model's reply: report HTML + JSON sidecar
# ─────────────────────────────────────────────────────────────────────────────

SIDECAR_KEYS = ["sector", "date", "direction_score", "label", "conviction",
                "tldr", "top_stocks"]
VALID_LABELS = {"BULLISH", "BEARISH", "NEUTRAL"}
VALID_CONVICTION = {"High", "Medium", "Low"}


def _iter_balanced_objects(text: str):
    """Yield (start, end) character spans of every top-level ``{...}`` object in
    `text`, correctly ignoring braces that appear inside JSON string literals
    (so a brace in a "tldr" value, or a CSS ``{ }`` block, doesn't fool it).
    Top-level = brace depth returns to zero; nested objects stay part of their
    enclosing span."""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    yield (start, i + 1)
                    start = -1


def _find_sidecar(text: str):
    """Locate the JSON sidecar tolerantly and return (sidecar_dict, start_index).

    Two passes:
      1. Preferred — the LAST fenced ```json ... ``` block.
      2. Fallback — the LAST balanced top-level ``{...}`` object that parses as
         JSON (preferring one that actually looks like our sidecar). This
         rescues replies where the model dropped the fence or added trailing
         text after the JSON. CSS/JS braces are skipped automatically because
         they don't parse as JSON."""
    # Pass 1: a properly fenced json block (the normal, happy path).
    for m in reversed(list(re.finditer(r"```json\s*(.*?)```", text, re.DOTALL))):
        try:
            return json.loads(m.group(1).strip()), m.start()
        except Exception:
            break  # fence exists but is malformed -> fall through to pass 2

    # Pass 2: scan for balanced top-level objects and parse them.
    parsed = []  # list of (start_index, dict)
    for s, e in _iter_balanced_objects(text):
        try:
            obj = json.loads(text[s:e])
        except Exception:
            continue
        if isinstance(obj, dict):
            parsed.append((s, obj))
    if parsed:
        # Prefer the last object that looks like the sidecar; else the last dict.
        for s, obj in reversed(parsed):
            if "direction_score" in obj:
                return obj, s
        return parsed[-1][1], parsed[-1][0]

    raise ValueError("no parseable JSON sidecar found in model output")


def extract_parts(text: str):
    """Split the reply into (report_html, sidecar_dict).

    The model emits the HTML report first, then the JSON sidecar last. We locate
    the sidecar tolerantly (see _find_sidecar) and treat everything before it as
    the report HTML (stripping an optional wrapping ```html / ```json fence)."""
    sidecar, start = _find_sidecar(text)

    body = text[:start].strip()
    # If the model wrapped the report in ```html ... ``` fences, unwrap it, and
    # drop any dangling fence opener (```html / ```json) left right before the
    # sidecar in the fence-less fallback case.
    body = re.sub(r"^```html\s*\n?", "", body)
    body = re.sub(r"\n?```(?:json|html)?\s*$", "", body).strip()
    if not body:
        raise ValueError("report HTML was empty")
    return body, sidecar


def validate_sidecar(s: dict):
    """Fail loudly if the sidecar is missing fields or out of range."""
    missing = [k for k in SIDECAR_KEYS if k not in s]
    if missing:
        raise ValueError(f"sidecar missing fields: {missing}")
    if s["label"] not in VALID_LABELS:
        raise ValueError(f"bad label: {s['label']!r}")
    if s["conviction"] not in VALID_CONVICTION:
        raise ValueError(f"bad conviction: {s['conviction']!r}")
    score = float(s["direction_score"])
    if not (-100 <= score <= 100):
        raise ValueError(f"direction_score out of range: {score}")
    if not isinstance(s["top_stocks"], list) or not s["top_stocks"]:
        raise ValueError("top_stocks must be a non-empty list")


# ─────────────────────────────────────────────────────────────────────────────
# Cost accounting
# ─────────────────────────────────────────────────────────────────────────────

_COUNT_KEYS = ("input", "output", "cache_write", "cache_read", "web_searches")


def usage_counts(usage) -> dict:
    """Pull the token / web-search counts out of one API response's usage."""
    st = getattr(usage, "server_tool_use", None)
    return {
        "input": getattr(usage, "input_tokens", 0) or 0,
        "output": getattr(usage, "output_tokens", 0) or 0,
        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "web_searches": (getattr(st, "web_search_requests", 0) or 0) if st else 0,
    }


def add_counts(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + b.get(k, 0) for k in _COUNT_KEYS}


def cost_of(counts: dict) -> float:
    """Dollar cost for a bundle of token / web-search counts at MODEL's prices."""
    p = PRICING.get(MODEL, PRICING["claude-sonnet-4-6"])
    tokens = (counts["input"] * p["in"]
              + counts["output"] * p["out"]
              + counts["cache_write"] * p["cache_write"]
              + counts["cache_read"] * p["cache_read"]) / 1_000_000
    return tokens + counts["web_searches"] * WEB_SEARCH_COST_PER_1K / 1000


# ─────────────────────────────────────────────────────────────────────────────
# Navigation bar injection
# ─────────────────────────────────────────────────────────────────────────────

def render_nav(sector: str, slug: str, current_date: str, others: list) -> str:
    """Render the injected nav bar (templates/report.html.j2).

    `others` = the OTHER kept reports for this sector (newest first), each a
    dict with at least 'date' and 'label'."""
    tmpl = env.get_template("report.html.j2")
    return tmpl.render(sector=sector, slug=slug,
                       current_date=current_date, entries=others)


def strip_nav(html: str) -> str:
    """Remove any previously injected nav bar so we can re-insert a fresh one."""
    return re.sub(
        re.escape(NAV_START) + r".*?" + re.escape(NAV_END),
        "", html, flags=re.DOTALL,
    )


def inject_nav(html: str, nav: str) -> str:
    """Insert the nav bar right after the opening <body> tag (or at the very
    top if the document has no <body>)."""
    wrapped = f"{NAV_START}{nav}{NAV_END}"
    m = re.search(r"<body[^>]*>", html, re.IGNORECASE)
    if m:
        return html[:m.end()] + wrapped + html[m.end():]
    return wrapped + html


# ─────────────────────────────────────────────────────────────────────────────
# Per-sector build
# ─────────────────────────────────────────────────────────────────────────────

def _record(sector, slug, status, usages, attempts, sidecar=None, date=None) -> dict:
    """Assemble the per-sector result (status + cost) returned to main()."""
    counts = {k: 0 for k in _COUNT_KEYS}
    for u in usages:
        counts = add_counts(counts, usage_counts(u))
    rec = {"sector": sector, "slug": slug, "status": status,
           "attempts": attempts, "counts": counts, "cost": cost_of(counts)}
    if status == "ok" and sidecar:
        rec.update({
            "date": date,
            "label": sidecar["label"],
            "direction_score": int(round(float(sidecar["direction_score"]))),
            "conviction": sidecar["conviction"],
            "tldr": sidecar["tldr"],
        })
    return rec


def build_sector(client: Anthropic, master_prompt: str, sector: str) -> dict:
    """Build one sector. Returns a result record (status + cost). On a skip,
    yesterday's files are left exactly as they were."""
    slug = slugify(sector)
    print(f"\n=== {sector}  ({slug}) ===")

    # 1) Ask the model, parse, and validate. If anything fails, make ONE
    #    automatic retry with a forceful format reminder before giving up. Only
    #    after the retry ALSO fails do we skip and keep yesterday's report.
    #    Every call's usage is recorded (even failed/truncated ones) so the cost
    #    report counts what the run actually spent, retries included.
    usages = []

    def attempt(extra=""):
        text, usage, stop_reason = call_model(client, master_prompt, sector, extra)
        if usage is not None:
            usages.append(usage)
        if not text.strip():
            raise ValueError("model returned no text")
        if stop_reason == "max_tokens":
            raise ValueError(
                f"report was truncated at MAX_TOKENS={MAX_TOKENS} "
                "(raise MAX_TOKENS so the full report + sidecar fit)")
        body, sidecar = extract_parts(text)
        validate_sidecar(sidecar)
        return body, sidecar

    attempts = 1
    try:
        body, sidecar = attempt()
    except Exception as e:
        print(f"  !! attempt 1 failed — {e}")
        print(f"  !! retrying '{sector}' once with a stricter format reminder...")
        attempts = 2
        try:
            body, sidecar = attempt(RETRY_REMINDER)
        except Exception as e2:
            print(f"  !! SKIPPED — {e2}")
            print(f"  !! Keeping yesterday's report for '{sector}'.")
            return _record(sector, slug, "skipped", usages, attempts)
        print(f"  .. retry succeeded for '{sector}'.")

    sector_dir = SITE / slug
    sector_dir.mkdir(parents=True, exist_ok=True)

    # Use the model's date if it looks right, otherwise today's UTC date.
    today = str(sidecar.get("date", "")).strip()
    if not DATE_RE.match(today):
        today = today_str()

    # 2) Save today's raw report (nav bar is added in step 5).
    (sector_dir / f"{today}.html").write_text(body, encoding="utf-8")

    # 3) Carry forward metadata for older dates from the existing index.json,
    #    then add/replace today's entry.
    index_path = sector_dir / "index.json"
    meta_by_date = {}
    if index_path.exists():
        try:
            for e in json.loads(index_path.read_text(encoding="utf-8")):
                if "date" in e:
                    meta_by_date[e["date"]] = e
        except Exception:
            meta_by_date = {}
    meta_by_date[today] = {
        "date": today,
        "label": sidecar["label"],
        "direction_score": int(round(float(sidecar["direction_score"]))),
        "conviction": sidecar["conviction"],
        "tldr": sidecar["tldr"],
    }

    # 4) Prune: keep the newest KEEP dated files, delete the rest.
    dated = sorted(
        (p.stem for p in sector_dir.glob("*.html") if DATE_RE.match(p.stem)),
        reverse=True,
    )
    keep = dated[:KEEP]
    for old in dated[KEEP:]:
        (sector_dir / f"{old}.html").unlink(missing_ok=True)
        meta_by_date.pop(old, None)
        print(f"  pruned old report: {old}.html")

    # Write index.json (newest first) for the kept reports only.
    kept_entries = [meta_by_date[d] for d in keep if d in meta_by_date]
    index_path.write_text(
        json.dumps(kept_entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 5) (Re)build the nav bar on EVERY kept report so each dropdown is current.
    for d in keep:
        f = sector_dir / f"{d}.html"
        raw = strip_nav(f.read_text(encoding="utf-8"))
        others = [e for e in kept_entries if e["date"] != d]
        f.write_text(inject_nav(raw, render_nav(sector, slug, d, others)),
                     encoding="utf-8")

    # 6) The latest report becomes the folder's index.html.
    latest = (sector_dir / f"{today}.html").read_text(encoding="utf-8")
    (sector_dir / "index.html").write_text(latest, encoding="utf-8")

    print(f"  OK — {sidecar['label']} {meta_by_date[today]['direction_score']:+d} "
          f"({sidecar['conviction']} conviction), {len(keep)} report(s) kept")
    return _record(sector, slug, "ok", usages, attempts, sidecar, today)


# ─────────────────────────────────────────────────────────────────────────────
# Hub page
# ─────────────────────────────────────────────────────────────────────────────

def build_hub(sectors: list):
    """Rebuild site/index.html from every sector's latest index.json entry."""
    cards = []
    for sector in sectors:
        slug = slugify(sector)
        idx = SITE / slug / "index.json"
        if not idx.exists():
            continue
        try:
            entries = json.loads(idx.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not entries:
            continue
        latest = entries[0]
        cards.append({"sector": sector, "slug": slug, **latest})

    html = env.get_template("hub.html.j2").render(
        cards=cards, generated=now_stamp())
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "index.html").write_text(html, encoding="utf-8")
    print(f"\nHub rebuilt with {len(cards)} sector card(s).")


# ─────────────────────────────────────────────────────────────────────────────
# Cost report + email notification
# ─────────────────────────────────────────────────────────────────────────────

def summarize_cost(records: list) -> dict:
    """Print a per-sector + total cost table to the log; return the totals."""
    total = {k: 0 for k in _COUNT_KEYS}
    print(f"\n----- Cost report (model: {MODEL}) -----")
    print(f"  {'sector':<16}{'status':<9}{'input':>9}{'output':>8}"
          f"{'cache_rd':>10}{'web':>5}{'cost':>9}")
    for r in records:
        c = r["counts"]
        total = add_counts(total, c)
        note = f"  (x{r['attempts']})" if r["attempts"] > 1 else ""
        print(f"  {r['sector']:<16}{r['status']:<9}{c['input']:>9}{c['output']:>8}"
              f"{c['cache_read']:>10}{c['web_searches']:>5}{('$%.3f' % r['cost']):>9}{note}")
    total_cost = cost_of(total)
    print(f"  {'TOTAL':<16}{'':<9}{total['input']:>9}{total['output']:>8}"
          f"{total['cache_read']:>10}{total['web_searches']:>5}{('$%.2f' % total_cost):>9}")
    print(f"  Estimated cost for today's run: ${total_cost:.2f}")
    return {"counts": total, "cost": total_cost}


def _label_color(label: str) -> str:
    return {"BULLISH": "#16a34a", "BEARISH": "#dc2626"}.get(label, "#64748b")


def build_email_html(records: list, cost: dict, date: str) -> str:
    """Compose the notification email body (inline-styled HTML)."""
    n_ok = sum(1 for r in records if r["status"] == "ok")
    hub = f'<p><a href="{SITE_URL}/" style="color:#2563eb">Open the hub &rarr;</a></p>' if SITE_URL else ""

    sector_rows = []
    for r in records:
        if r["status"] == "ok":
            link = f'{SITE_URL}/{r["slug"]}/' if SITE_URL else ""
            name = (f'<a href="{link}" style="color:#0f172a;text-decoration:none;font-weight:600">{r["sector"]}</a>'
                    if link else f'<b>{r["sector"]}</b>')
            badge = (f'<span style="background:{_label_color(r["label"])};color:#fff;font-size:11px;'
                     f'font-weight:700;padding:2px 7px;border-radius:4px">{r["label"]}</span>')
            call = f'{badge} &nbsp;<b>{r["direction_score"]:+d}</b> &middot; {r["conviction"]}'
            tldr = r["tldr"]
        else:
            name = r["sector"]
            call = '<span style="color:#b45309">kept yesterday\'s report</span>'
            tldr = '<span style="color:#94a3b8">refresh failed &mdash; skipped</span>'
        sector_rows.append(
            f'<tr><td style="padding:8px 10px;border-bottom:1px solid #eee;vertical-align:top">{name}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #eee;vertical-align:top;white-space:nowrap">{call}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #eee;color:#475569">{tldr}</td></tr>')

    cost_rows = []
    for r in records:
        c = r["counts"]
        cost_rows.append(
            f'<tr><td style="padding:5px 10px;border-bottom:1px solid #f1f5f9">{r["sector"]}</td>'
            f'<td style="padding:5px 10px;border-bottom:1px solid #f1f5f9;text-align:right">{c["input"]:,}</td>'
            f'<td style="padding:5px 10px;border-bottom:1px solid #f1f5f9;text-align:right">{c["output"]:,}</td>'
            f'<td style="padding:5px 10px;border-bottom:1px solid #f1f5f9;text-align:right">{c["web_searches"]}</td>'
            f'<td style="padding:5px 10px;border-bottom:1px solid #f1f5f9;text-align:right">${r["cost"]:.3f}</td></tr>')
    t = cost["counts"]

    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:640px;margin:0 auto;color:#0f172a">
  <h2 style="margin:0 0 4px">Swing-Trade Sector Reports are live</h2>
  <p style="color:#64748b;margin:0 0 16px">{date} &middot; {n_ok}/{len(records)} sectors refreshed
     &middot; estimated cost <b>${cost['cost']:.2f}</b></p>
  {hub}
  <table style="border-collapse:collapse;width:100%;font-size:14px;margin:8px 0 24px">
    <thead><tr style="text-align:left;color:#64748b;font-size:12px">
      <th style="padding:6px 10px">Sector</th><th style="padding:6px 10px">Call</th><th style="padding:6px 10px">TL;DR</th>
    </tr></thead>
    <tbody>{''.join(sector_rows)}</tbody>
  </table>

  <h3 style="margin:0 0 6px;font-size:15px">Today's cost &mdash; ${cost['cost']:.2f} <span style="color:#94a3b8;font-weight:400">({MODEL})</span></h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px;color:#334155">
    <thead><tr style="text-align:right;color:#94a3b8;font-size:11px">
      <th style="padding:5px 10px;text-align:left">Sector</th><th style="padding:5px 10px">Input tok</th>
      <th style="padding:5px 10px">Output tok</th><th style="padding:5px 10px">Searches</th><th style="padding:5px 10px">Cost</th>
    </tr></thead>
    <tbody>{''.join(cost_rows)}
      <tr style="font-weight:700"><td style="padding:6px 10px">TOTAL</td>
        <td style="padding:6px 10px;text-align:right">{t['input']:,}</td>
        <td style="padding:6px 10px;text-align:right">{t['output']:,}</td>
        <td style="padding:6px 10px;text-align:right">{t['web_searches']}</td>
        <td style="padding:6px 10px;text-align:right">${cost['cost']:.2f}</td></tr>
    </tbody>
  </table>
  <p style="color:#94a3b8;font-size:12px;margin-top:18px">Cost is an estimate from token usage at list prices
     (web search billed at ${WEB_SEARCH_COST_PER_1K:.0f}/1,000). Cloudflare may take a minute to publish.
     Educational analysis, not financial advice.</p>
</div>"""


def send_email_resend(api_key: str, subject: str, html: str):
    """POST the notification to the Resend API."""
    payload = json.dumps({
        "from": EMAIL_FROM, "to": EMAIL_TO, "subject": subject, "html": html,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def notify_email(records: list, cost: dict, date: str):
    """Email the recipients that the reports are live, with today's cost."""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("  .. RESEND_API_KEY not set — skipping email notification.")
        return
    n_ok = sum(1 for r in records if r["status"] == "ok")
    subject = (f"AI Sector Reports live — {date} "
               f"({n_ok}/{len(records)} refreshed, ${cost['cost']:.2f})")
    send_email_resend(api_key, subject, build_email_html(records, cost, date))
    print(f"  .. notification email sent to {', '.join(EMAIL_TO)}.")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # Market-open guard: do nothing on weekends, holidays, or any non-trading
    # day. This runs BEFORE the API key check and any sector loop / API call,
    # so a closed day costs nothing and exits cleanly.
    if not market_open_today():
        print("US market closed today — skipping run.")
        sys.exit(0)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        sys.exit(1)

    sectors = load_sectors()
    if not sectors:
        print("ERROR: sectors.json is empty.")
        sys.exit(1)

    master_prompt = MASTER_PROMPT_PATH.read_text(encoding="utf-8")
    client = Anthropic(timeout=900)  # generous timeout: web search can be slow.

    print(f"Building {len(sectors)} sector(s) with model {MODEL}.")
    records = [build_sector(client, master_prompt, sector) for sector in sectors]
    succeeded = sum(1 for r in records if r["status"] == "ok")

    # Always rebuild the hub from whatever reports exist (including ones kept
    # from previous runs for skipped sectors).
    build_hub(sectors)

    # Per-run cost report (also drives the email).
    cost = summarize_cost(records)
    print(f"\nDone: {succeeded}/{len(sectors)} sector(s) refreshed this run.")

    have_site = any(
        (SITE / slugify(s) / "index.html").exists() for s in sectors)

    # Email the recipients that the reports are live + today's cost. Never let
    # an email problem fail the build — the reports are already published.
    if have_site:
        try:
            notify_email(records, cost, today_str())
        except Exception as e:
            print(f"  !! email notification failed (non-fatal) — {e}")

    # If literally nothing succeeded AND we had no prior site, fail so the
    # operator notices. Otherwise exit cleanly (a partial run still publishes
    # yesterday's good reports for the skipped sectors).
    if succeeded == 0 and not have_site:
        print("ERROR: no reports were produced and there is no prior site.")
        sys.exit(1)


if __name__ == "__main__":
    main()
