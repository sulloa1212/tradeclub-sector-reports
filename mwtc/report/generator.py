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
    'investment — always paper trade first before risking real money. <b>This report is '
    'generated with the assistance of artificial intelligence, and AI can make '
    'mistakes.</b> The analysis, prices, technical levels, earnings dates, and figures '
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


_SECTION_RE = re.compile(r'<section([^>]*)>(\s*<div class="sec-title">)(.*?)(</div>)', re.S)


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
        attrs, gap, label, close = m.group(1), m.group(2), m.group(3), m.group(4)
        # Reuse the model's own id if it assigned one (it now does); otherwise
        # derive a slug and add the id ourselves. Either way, every section gets
        # a matching nav button — the old regex only matched bare <section> and
        # silently produced a 1-button nav once the model started emitting ids.
        existing = re.search(r'id="([^"]+)"', attrs or "")
        if existing:
            slug = existing.group(1)
            open_tag = f'<section{attrs}>'
        else:
            slug = _slug(label)
            base, n = slug, 2
            while slug in seen:
                slug = f"{base}-{n}"; n += 1
            open_tag = f'<section id="{slug}"{attrs}>'
        seen.add(slug)
        items.append((slug, _clean_label(label)))
        return f'{open_tag}{gap}{label}{close}'

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


def _coverage_gaps(html: str, data: dict) -> list:
    """Deterministic must-cover check against the packet: the top overnight
    earnings result must appear early in the report, and every large pre-market
    mover must appear somewhere. Returns human-readable gap descriptions."""
    gaps = []
    try:
        # VISIBLE text, not raw markup: the report's <style> block alone eats
        # most of an 8000-char raw window, so the lead check false-flagged a
        # report that led with AVGO (2026-09-03) and burned a paid retry.
        up = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", html)
        up = re.sub(r"<[^>]+>", " ", up)
        up = re.sub(r"\s+", " ", up).upper()
        ov = ((data.get("earnings") or {}).get("overnight_results") or [])
        if ov:
            sym = str(ov[0].get("symbol") or "").upper()
            if sym and sym not in up[:4000]:
                gaps.append(f"the biggest overnight earnings result ({sym}) must lead the summary")
        movers = data.get("movers") or {}
        for grp in ("gainers", "losers"):
            for m in (movers.get(grp) or [])[:3]:
                if not isinstance(m, dict):
                    continue
                sym = str(m.get("symbol") or m.get("ticker") or "").upper()
                try:
                    pct = abs(float(m.get("pct_change") or m.get("changesPercentage")
                                    or m.get("change_pct") or m.get("pct") or 0))
                except (TypeError, ValueError):
                    pct = 0
                if sym and pct >= 4 and sym not in up:
                    gaps.append(f"top pre-market {grp[:-1]} {sym} ({pct:.0f}%) never mentioned")
        # Reaction contradiction (2026-09-03 AVGO lesson): an overnight reporter
        # trading hard DOWN must be covered as down — a report that mentions the
        # ticker only in bullish beat language gets one targeted retry.
        for r in ov:
            sym = str(r.get("symbol") or "").upper()
            try:
                pct = float(r.get("premarket_reaction_pct"))
            except (TypeError, ValueError):
                continue
            if not sym or pct >= -3:
                continue
            i = up.find(sym)
            if i < 0:
                continue
            ctx = up[max(0, i - 400): i + 400]
            neg = ("DOWN", "DROP", "FALL", "FELL", "SLID", "SLIP", "LOWER", "WEAK",
                   "SANK", "TUMBL", "DECLIN", "SELL", "SOLD", "GUID", "MISS",
                   "DISAPPOINT", "NEGATIVE", "RED")
            if not any(w in ctx for w in neg):
                gaps.append(f"{sym} trades {pct:.1f}% pre-market after its print — "
                            "cover the actual reaction and its driver, not just the headline beat")
    except Exception as e:  # noqa: BLE001 — the check must never kill a run
        log.warning("coverage check skipped: %s", e)
    return gaps


def _cap_packet(data: dict) -> dict:
    """Size governor: a backstop for ANOMALIES that must stay silent on a normal
    day. 2026-09-24: a UW endpoint began returning huge payloads, the packet hit
    1.66M tokens (model limit 1M) and the wrap and next pre-market 400-failed.

    Budgets are in compact-JSON chars. Calibrated on that failure, this packet
    runs ~0.52 tokens per compact char (the prompt embeds it with indent=2,
    ~1.4x larger), so the 1.5M total is roughly 790K tokens.

    'institutional' carries ~30 endpoint payloads and measures ~470-510K after
    the per-endpoint trims in the UW source (every scheduled run sees a full
    session — the 8:40 pre-market carries the prior day's). Its 700K budget
    sits well above that; the source clamps are what catch a runaway feed.

    When something IS over budget, every unprotected list gives up the same
    fraction, so the feed that bloated pays its share and no single ticker is
    gutted. PROTECTED leaves — the GEX window and the small payloads the
    dashboard dials are computed from — are touched only if lists alone cannot
    absorb the overage. Works on the copy it is given. Never raises, always
    terminates."""
    BUDGET = 250_000
    BUDGETS = {"institutional": 700_000}
    TOTAL = 1_500_000
    PROTECTED = frozenset({"gex_by_strike", "gex_window", "options_volume",
                           "interpolated_iv", "expected_move", "session_total"})

    def _size(v) -> int:
        return len(json.dumps(v, default=str))

    def _f(x):
        try:
            v = float(str(x).replace(",", "").strip())
            return v if v == v and abs(v) != float("inf") else None
        except (TypeError, ValueError):
            return None

    def _is_gex(r) -> bool:
        # A projected gex_by_strike row. Flow alerts and contract rows carry a
        # `strike` too, but those feeds are newest/largest-first and must keep
        # their prefix — so the gamma fields are what identify a strike ladder.
        return (isinstance(r, dict) and "strike" in r
                and ("call_gamma_oi" in r or "put_gamma_oi" in r))

    def _shrink(v, budget: int):
        if isinstance(v, list):
            n = len(v)
            ladder = n > 0 and all(_is_gex(r) for r in v[:3])
            order = None
            if ladder:
                # Same rule as the source's near-spot window: keep the strikes
                # NEAREST THE SPOT by price distance (UW's own underlying
                # price), in their original order. Counting rows from the
                # middle of the list is wrong when strike spacing is uneven
                # or the chain is one-sided.
                px = sorted(x for x in (_f(r.get("price")) for r in v
                                        if isinstance(r, dict)) if x and x > 0)
                ks = [_f(r.get("strike")) if isinstance(r, dict) else None for r in v]
                if px and all(k is not None for k in ks):
                    ref = px[len(px) // 2]
                    order = sorted(range(n), key=lambda i: (abs(ks[i] - ref), i))

            def take(m: int):
                if not ladder:
                    return v[:m]
                if order is not None:
                    return [v[i] for i in sorted(order[:m])]
                start = (n - m) // 2           # no usable price: middle of the list
                return v[start:start + m]

            lo, hi = 0, n
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if _size(take(mid)) <= budget:
                    lo = mid
                else:
                    hi = mid - 1
            return take(lo)
        if isinstance(v, dict):
            import heapq
            out = dict(v)
            cost = {k: _size(x) for k, x in out.items()}
            total = _size(out)

            def keycost(k) -> int:            # '"key": ' plus the ', ' separator
                return len(json.dumps(str(k))) + 4

            # Largest child first; sizes are tracked incrementally (replacing a
            # value changes the JSON length by exactly the difference), so a
            # dict with 100K+ small keys costs O(n log n), not O(n^2).
            heap = [(-c, i, k) for i, (k, c) in enumerate(cost.items())]
            heapq.heapify(heap)
            markers = set()
            while heap and total > budget:
                neg, i, k = heapq.heappop(heap)
                child = -neg
                if k not in out or cost.get(k) != child:
                    continue                       # stale heap entry
                if k in markers:                   # already a marker, still over
                    del out[k]
                    total -= child + keycost(k)
                    continue
                target = max(child - (total - budget), 2_000)
                shrunk = None
                if target < child and isinstance(out[k], (list, dict)):
                    shrunk = _shrink(out[k], target)
                new = _size(shrunk) if shrunk is not None else child
                if new < child:
                    out[k], cost[k] = shrunk, new
                    total -= child - new
                    heapq.heappush(heap, (-new, i, k))
                    continue
                marker = f"[truncated: oversized ({child:,} chars)]"
                msize = len(marker) + 2
                if msize < child:
                    out[k], cost[k] = marker, msize
                    markers.add(k)
                    total -= child - msize
                    heapq.heappush(heap, (-msize, i, k))   # deleted if still over
                else:
                    del out[k]                     # cannot be reduced: drop
                    total -= child + keycost(k)
            # Every step strictly shrinks the dict or retires a key, so the loop
            # always ends. The tracked total is exact but for one separator, so
            # this check rarely does anything; it stays linear if it must.
            if out and _size(out) > budget:
                for k in sorted(out, key=lambda kk: -cost.get(kk, 0)):
                    del out[k]
                    total -= cost.get(k, 0) + keycost(k)
                    if total <= budget and _size(out) <= budget:
                        break
            return out
        return f"[truncated: oversized value ({_size(v):,} chars)]"

    def _lists(node) -> list:
        """(parent, key) of every list under node not below a PROTECTED key."""
        found, stack = [], [node]
        while stack:
            cur = stack.pop()
            if not isinstance(cur, dict):
                continue
            for k, v in cur.items():
                if k in PROTECTED:
                    continue
                if isinstance(v, list):
                    found.append((cur, k))
                elif isinstance(v, dict):
                    stack.append(v)
        return found

    def _fit(node, budget: int, label: str):
        """Bring node under budget: proportional trim of the unprotected lists
        first, largest-first shrinking only as the last resort."""
        over = _size(node) - budget
        if over <= 0:
            return node
        if isinstance(node, dict):
            lists = _lists(node)
            sizes = [_size(par[k]) for par, k in lists]
            cuttable = sum(sizes)
            if cuttable and over <= cuttable * 0.9:
                frac = over / cuttable
                for (par, k), sz_ in zip(lists, sizes):
                    rows = len(par[k])
                    # each list drops at least its share, so the sum of the
                    # cuts covers the overage
                    par[k] = _shrink(par[k], max(int(sz_ * (1 - frac)), 2))
                    if len(par[k]) < rows:
                        log.warning("  governor: %s … %s %d -> %d rows",
                                    label, k, rows, len(par[k]))
                if _size(node) <= budget:
                    return node
            log.warning("  governor: %s cannot fit by trimming lists alone — "
                        "shrinking largest-first (protected leaves may be cut)", label)
        return _shrink(node, budget)

    def _refresh_windows(node) -> None:
        """Keep every gex_window truthful about the ladder that actually ships."""
        stack = [node]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                if "gex_window" in cur and "gex_by_strike" in cur:
                    rows, win = cur["gex_by_strike"], cur["gex_window"]
                    if not isinstance(rows, list) or not rows:
                        cur["gex_by_strike"], cur["gex_window"] = None, None
                    elif isinstance(win, dict) and len(rows) != win.get("rows_kept"):
                        ks = [k for k in (_f(r.get("strike")) for r in rows
                                          if isinstance(r, dict)) if k is not None]
                        ref = _f(win.get("spot_used"))
                        if ks and ref:
                            win.update(rows_kept=len(rows), strike_min=min(ks),
                                       strike_max=max(ks),
                                       pct_below_spot=round((ref - min(ks)) / ref * 100, 1),
                                       pct_above_spot=round((max(ks) - ref) / ref * 100, 1),
                                       governor_trimmed=True)
                        else:
                            cur["gex_by_strike"], cur["gex_window"] = None, None
                stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
            elif isinstance(cur, list):
                stack.extend(v for v in cur if isinstance(v, dict))

    def _enforce(node, cap: int, label: str):
        """Fit, then make the GEX windows truthful. Refreshing a window adds a
        few fields, so room is reserved for it and the result is re-checked:
        what ships is never above `cap`."""
        node = _fit(node, cap - 2_000, label)
        _refresh_windows(node)
        extra = _size(node) - cap
        if extra > 0:
            node = _fit(node, cap - extra - 2_000, label)
            _refresh_windows(node)
        return node

    if not isinstance(data, dict):
        return data
    try:
        sizes = sorted(((k, _size(v)) for k, v in data.items()), key=lambda x: -x[1])
        log.info("packet key sizes (chars): %s",
                 ", ".join(f"{k}={s:,}" for k, s in sizes[:8]))
        for k, s in sizes:
            cap = BUDGETS.get(k, BUDGET)
            if s > cap:
                log.warning("packet key '%s' oversized (%s chars) — trimming to ~%s",
                            k, f"{s:,}", f"{cap:,}")
                if isinstance(data[k], dict):
                    subs = sorted(((sk, _size(sv)) for sk, sv in data[k].items()),
                                  key=lambda x: -x[1])[:6]
                    log.warning("  '%s' sub-key sizes: %s", k,
                                ", ".join(f"{sk}={ss:,}" for sk, ss in subs))
                data[k] = _enforce(data[k], cap, k)
        total = _size(data)
        if total > TOTAL:
            # several keys at their caps together can still exceed the model limit
            log.warning("packet total %s chars exceeds %s — trimming every feed "
                        "proportionally", f"{total:,}", f"{TOTAL:,}")
            data = _enforce(data, TOTAL, "packet")
        log.info("packet total after governor: %s chars", f"{_size(data):,}")
    except Exception as e:  # noqa: BLE001 — the governor must never kill a run
        log.warning("packet size governor skipped: %s", e)
    return data


def generate(data_packet: dict, report_date: str, mode: Optional[str] = None) -> str:
    """Call Claude and return the finished HTML string. mode = premarket|postmarket."""
    from anthropic import Anthropic

    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — cannot generate the report.")

    mode = (mode or config.REPORT_MODE or "premarket").lower()
    template_html = _load_template()
    # The governor works on a COPY. The dashboard dials and the coverage check
    # below are computed from the full packet, so a trim made only to fit the
    # model's context can never change a dial or hide a mover from the checker.
    prompt_packet = _cap_packet(json.loads(json.dumps(data_packet, default=str)))
    packet_json = json.dumps(prompt_packet, default=str, indent=2)

    system, user = prompt.build_messages(mode, packet_json, template_html, report_date)

    # max_retries: the SDK auto-retries transient errors (429 / 5xx / connection)
    # with backoff. This is the only LLM call in the bot and a scheduled run has
    # no second chance until the next slot, so give it extra headroom.
    # timeout=900: a large report can take a while; without an explicit long
    # timeout the SDK refuses a non-streaming call ("Streaming is required for
    # operations that may take longer than 10 minutes"). Matches build.py.
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=4, timeout=900)
    raw = None
    for attempt in (1, 2):
        log.info("Calling Anthropic model=%s mode=%s attempt=%s",
                 config.ANTHROPIC_MODEL, mode, attempt)
        resp = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            # 64000: Sonnet's output ceiling. The 21-section MWTC report outgrew 32000
            # and tripped the truncation guard; running at the max leaves no headroom
            # to lose a report to length. Cost is per token used.
            max_tokens=64000,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        generate.last_usage = getattr(resp, "usage", None)  # for the notifier cost line
        # A dense report (esp. post-market) can outrun the budget; never publish a
        # report that was cut off mid-element. Fail loud so the run can be re-fired.
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise RuntimeError("report truncated at max_tokens — raise max_tokens or trim the prompt")
        raw = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        # Coverage check (2026-08-27 NVDA lesson): the packet SAYS what the
        # biggest overnight facts are — verify the report actually covers them
        # instead of trusting the prompt. One targeted retry, then fail-open.
        gaps = _coverage_gaps(raw, data_packet)
        if not gaps:
            break
        if attempt == 1:
            log.warning("coverage gaps, retrying once: %s", gaps)
            user = user + ("\n\nCOVERAGE FIX (your previous draft missed these — "
                           "they are in the packet and MUST be covered): "
                           + "; ".join(gaps))
        else:
            log.warning("coverage gaps persist after retry (publishing anyway): %s", gaps)
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
