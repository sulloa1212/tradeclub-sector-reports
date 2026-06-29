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

import os
import sys
import json
import html as _html
import argparse
import datetime as dt
import urllib.request
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


def _previous_reports_widget(slug: str, dates: list) -> str:
    """Floating 'Previous reports' dropdown (pure CSS) linking each kept dated
    copy. Mirrors build.py so MWTC reports match the registry reports."""
    if not dates:
        return ""
    items = "".join(f'<li><a href="/{slug}/{d}.html">{d}</a></li>' for d in dates)
    return (
        '<style>.tc-prev{position:fixed;left:16px;bottom:60px;z-index:99999;'
        'font:600 13px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}'
        '.tc-prev>summary{list-style:none;cursor:pointer;display:inline-flex;align-items:center;gap:7px;'
        'padding:9px 15px;border-radius:999px;background:#161b24;border:1px solid #2a3340;color:#e8eef3;'
        'box-shadow:0 3px 14px rgba(0,0,0,.55)}'
        '.tc-prev>summary::-webkit-details-marker{display:none}'
        '.tc-prev[open]>summary{border-color:#29b6f6;color:#fff}'
        '.tc-prev ul{position:absolute;left:0;bottom:calc(100% + 8px);margin:0;padding:6px;list-style:none;'
        'background:#161b24;border:1px solid #2a3340;border-radius:10px;min-width:190px;max-height:50vh;'
        'overflow:auto;box-shadow:0 12px 32px rgba(0,0,0,.55)}'
        '.tc-prev li a{display:block;padding:8px 12px;border-radius:7px;color:#e8eef3;text-decoration:none;'
        'font-variant-numeric:tabular-nums}'
        '.tc-prev li a:hover{background:#0f1830;color:#fff}'
        '@media print{.tc-prev{display:none}}</style>'
        '<details class="tc-prev"><summary>&#128336;&nbsp;Previous reports</summary>'
        f'<ul>{items}</ul></details>'
    )


def inject_previous_reports(html: str, slug: str, dates: list) -> str:
    widget = _previous_reports_widget(slug, dates)
    if not widget or "tc-prev" in html:
        return html
    low = html.lower()
    i = low.find("<body")
    if i == -1:
        return widget + html
    j = html.find(">", i)
    return html[:j + 1] + widget + html[j + 1:] if j != -1 else widget + html


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
    # Add the "previous reports" dropdown (other kept dates), then finalize.
    final = inject_previous_reports(body, slug, [x for x in keep if x != date])
    (d / f"{date}.html").write_text(final, encoding="utf-8")
    (d / "index.html").write_text(final, encoding="utf-8")
    return d / f"{date}.html"


# --- Telegram notification (stdlib only; reuses the repo's TELEGRAM_* / SITE_URL) -
PRICING = {  # per-million-token list prices ($)
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},
    "claude-opus-4-8": {"in": 15.0, "out": 75.0},
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0},
}
_DOT = {"bull": "🟢", "bear": "🔴", "neutral": "⚪", "warn": "🟠"}


def _est_cost() -> float:
    """Estimate the Anthropic cost from the usage stashed on generator.generate
    (0.0 if unavailable, e.g. a stub run)."""
    u = getattr(generator.generate, "last_usage", None)
    if u is None:
        return 0.0
    rate = PRICING.get(config.ANTHROPIC_MODEL, PRICING["claude-sonnet-4-6"])
    cin = (getattr(u, "input_tokens", 0) or 0) / 1e6 * rate["in"]
    cout = (getattr(u, "output_tokens", 0) or 0) / 1e6 * rate["out"]
    return round(cin + cout, 4)


def _telegram_message(sidecar: dict, mode: str, cost: float) -> str:
    e = _html.escape
    site = (os.environ.get("SITE_URL") or "").rstrip("/")
    lines = [
        f"<b>🧠 {e(NAME[mode])} is live</b>",
        f"{e(str(sidecar.get('date', '')))} · est. cost ${cost:.2f}",
        "",
        f"{_DOT.get(sidecar.get('accent'), '⚪')} <b>{e(str(sidecar.get('status_label', '')))}</b>",
    ]
    if sidecar.get("headline"):
        lines.append(f"   <i>{e(str(sidecar['headline']))}</i>")
    if site:
        lines += ["", f'<a href="{e(site)}/{SLUG[mode]}/">Open the report →</a>']
    return "\n".join(lines)


def _send_telegram(token: str, chat_id: str, text: str) -> None:
    payload = json.dumps({"chat_id": chat_id, "text": text,
                          "parse_mode": "HTML", "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def notify(sidecar: dict, mode: str) -> None:
    """Send one Telegram message that the report is live (non-fatal). Uses the
    same TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secrets as the other reports."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("  .. no Telegram credentials — skipping notification.")
        return
    text = _telegram_message(sidecar, mode, _est_cost())
    ids = [c.strip() for c in chat.split(",") if c.strip()]
    for cid in ids:
        _send_telegram(token, cid, text)
    print(f"  .. Telegram notification sent to {len(ids)} chat(s).")


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
    if not stub:  # don't notify on a stub/test render
        try:
            notify(sidecar, mode)
        except Exception as e:
            print(f"  !! notification failed (non-fatal) — {e}")
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
