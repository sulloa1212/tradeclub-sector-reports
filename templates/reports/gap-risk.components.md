# Gap Risk (v8) — Components Blueprint

Writer's guide for filling `<!--REPORT-CONTENT-->` in `gap-risk.template.html`.
The template already supplies the fixed chrome (head+`<style>`, header, nav, footer/disclaimer). There is **no `<script>`** in this report. Everything below is the day's content that replaces `<!--REPORT-CONTENT-->`, in this exact order.

Genericized data uses angle-bracket tokens: `<ticker>`, `<price>`, `<pct>`, `<pts>`, `<odds>`, `<level>`, `<dial>`, `<width%>`, `<color>`, `<text>`. Keep all HTML entities (`&plusmn;`, `&middot;`, `&mdash;`, `&le;`, `&#9664;`, etc.) exactly as shown.

---

## Chrome placeholders (already in template — set these once per run)

| Token | Meaning | Example |
|---|---|---|
| `{{TITLE}}` | Report subject / h1 | `Daily AI Weekend Gap Risk Report` |
| `{{RUN_TYPE}}` | Run label, title case | `Post-Market Run` |
| `{{RUN_TYPE_UPPER}}` | Run label, upper (colored in stamp) | `POST-MARKET RUN` |
| `{{DATE}}` | Long date | `Friday, June 26, 2026` |
| `{{ET_TIME}}` | Time stamp | `~7:41 PM ET` |
| `{{STAMP_TAGLINE}}` | Short context tail on stamp line | `weekend gap into Monday &middot; cushion thin` |

The `.sub` line (`SPX &middot; NDX &middot; DJX &middot; RUT &mdash; gap into the next open + 1-week outlook`) and the nav (`Gap Board / NDX / RUT / SPX / DJX / Big Move / Clock / Calendar / Playbook`) are part of the fixed chrome — do not regenerate them.

---

## SECTION ORDER (content that replaces `<!--REPORT-CONTENT-->`)

| # | id | Title (h2) | Purpose |
|---|---|---|---|
| — | (none) | `.heads` banner | One-line "what happened + why" hook above the fold |
| — | (none) | `.tldr` — "The 60-Second Read" | 5-bullet executive summary |
| 1 | `board` | The Gap Board — Tap An Index To Jump To Its Card | Comparison table of all 4 indices + breadth read |
| 2..5 (drills) | `ndx` `rut` `spx` `djx` | one `.drill` card per index | Per-index weekend + 1-week odds tables, gauges, levels, cushion |
| 2 | `bigmove` | Big Move Ranking with Probabilities — 1-Week Horizon | 4-index ranking table with probability bars |
| 3 | `clock` | The Weekend Clock — Where Monday's Gap Gets Made | Vertical timeline of weekend catalysts |
| 4 | `calendar` | Event Calendar — Next Few Sessions | Event table with done-rows |
| 5 | `playbook` | Weekend + 1-Week Playbook | DO / DON'T two-column grid |
| — | (none) | closing `.banner` | Final one-paragraph takeaway |
| — | (none) | How To Read This Report | Definition-list legend |

> Note the on-page section numbers: the Gap Board is **1**; the four per-index drill cards are unnumbered (linked from the board/nav); then Big Move=**2**, Clock=**3**, Calendar=**4**, Playbook=**5**. The drill cards physically sit between the board and the Big Move section, in nav order NDX, RUT, SPX, DJX. Preserve that.

---

## COMPONENT PATTERNS

### C1. `.heads` — top hook banner (fixed accent border)
```html
<div class="heads">
    <div class="icon">&#x26A0;&#xFE0F;</div>
    <div><p class="t"><text: one-sentence headline></p>
    <p class="b"><text: 2–3 sentence context, <b>bold</b> the key drivers></p></div>
</div>
```
Dynamic: the icon glyph, both paragraphs. Fixed: classes, structure.

### C2. `.tldr` — "The 60-Second Read"
```html
<div class="tldr">
    <h2>&#x1F3AF; The 60-Second Read</h2>
    <ul>
      <li><span class="lead"><text></span> ... <span class="pos">…</span> / <span class="neg">…</span> …</li>
      <!-- exactly 5 <li>; last li typically opens with the ⚠ glyph &#9888; -->
    </ul>
</div>
```
Inline color spans (dynamic which is used):
- `.lead` = bold lead-in (neutral). Combine as `class="lead neg"` / `class="lead pos"` to tint.
- `.pos` = bullish/green, `.neg` = bearish/red, `.flat`/`.muted` = neutral.
- `.flip` = accent (cyan) — used for flip/cushion levels like `~29,000`.

### C3. Section header `h2.sec-h` (numbered)
```html
<h2 class="sec-h"><span class="num"><n></span> <text: Section Title></h2>
```
`.num` is the cyan circle badge. Numbers used on page: 1 (board), 2 (bigmove), 3 (clock), 4 (calendar), 5 (playbook). The "How To Read This Report" legend header omits the `.num` span:
```html
<h2 class="sec-h">How To Read This Report</h2>
```

### C4. `.board` table — the Gap Board (Section 1)
```html
<section id="board">
    <h2 class="sec-h"><span class="num">1</span> The Gap Board &mdash; Tap An Index To Jump To Its Card</h2>
    <table class="board">
      <tr><th class="inst">Index (ETF)</th><th>Live</th><th>Day %</th><th>Impl. Weekend Move</th><th>Lean</th><th>Weekend Gap Dial</th><th class="lvls" style="text-align:left">Key Whole-# Levels</th></tr>
      <tr>
        <td class="inst"><a href="#<idlower>"><b><ticker></b> <small>(<etf>)</small></a></td>
        <td class="num"><price> <small style="color:var(--faint)">est</small></td>   <!-- <small>est</small> only when price is estimated -->
        <td class="num <pos|neg|flat>"><pct></td>
        <td class="num">&plusmn;<pct> <small style="color:var(--faint)"><pts>p</small></td>
        <td class="num"><b class="dn"><odds>%</b> <small style="color:var(--faint)">down</small></td>
        <td><span class="dialpill <dialclass>"><dial label></span></td>
        <td class="lvls">S <lvl> / <lvl> &middot; R <lvl> / <lvl></td>
      </tr>
      <!-- one <tr> per index, board order NDX, RUT, SPX, DJX -->
    </table>
    <div class="breadth"><b>Breadth read:</b> <text, with <b>bold</b> emphases></div>
</section>
```
Dynamic: every cell value, dial class/label, the day-% color class, whether `<small>est</small>` appears, breadth prose. Fixed: header row, class names, `&plusmn;`/`&middot;` entities.

### C5. `.drill` — per-index card (one per index, ids `ndx`/`rut`/`spx`/`djx`)
Outer shell:
```html
<section id="<idlower>">
    <div class="cardnav"><a href="#board">&uarr; Gap Board</a></div>
    <div class="drill" style="border-top-color:<accentcolor>">
      <div class="dhead" style="margin-bottom:2px">
        <div>
          <div class="dtitle"><ticker> <small><full name> &middot; <etf></small></div>
          <div class="dsub">Live <b>~<price></b> (<span class="pos"><pct></span>, est.) &nbsp;&middot;&nbsp; weekend 1SD <b>&plusmn;<pct></b> (&plusmn;<pts> pts) &nbsp;&middot;&nbsp; 1-week 1SD <b>&plusmn;<pct></b> &nbsp;&middot;&nbsp; <b><one-liner></b> &mdash; <text></div>
        </div>
      </div>
      <div class="cardgrid">
        <div class="cmain"> ... weekend block, cushion, weekhead, 1-week block ... </div>
        <div class="crail"> ... weekend gauge, levels block, 1-week rail+gauge ... </div>
      </div>
      <div class="metarow"> ... two <div> meta notes ... </div>
    </div>
</section>
```
`border-top-color` (drill top edge) is dynamic per risk: red `#ef4444` for the epicenter/highest-risk name, otherwise pick to match risk tone. Default CSS is `var(--bear)`.

#### C5a. `.block` odds table (used twice per card: weekend, then 1-week)
```html
<div class="block">
  <h4><text: e.g. Weekend gap &mdash; odds Monday opens DOWN vs UP (from <price>)></h4>
  <div class="leanrow">Lean (skew/positioning estimate): <b class="dn">~<odds>% down</b> &nbsp;/&nbsp; <b class="up">~<odds>% up</b></div>
  <div class="dgrid dghead">
    <span>move</span><span class="dn" style="text-align:left">level</span><span class="dn" style="text-align:left">odds</span>
    <span class="ctr">&#9664; down &nbsp;|&nbsp; up &#9654;</span>
    <span class="up" style="text-align:right">odds</span><span class="up" style="text-align:right">level</span>
  </div>
  <!-- first data row is the "quiet"/inside row: add class qrow and use &le; -->
  <div class="dgrid qrow">
    <span class="dlab">&le;<pct></span><span class="dprice dn"><level></span><span class="dp dn"><odds>%</span>
    <span class="dtrack"><span class="dhalf l"><span class="df dfdn" style="width:<w%>"></span></span><span class="dmid"></span><span class="dhalf r"><span class="df dfup" style="width:<w%>"></span></span></span>
    <span class="dp up"><odds>%</span><span class="dprice up"><level></span>
  </div>
  <!-- subsequent rows: plain .dgrid, move label like 0.5% / 1% / 1.5% / 2% (weekend) or 1% / 2% / 3% / 4% (1-week) -->
  <div class="dgrid">
    <span class="dlab"><pct></span><span class="dprice dn"><level></span><span class="dp dn"><odds>%</span>
    <span class="dtrack"><span class="dhalf l"><span class="df dfdn" style="width:<w%>"></span></span><span class="dmid"></span><span class="dhalf r"><span class="df dfup" style="width:<w%>"></span></span></span>
    <span class="dp up"><odds>%</span><span class="dprice up"><level></span>
  </div>
  <!-- typically 1 qrow + 4 plain rows -->
  <div class="note"><text: how-to-read note, with <b>bold</b> and vol reading></div>
</div>
```
Split-bar mechanics (fixed classes, dynamic widths):
- `.dfdn` = red down fill (`.dhalf.l`, grows right→left from center). `.dfup` = green up fill (`.dhalf.r`, left→right). `.dmid` = center divider.
- `width:<w%>` on `.df` is the visual bar length, roughly `odds×2` capped at 100% (design uses e.g. 16%→32%, 40%→80%, 26%→52%). Down and up widths are independent.
- `.dn` = red text/number, `.up` = green text/number. `.dprice` = level (muted), `.dp` = odds (bold colored). `.qrow` tints `.dlab` cyan.

#### C5b. `.cushion` — gamma explainer (one per card, after weekend block)
```html
<div class="cushion ">   <!-- trailing space intentional in design; add "thin" for the muted variant: class="cushion thin" -->
  <span class="h">&#x1F6E1;&#xFE0F; What &ldquo;the cushion&rdquo; means (gamma, in plain English)</span>
  <text with <b>bold</b>, <b class="pos">buy dips</b>, <b class="neg">off</b>>
</div>
```
Variants: `.cushion` (accent left-border, default) vs `.cushion.thin` (faint border, `var(--panel2)` bg) — use `.thin` when the cushion is weak/off.

#### C5c. `.weekhead` — divider before the 1-week block
```html
<div class="weekhead">&#x1F4C6; 1-Week Outlook <small>&mdash; next ~5 trading sessions; ranked in the Big Move section below</small></div>
```
Fixed text (dashed top-border divider).

#### C5d. Right rail (`.crail`): weekend gauge, levels, 1-week rail
Gauge (SVG dial — reused verbatim except the needle `<line>` endpoint and the value text/color):
```html
<div class="railbox"><div class="gaugebox">
  <svg viewBox="0 0 220 122" width="160" height="89" xmlns="http://www.w3.org/2000/svg">
    <path d="M 20 110 A 90 90 0 0 1 110 20" stroke="#22c55e" stroke-width="15" fill="none" stroke-linecap="round"/>
    <path d="M 110 20 A 90 90 0 0 1 200 110" stroke="#ef4444" stroke-width="15" fill="none" stroke-linecap="round"/>
    <path d="M 75 32 A 90 90 0 0 1 145 32" stroke="#f59e0b" stroke-width="15" fill="none" stroke-linecap="round"/>
    <line x1="110" y1="110" x2="<nx>" y2="<ny>" stroke="#e8edf3" stroke-width="3" stroke-linecap="round"/>
    <circle cx="110" cy="110" r="7" fill="#e8edf3"/>
    <text x="20" y="120" font-size="11" fill="#6b7787">Calm</text><text x="170" y="120" font-size="11" fill="#6b7787">Risky</text>
  </svg>
  <div class="gauge-value" style="color:<gaugecolor>"><DIAL LABEL></div>
  <div class="gauge-label"><Weekend gap risk | 1-week move risk></div>
</div></div>
```
Needle endpoint `(x2,y2)` sweeps from lower-left (calm) to lower-right (risky) around pivot `(110,110)`. Design examples: HIGH weekend → `160,40`; EXTREME 1-week → `190,70`. `gauge-value` color matches severity: `#f97316` (HIGH/orange), `#ef4444` (EXTREME/red), warn `#f59e0b`, green `#22c55e` (calm).

Levels block (right rail):
```html
<div class="block">
  <h4>Key whole-number levels</h4>
  <div class="lvls">
    <div class="lvrow"><span class="lab">Resistance</span><span class="chip r"><lvl></span><span class="chip r"><lvl></span></div>
    <div class="lvrow"><span class="lab">Live</span><span class="chip "><price></span></div>   <!-- neutral chip: class="chip " (trailing space) -->
    <div class="lvrow"><span class="lab">Weekend 1SD</span><span class="chip f"><lo> &ndash; <hi></span></div>
    <div class="lvrow"><span class="lab">1-week 1SD</span><span class="chip f"><lo> &ndash; <hi></span></div>
    <div class="lvrow"><span class="lab">Support</span><span class="chip s"><lvl></span><span class="chip s"><lvl></span></div>
    <div class="lvrow"><span class="lab">Cushion line</span><span class="chip f"><text></span></div>
  </div>
  <div class="note" style="margin-top:8px">Round numbers act as magnets &mdash; option open-interest clusters there. Re-verify live.</div>
</div>
```
Chip colors (fixed meaning): `.chip.r` = resistance (red), `.chip.s` = support (green), `.chip.f` = flip/range (cyan), `.chip ` (bare) = neutral/live.

1-week rail box (summary + second gauge):
```html
<div class="railbox">
  <div class="rsum">1-week move <b>&plusmn;<pct></b> (&plusmn;<pts> pts)<br>chance of a &gt;3% week: <b><odds>%</b><br>range <lo> &ndash; <hi></div>
  <div class="gaugebox"> ...second gauge SVG per C5d, label "1-week move risk"... </div>
</div>
```

#### C5e. `.metarow` — two per-card meta notes
```html
<div class="metarow">
  <div><b>&#x1F4C5; Driver:</b> <text></div>
  <div><b>&#x21A9; Gap-fill:</b> <text></div>
</div>
```

### C6. Big Move Ranking (Section 2) — `.panel` + `.board` with `.pbar`
```html
<section id="bigmove">
    <h2 class="sec-h"><span class="num">2</span> Big Move Ranking with Probabilities &mdash; 1-Week Horizon</h2>
    <p style="color:var(--muted);font-size:13.5px;margin:0 0 12px"><text intro></p>
    <div class="panel" style="padding:6px 18px">
      <table class="board">
        <tr><th class="num" style="width:36px">Rank</th><th class="inst">Index (ETF)</th><th>1-Week 1SD</th><th>Prob. of a &gt;3% week</th><th>Lean</th><th>1-Week Dial</th></tr>
        <tr>
          <td class="num" style="color:var(--faint)">#<rank></td>
          <td class="inst"><a href="#<idlower>"><b><ticker></b> <small>(<etf>)</small></a></td>
          <td class="num">&plusmn;<pct> <small style="color:var(--faint)"><pts>p</small></td>
          <td style="white-space:nowrap"><span class="pbar"><i style="width:<w%>"></i></span><b><odds>%</b></td>
          <td class="num"><b class="dn"><odds>%</b> <small style="color:var(--faint)">down</small></td>
          <td><span class="dialpill <dialclass>"><dial label></span></td>
        </tr>
        <!-- rows ranked #1..#4 by prob of >3% week -->
      </table>
    </div>
    <div class="breadth" style="margin-top:12px"><b>How to read it:</b> <text></div>
</section>
```
`.pbar > i` width is the visual probability bar (design: 44%→88%, i.e. `odds×2`). Fixed: header, classes.

### C7. Weekend Clock (Section 3) — `.clock` timeline
```html
<section id="clock">
    <h2 class="sec-h"><span class="num">3</span> The Weekend Clock &mdash; Where Monday&rsquo;s Gap Gets Made</h2>
    <p style="color:var(--muted);font-size:13.5px;margin:0 0 12px"><text intro></p>
    <div class="clock">
      <div class="ce"><span class="t"><time/label></span><span class="w"><text, <b>bold</b> emphases></span></div>
      <!-- one .ce per timeline entry -->
    </div>
</section>
```
`.ce` = timeline entry (cyan dot via ::before, dashed separators). `.t` = cyan time label, `.w` = muted body.

### C8. Event Calendar (Section 4) — `.panel` + `table.cal`
```html
<section id="calendar">
    <h2 class="sec-h"><span class="num">4</span> Event Calendar &mdash; Next Few Sessions</h2>
    <div class="panel" style="padding:6px 18px">
      <table class="cal">
        <tr><th class="dt">When</th><th>Event</th><th>Why it matters for the gap</th></tr>
        <tr class="done"><td class="dt"><when></td><td><event> &mdash; <b>done</b></td><td><text></td></tr>   <!-- .done = past event: dims row + appends "— DONE" via CSS -->
        <tr><td class="dt"><when></td><td><event></td><td><text></td></tr>
      </table>
    </div>
    <div class="note" style="margin-top:8px"><text: backdrop note></div>
</section>
```
`tr.done` (fixed): dims the row to 0.72 opacity and CSS auto-appends a red "— DONE" after the date cell. Use for events that have already occurred.

### C9. Playbook (Section 5) — `.dodont` two-column grid
```html
<section id="playbook">
    <h2 class="sec-h"><span class="num">5</span> Weekend + 1-Week Playbook</h2>
    <div class="dodont">
      <div class="col do"><h3>&#x2705; DO</h3><ul>
        <li><text, <b>bold</b> key terms></li>
        <!-- ~6 li -->
      </ul></div>
      <div class="col dont"><h3>&#x274C; DON&rsquo;T</h3><ul>
        <li><text></li>
        <!-- ~6 li -->
      </ul></div>
    </div>
</section>
```
`.col.do` = green panel, `.col.dont` = red panel. Fixed headings (✅ DO / ❌ DON'T).

### C10. Closing `.banner` (unnumbered section)
```html
<section>
    <div class="banner">
      <div class="icon">&#x1F4CC;</div>
      <div>
        <p class="title"><text: one-sentence bottom line></p>
        <p class="body"><text: action-oriented follow-up></p>
      </div>
    </div>
</section>
```
Accent-bordered summary card. Dynamic: icon, both paragraphs.

### C11. Legend (unnumbered section) — "How To Read This Report"
```html
<section>
    <h2 class="sec-h">How To Read This Report</h2>
    <div class="legend">
      <dl style="margin:0">
        <dt><term></dt><dd><definition, <b>bold</b>/<i>italic</i> as needed></dd>
        <!-- repeat -->
      </dl>
    </div>
</section>
```
This legend is effectively boilerplate — the design ships a fixed set of ~11 terms (Live mid-session run, Weekend gap, 1-Week implied move, "Quiet" row, Direction Split, Big Move Ranking, Risk dials, The Cushion, Whole-number levels, Breadth read). Reuse verbatim unless report semantics change.

---

## DIAL / COLOR CLASS REFERENCE

Risk dial pills (`.dialpill` + one modifier), lowest→highest severity:
| class | label | look | when |
|---|---|---|---|
| `.d-calm` | Calm | green | lowest implied size + intact cushion |
| `.d-elev` | Elevated | amber (`--warn`) | modestly elevated |
| `.d-high` | High | orange | wide band / thin cushion |
| `.d-ext` | Extreme | red | widest band + fragile |

Gauge-value text color pairs with dial: green `#22c55e` (Calm), `#f59e0b` (Elevated), `#f97316` (High), `#ef4444` (Extreme).

Directional text: `.pos`/`.up`/`.dfup` = green (up); `.neg`/`.dn`/`.dfdn`/`.chip.r` = red (down/resistance); `.flat`/`.muted` = neutral; `.flip`/`.chip.f`/`.accent` = cyan (flip/range levels); `.chip.s` = green (support).

## DYNAMIC vs FIXED — quick rule
- **Fixed every run:** all class names, table header rows, section titles/numbers, gauge SVG arcs & pivot, legend terms, DO/DON'T & cushion/weekhead headings, entities, the `.sub` line, nav.
- **Dynamic every run:** all numeric cells (price/pct/pts/odds/levels), bar/needle geometry (`width:`, gauge `<line>` x2/y2), which dial class + label, which text-color class, `.done` flags, `est` markers, `.cushion` vs `.cushion.thin`, and all prose.
