"""The date bar: one prominent strip under every report's title saying which
day, what time and which run the page is.

The date, time and run label always come from code (the run clock), never
from the model. Both pipelines use it: build.py for the house and engine
reports, mwtc/publish.py for Pre-Market and Market Wrap.
"""
from __future__ import annotations

import html as _html
import re
from datetime import datetime

MARK = 'class="tc-datebar"'

CSS = (
    '<style id="tc-datebar">'
    '.tc-datebar{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;'
    'margin:10px 0 10px;padding:10px 15px;background:#141a24;border:1px solid #26303f;'
    'border-left:4px solid #4ea1ff;border-radius:0 8px 8px 0;line-height:1.25}'
    '.tc-datebar .tc-db-date{font-size:19px;font-weight:800;color:#fff;letter-spacing:.01em}'
    '.tc-datebar .tc-db-time{font-size:15px;font-weight:700;color:#e8edf4;white-space:nowrap}'
    '.tc-datebar svg{width:17px;height:17px;vertical-align:-3px;margin-right:7px;fill:none;'
    'stroke-width:2;stroke-linecap:round;stroke-linejoin:round}'
    '.tc-datebar .tc-db-date svg{stroke:#4ea1ff}.tc-datebar .tc-db-time svg{stroke:#9fb0c3;'
    'width:15px;height:15px;margin-right:5px}'
    '.tc-datebar .tc-db-run{font-size:11px;font-weight:800;letter-spacing:.06em;padding:4px 10px;'
    'border-radius:999px;background:rgba(78,161,255,.14);color:#4ea1ff;'
    'border:1px solid rgba(78,161,255,.45);white-space:nowrap}'
    '@media(max-width:640px){.tc-datebar{gap:6px 12px;padding:9px 12px}'
    '.tc-datebar .tc-db-date{font-size:17px}'
    # On a phone the header stacks the logo above the title, as the Gap Scout
    # and MWTC headers already do; the house report.css has no such rule and
    # left the title (and so the bar) a ~100px column beside the logo.
    '.header{flex-direction:column;align-items:flex-start}.head-text{padding-right:54px}}'
    '</style>')

_CAL = ('<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/>'
        '<path d="M16 3v4M8 3v4M3 11h18"/></svg>')
_CLOCK = ('<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/>'
          '<path d="M12 7v5l3 3"/></svg>')


def long_date(t: datetime) -> str:
    """'Thursday, October 1, 2026' (no zero padding)."""
    return f'{t.strftime("%A, %B")} {t.day}, {t.year}'


def clock(t: datetime) -> str:
    """'8:57 AM ET'."""
    return f'{t.hour % 12 or 12}:{t.minute:02d} {"AM" if t.hour < 12 else "PM"} ET'


def bar(date_text: str, time_text: str, run_label: str) -> str:
    """The strip itself. Inputs are plain text; they are escaped here."""
    def esc(s):
        return _html.escape(str(s or "").strip(), quote=False)
    run = f'<span class="tc-db-run">{esc(run_label)}</span>' if run_label else ""
    return (f'<div class="tc-datebar">'
            f'<span class="tc-db-date">{_CAL}{esc(date_text)}</span>'
            f'<span class="tc-db-time">{_CLOCK}{esc(time_text)}</span>{run}</div>')


_STAMP_HOUSE = re.compile(
    r'(<div class="stamp">)\s*(?:[A-Z][a-z]+day,\s*)?[A-Z][a-z]+ \d{1,2}, \d{4}\s*'
    r'(?:&middot;|·)\s*~?\d{1,2}:\d{2}\s*[AP]M ET'
    r'(?:\s*(?:&middot;|·)\s*[A-Za-z][A-Za-z /-]{0,40})?'
    r'\s*(?:<span class="run-badge">[^<]{0,40}</span>)?\s*')
_STAMP_MWTC = re.compile(
    r'(<div class="stamp">)\s*Generated\s+[^<|]{0,40}?\bET\b\s*(?:&nbsp;)?\s*\|\s*(?:&nbsp;)?\s*')
_EMPTY_STAMP = re.compile(r'<div class="stamp">\s*</div>\s*')
_DATE = r'(?:[A-Z][a-z]+day,\s*)?[A-Z][a-z]+ \d{1,2}, \d{4}'
_SEP = r'\s*(?:&middot;|\u00b7|&mdash;|\u2014|\|)\s*'
_SUB = re.compile(r'(<div class="sub">)(.*?)(</div>)', re.S)
_SUB_LEAD = re.compile(r'^\s*' + _DATE + _SEP)
_SUB_TAIL = re.compile(_SEP + _DATE + r'\s*$')
_SUB_ONLY = re.compile(r'^\s*' + _DATE + r'\s*$')


def _tidy_stamp(head: str) -> str:
    """The stamp line under the title repeated the date, time and run type the
    bar now shows. Drop that leading part when it is in a known shape, keep
    the rest (badges, freshness notes); drop the line if nothing is left. An
    unfamiliar stamp is left exactly as it was."""
    for rx in (_STAMP_HOUSE, _STAMP_MWTC):
        head, n = rx.subn(r'\1', head, count=1)
        if n:
            break
    return _EMPTY_STAMP.sub("", head, count=1)


def _tidy_sub(seg: str) -> str:
    """A subtitle that opens or ends with the date ('Thursday, October 01, 2026
    · Pre-Market Edition') loses just that date; nothing else is touched."""
    def fix(m):
        t = m.group(2)
        if _SUB_ONLY.match(t):
            return ""
        t = _SUB_TAIL.sub("", _SUB_LEAD.sub("", t, count=1), count=1)
        return m.group(1) + t + m.group(3)
    return _SUB.sub(fix, seg, count=1)


def inject(html: str, date_text: str, time_text: str, run_label: str) -> str:
    """Put the bar right under the page's header title. Idempotent: a page that
    already has a bar is returned unchanged, and so is a page without the
    standard header (class="head-text" holding an <h1>) — never an error."""
    if not isinstance(html, str) or MARK in html:
        return html
    i = html.find('class="head-text"')
    if i < 0:
        return html
    j = html.find("</h1>", i)
    if j < 0 or j - i > 6000:
        return html
    j += len("</h1>")
    u = html.find('<div class="sub">', j)
    if 0 <= u - j <= 400:
        v = html.find("</div>", u)
        if v > 0:
            v += len("</div>")
            html = html[:u] + _tidy_sub(html[u:v]) + html[v:]
    s = html.find('<div class="stamp">', j)
    if 0 <= s - j <= 4000:
        e = html.find("</div>", s)
        if e > 0:
            e += len("</div>")
            html = html[:s] + _tidy_stamp(html[s:e]) + html[e:]
    html = html[:j] + bar(date_text, time_text, run_label) + html[j:]
    if "</head>" in html:
        return html.replace("</head>", CSS + "\n</head>", 1)
    return html[:j] + CSS + html[j:]
