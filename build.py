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

def call_model(client: Anthropic, master_prompt: str, sector: str) -> str:
    """Run the master prompt for ONE sector with live web search enabled.

    The master prompt is sent as its own content block with prompt caching on,
    so after the first sector it is served from cache (it's identical every
    time) — only the short 'SECTOR: <name>' line changes per call.
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
                # The only part that changes per sector.
                {"type": "text", "text": f"\n\nSECTOR: {sector}"},
            ],
        }],
    )
    # Stitch together the plain-text blocks (skip web-search tool blocks).
    text = "".join(
        block.text for block in resp.content
        if getattr(block, "type", None) == "text"
    )
    if not text.strip():
        raise ValueError("model returned no text")
    # If the model ran out of room, the report was cut off mid-stream and the
    # JSON sidecar (which comes last) never made it. Say so plainly — the fix is
    # to raise MAX_TOKENS, not to chase a phantom parse bug.
    if getattr(resp, "stop_reason", None) == "max_tokens":
        raise ValueError(
            f"report was truncated at MAX_TOKENS={MAX_TOKENS} "
            "(raise MAX_TOKENS so the full report + sidecar fit)")
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Parsing the model's reply: report HTML + JSON sidecar
# ─────────────────────────────────────────────────────────────────────────────

SIDECAR_KEYS = ["sector", "date", "direction_score", "label", "conviction",
                "tldr", "top_stocks"]
VALID_LABELS = {"BULLISH", "BEARISH", "NEUTRAL"}
VALID_CONVICTION = {"High", "Medium", "Low"}


def extract_parts(text: str):
    """Split the reply into (report_html, sidecar_dict).

    The model emits the HTML report first, then a fenced ```json block last.
    We grab the LAST json fence as the sidecar and treat everything before it as
    the report HTML (stripping an optional wrapping ```html fence)."""
    fences = list(re.finditer(r"```json\s*(.*?)```", text, re.DOTALL))
    if not fences:
        raise ValueError("no ```json sidecar block found in model output")
    last = fences[-1]
    sidecar = json.loads(last.group(1).strip())

    body = text[:last.start()].strip()
    # If the model wrapped the report in ```html ... ``` fences, unwrap it.
    body = re.sub(r"^```html\s*\n?", "", body)
    body = re.sub(r"\n?```\s*$", "", body).strip()
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

def build_sector(client: Anthropic, master_prompt: str, sector: str) -> bool:
    """Build one sector. Returns True on success, False if it was skipped
    (in which case yesterday's files are left exactly as they were)."""
    slug = slugify(sector)
    print(f"\n=== {sector}  ({slug}) ===")

    # 1) Ask the model. Any failure here -> skip, keep yesterday's report.
    try:
        text = call_model(client, master_prompt, sector)
        body, sidecar = extract_parts(text)
        validate_sidecar(sidecar)
    except Exception as e:
        print(f"  !! SKIPPED — {e}")
        print(f"  !! Keeping yesterday's report for '{sector}'.")
        return False

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
    return True


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
    succeeded = 0
    for sector in sectors:
        if build_sector(client, master_prompt, sector):
            succeeded += 1

    # Always rebuild the hub from whatever reports exist (including ones kept
    # from previous runs for skipped sectors).
    build_hub(sectors)

    print(f"\nDone: {succeeded}/{len(sectors)} sector(s) refreshed this run.")
    # If literally nothing succeeded AND we had no prior site, fail so the
    # operator notices. Otherwise exit cleanly (a partial run still publishes
    # yesterday's good reports for the skipped sectors).
    if succeeded == 0 and not any(
        (SITE / slugify(s) / "index.html").exists() for s in sectors
    ):
        print("ERROR: no reports were produced and there is no prior site.")
        sys.exit(1)


if __name__ == "__main__":
    main()
