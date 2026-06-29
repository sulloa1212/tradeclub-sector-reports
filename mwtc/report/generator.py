"""Report generator: data packet -> Claude -> single-file HTML report."""
from __future__ import annotations

import re
import json
import logging
from pathlib import Path
from typing import Optional

from .. import config
from . import prompt, gauges

log = logging.getLogger("generator")

_LOGO_RE = re.compile(r'src="(data:image/[^"]+)"')

# Verbatim legal disclaimer — injected deterministically (never LLM-authored).
FOOTER_DISCLAIMER = (
    '<b>Educational purposes only — not investment advice.</b> The Freedom Management '
    'Group, Inc. d/b/a Michael Wade Trade Coaching is not a broker, adviser, or '
    'fiduciary. All trades are at your own risk; past performance does not guarantee '
    'future results. Options involve substantial risk and you can lose more than your '
    'investment — always paper trade first before risking real money. This report is '
    'generated with the assistance of artificial intelligence, and AI can make '
    'mistakes. The analysis, prices, technical levels, earnings dates, and figures '
    'herein are produced by automated models that may misinterpret data, rely on '
    'sources that are outdated or inaccurate, or generate confident-sounding output '
    'that is simply wrong. Nothing here has been independently verified by a licensed '
    'professional. Always confirm every data point, price, and date against your own '
    'brokerage and primary sources before acting, and treat this report as a starting '
    'point for your own research — never as a substitute for your own judgment. By '
    'using our services, you agree to our '
    '<a href="https://www.mwtradecoach.com/terms-and-conditions">Terms &amp; Conditions</a> '
    'and <a href="https://www.mwtradecoach.com/privacy-policy">Privacy Policy</a>.'
)


def ensure_disclaimer(html: str) -> str:
    """Guarantee the verbatim disclaimer is present. The <!--DISCLAIMER--> marker
    replace is a no-op if the model didn't reproduce the marker, so inject the
    disclaimer into a footer before </body> as a deterministic fallback."""
    if "Educational purposes only" in html and "Freedom Management Group" in html:
        return html
    block = (
        '<footer style="margin-top:36px;padding-top:18px;border-top:1px solid #2a3340;'
        'font-size:11.5px;line-height:1.6;color:#9aa7b6">'
        '<div style="background:#161b24;border:1px solid #2a3340;border-radius:10px;'
        f'padding:14px 16px"><p style="margin:0">{FOOTER_DISCLAIMER}</p></div></footer>'
    )
    m = re.search(r"</body>", html, re.IGNORECASE)
    return (html[:m.start()] + block + html[m.start():]) if m else (html + block)


def normalize_h1(html: str, mode: str) -> str:
    """Make sure the main <h1> names the report — the model sometimes drops the
    label and shows only the date (seen on the post-market edition)."""
    label = "Post-Market Report" if mode == "postmarket" else "Pre-Market Report"
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return html
    inner = m.group(1)
    if re.search(r"pre-?market|post-?market|market\s*wrap", inner, re.IGNORECASE):
        return html  # already labeled
    new_inner = f"{label} &mdash; {inner.strip()}" if inner.strip() else label
    return html[:m.start(1)] + new_inner + html[m.end(1):]


def _load_template() -> str:
    return (config.ASSETS_DIR / "report-template.html").read_text(encoding="utf-8")


def _load_logos() -> tuple[Optional[str], Optional[str]]:
    """Extract the two base64 data URIs (Trade Club AI, Michael Wade) from the
    embed snippet. Returns (tc_uri, mw_uri); either may be None."""
    snippet_path = config.ASSETS_DIR / "logo-embed-snippet.html"
    if not snippet_path.exists():
        return None, None
    uris = _LOGO_RE.findall(snippet_path.read_text(encoding="utf-8"))
    tc = uris[0] if len(uris) >= 1 else None
    mw = uris[1] if len(uris) >= 2 else None
    return tc, mw


def _strip_fences(text: str) -> str:
    """Remove any stray markdown code fences and leading prose before <!DOCTYPE."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    idx = text.lower().find("<!doctype html")
    if idx == -1:
        idx = text.lower().find("<html")
    return text[idx:] if idx > 0 else text


def _inject_logos(html: str) -> str:
    tc, mw = _load_logos()
    if tc:
        html = html.replace("{{LOGO_TC}}", tc)
    if mw:
        html = html.replace("{{LOGO_MW}}", mw)
    # If logos missing, drop the placeholders so they don't render as broken images.
    html = html.replace("{{LOGO_TC}}", "").replace("{{LOGO_MW}}", "")
    return html


_SECTION_RE = re.compile(r'<section>(\s*<div class="sec-title">)(.*?)(</div>)', re.S)


def _slug(label: str) -> str:
    txt = re.sub(r"<[^>]+>", "", label)                 # strip tags
    txt = re.sub(r"&[a-zA-Z]+;|&#\d+;", " ", txt)        # strip entities
    txt = re.sub(r"[^A-Za-z0-9 ]", " ", txt).strip().lower()
    return re.sub(r"\s+", "-", txt)[:40] or "section"


def _clean_label(label: str) -> str:
    txt = re.sub(r"<[^>]+>", "", label)
    txt = txt.replace("&amp;", "&")
    txt = re.sub(r"&[a-zA-Z]+;|&#\d+;", "", txt)         # drop emoji/entities
    txt = re.sub(r"\s+", " ", txt).strip(" -—·|")
    # keep it short for a button
    return (txt[:22].rstrip() + "…") if len(txt) > 23 else txt


def _inject_section_nav(html: str) -> str:
    """Deterministically add ids to each <section> and build a sticky nav bar.
    Robust to LLM wording: it keys off the house-style `.sec-title` div, not on
    any specific heading text. No-ops cleanly if no sections are found."""
    items: list[tuple[str, str]] = []
    seen: set[str] = set()

    def repl(m: re.Match) -> str:
        gap, label, close = m.group(1), m.group(2), m.group(3)
        slug = _slug(label)
        base, n = slug, 2
        while slug in seen:
            slug = f"{base}-{n}"; n += 1
        seen.add(slug)
        items.append((slug, _clean_label(label)))
        return f'<section id="{slug}">{gap}{label}{close}'

    html = _SECTION_RE.sub(repl, html)

    # TL;DR gets a "Summary" anchor too.
    if 'class="tldr"' in html and 'class="tldr" id=' not in html:
        html = html.replace('<div class="tldr">', '<div class="tldr" id="summary">', 1)
        items.insert(0, ("summary", "Summary"))

    if not items:
        return html.replace("<!--SECTION-NAV-->", "")

    nav = ('<nav class="secnav" aria-label="Jump to section">'
           + "".join(f'<a href="#{sid}">{label}</a>' for sid, label in items)
           + "</nav>")
    if "<!--SECTION-NAV-->" in html:
        return html.replace("<!--SECTION-NAV-->", nav, 1)
    # Fallback: insert before the TL;DR (or first section) if the marker is gone.
    for anchor in ('<div class="tldr"', "<section "):
        i = html.find(anchor)
        if i != -1:
            return html[:i] + nav + "\n  " + html[i:]
    return html


def generate(data_packet: dict, report_date: str, mode: Optional[str] = None) -> str:
    """Call Claude and return the finished HTML string. mode = premarket|postmarket."""
    from anthropic import Anthropic

    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — cannot generate the report.")

    mode = (mode or config.REPORT_MODE or "premarket").lower()
    template_html = _load_template()
    packet_json = json.dumps(data_packet, default=str, indent=2)

    system, user = prompt.build_messages(mode, packet_json, template_html, report_date)

    # max_retries: the SDK auto-retries transient errors (429 / 5xx / connection)
    # with backoff. This is the only LLM call in the bot and a scheduled run has
    # no second chance until the next slot, so give it extra headroom.
    # timeout=900: a 32000-token report can take a while; without an explicit
    # long timeout the SDK refuses a non-streaming call ("Streaming is required
    # for operations that may take longer than 10 minutes"). Matches build.py.
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=4, timeout=900)
    log.info("Calling Anthropic model=%s mode=%s", config.ANTHROPIC_MODEL, mode)
    resp = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=32000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    generate.last_usage = getattr(resp, "usage", None)  # for the cost line in the notifier
    # A dense report (esp. post-market) can outrun the budget; never publish a
    # report that was cut off mid-element. Fail loud so the run can be re-fired.
    if getattr(resp, "stop_reason", None) == "max_tokens":
        raise RuntimeError("report truncated at max_tokens — raise max_tokens or trim the prompt")
    raw = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
    html = _inject_logos(_strip_fences(raw))
    # Inject the deterministic dial dashboard at the marker (never LLM-drawn).
    html = html.replace("<!--DASHBOARD-->", gauges.render_dashboard(data_packet, mode))
    # Inject the -100..+100 index bias dials in the Technical Analysis section.
    html = html.replace("<!--INDEX-DIALS-->", gauges.render_index_bias_dials(data_packet, mode))
    # Inject the verbatim legal disclaimer (never LLM-authored).
    html = html.replace("<!--DISCLAIMER-->", FOOTER_DISCLAIMER)
    # Add section ids + the sticky jump-nav (deterministic, post-process).
    html = _inject_section_nav(html)
    # Deterministic safety nets — the model can drop the disclaimer marker or the
    # h1 label; Python guarantees both.
    html = ensure_disclaimer(html)
    html = normalize_h1(html, mode)
    return html


def save(html: str, report_date: str, mode: str = "premarket") -> Path:
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    label = "Postmarket" if mode == "postmarket" else "Premarket"
    out = config.REPORTS_DIR / f"MWTC-{label}_{report_date}.html"
    out.write_text(html, encoding="utf-8")
    # Stable per-mode "latest" copies for GitHub Pages / bookmarking.
    (config.REPORTS_DIR / f"latest-{mode}.html").write_text(html, encoding="utf-8")
    log.info("Saved report -> %s", out)
    return out
