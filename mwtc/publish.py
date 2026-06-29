"""Publish a MWTC report into the Trade Club AI reports hub.

Runs the bot for one mode, then writes the finished HTML to site/<slug>/ (the
same place the other registry reports live) plus a sidecar so the hub card picks
it up. premarket -> site/pre-market/ ; postmarket -> site/market-wrap/.

  python -m mwtc.publish --mode premarket          # real run (needs ANTHROPIC_API_KEY)
  python -m mwtc.publish --mode premarket --stub    # offline: placeholder HTML, no API
  python -m mwtc.publish --mode premarket --dry-run  # collect + print the sidecar only

The shared hub itself is rebuilt separately by `python build.py --rebuild-hub`.
"""
from __future__ import annotations

import sys
import json
import argparse
import datetime as dt
from pathlib import Path

from . import config, main as mwtc_main
from .report import generator, gauges

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE = REPO_ROOT / "site"
KEEP = 5  # dated copies kept per slug (matches the rest of the site)

SLUG = {"premarket": "pre-market", "postmarket": "market-wrap"}
NAME = {"premarket": "Pre-Market Report", "postmarket": "Market Wrap Report"}

# The same fixed "All Reports" hub button the other reports carry (links to "/").
HUB_BUTTON = (
    '<style>.tc-hub-btn{position:fixed;left:16px;bottom:16px;z-index:99999;'
    'display:inline-flex;align-items:center;gap:7px;padding:9px 15px;border-radius:999px;'
    'background:#161b24;border:1px solid #2a3340;color:#e8eef3;'
    'font:600 13px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;'
    'text-decoration:none;box-shadow:0 3px 14px rgba(0,0,0,.55)}'
    '.tc-hub-btn:hover{border-color:#29b6f6;color:#fff}'
    '@media print{.tc-hub-btn{display:none}}</style>'
    '<a class="tc-hub-btn" href="/" aria-label="All Trade Club AI reports">&#8962;&nbsp;All Reports</a>'
)

STUB_HTML = ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>MWTC stub</title>"
             "</head><body><h1>MWTC stub report</h1><p>Placeholder for offline "
             "publish-path validation (no Anthropic call).</p></body></html>")


def utc_date() -> str:
    """UTC date, matching today_str() in build.py and the site's dated files."""
    return dt.datetime.utcnow().strftime("%Y-%m-%d")


def inject_hub_button(html: str) -> str:
    if "tc-hub-btn" in html:
        return html
    low = html.lower()
    i = low.find("<body")
    if i == -1:
        return HUB_BUTTON + html
    j = html.find(">", i)
    return html[:j + 1] + HUB_BUTTON + html[j + 1:] if j != -1 else HUB_BUTTON + html


def derive_sidecar(data: dict, mode: str) -> dict:
    """Build the hub sidecar from the data packet, reusing the report's own
    directional-bias heuristic so the card matches the in-report dials. Works
    from the free macro technicals even when the UW layer is absent."""
    tech = (data.get("macro") or {}).get("technicals") or {}
    vix = gauges._vix(data)
    keys = ["S&P 500", "Nasdaq Composite", "Dow Jones", "Russell 2000"]
    biases = [gauges.directional_bias(tech.get(k), vix) for k in keys if tech.get(k)]
    value = int(round(sum(biases) / len(biases))) if biases else 0

    if vix is not None and vix >= 25:
        accent = "warn"
    elif value >= 10:
        accent = "bull"
    elif value <= -10:
        accent = "bear"
    else:
        accent = "neutral"

    word = "BULLISH" if value >= 10 else "BEARISH" if value <= -10 else "MIXED"
    label = "PRE-MARKET" if mode == "premarket" else "MARKET WRAP"
    status_label = f"{label} · {word} LEAN"

    vix_txt = f", VIX ~{vix:.0f}" if isinstance(vix, (int, float)) else ""
    if mode == "premarket":
        headline = f"Pre-market setup — {word.lower()} lean across the major indexes{vix_txt}."
    else:
        headline = f"Closing recap — {word.lower()} tone across the major indexes{vix_txt}."

    return {
        "report": NAME[mode],
        "date": utc_date(),
        "status_label": status_label,
        "accent": accent,
        "headline": headline,
        "metric": {"type": "gauge", "value": value, "min": -100, "max": 100},
    }


def publish_html(html: str, data: dict, mode: str) -> Path:
    """Write the report + sidecar into site/<slug>/, prune to KEEP, refresh
    index.html. Returns the dated file path."""
    slug = SLUG[mode]
    d = SITE / slug
    d.mkdir(parents=True, exist_ok=True)
    date = utc_date()

    body = inject_hub_button(html)
    (d / f"{date}.html").write_text(body, encoding="utf-8")

    index_path = d / "index.json"
    meta = {}
    if index_path.exists():
        try:
            for e in json.loads(index_path.read_text(encoding="utf-8")):
                if "date" in e:
                    meta[e["date"]] = e
        except Exception:
            meta = {}
    meta[date] = derive_sidecar(data, mode)

    import re
    DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    dated = sorted((p.stem for p in d.glob("*.html") if DATE_RE.match(p.stem)), reverse=True)
    keep = dated[:KEEP]
    for old in dated[KEEP:]:
        (d / f"{old}.html").unlink(missing_ok=True)
        meta.pop(old, None)
    kept = [meta[x] for x in keep if x in meta]
    index_path.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding="utf-8")
    (d / "index.html").write_text((d / f"{date}.html").read_text(encoding="utf-8"), encoding="utf-8")
    return d / f"{date}.html"


def run(mode: str, dry_run: bool = False, stub: bool = False) -> int:
    data = mwtc_main.collect_data()
    data["mode"] = mode
    sidecar = derive_sidecar(data, mode)
    print("sidecar:", json.dumps(sidecar, ensure_ascii=False))

    if dry_run:
        print("DRY RUN — collected data + derived sidecar; no report written.")
        return 0

    if stub:
        html = STUB_HTML
    else:
        html = generator.generate(data, data["report_date"], mode=mode)

    out = publish_html(html, data, mode)
    print(f"Published {NAME[mode]} -> {out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["premarket", "postmarket"],
                    default=config.REPORT_MODE or "premarket")
    ap.add_argument("--dry-run", action="store_true",
                    help="Collect data + print the sidecar; write nothing (no Anthropic call).")
    ap.add_argument("--stub", action="store_true",
                    help="Use placeholder HTML instead of calling Anthropic (publish-path test).")
    a = ap.parse_args(argv)
    return run(a.mode, dry_run=a.dry_run, stub=a.stub)


if __name__ == "__main__":
    sys.exit(main())
