# Trade Club AI Report — Style Guide

The visual language behind the Sector Tracker reports, so any project can produce a report that looks like it came from the same shop. Everything is one self-contained HTML file: no build step, no external CSS/JS, no fonts to load.

## Design intent

Dark "terminal" aesthetic for a trader audience. Green = bullish/long, red = bearish/short, grey = neutral, amber = warning/event-risk. Dense but scannable: a 60-second read up top, then progressively more detail. Single column, max 1080px, fully responsive down to phone width.

## Color tokens

All colors live in `:root` as CSS variables. Change them in one place to re-skin the whole report.

| Token | Hex | Use |
|---|---|---|
| `--bg` | `#0e1117` | Page background |
| `--panel` | `#161b24` | Card / panel background |
| `--panel2` | `#1d242f` | Inset background (meters, bar tracks, legend) |
| `--line` | `#2a3340` | Borders, dividers |
| `--ink` | `#e8edf3` | Primary text |
| `--muted` | `#9aa7b6` | Secondary text |
| `--faint` | `#6b7787` | Labels, captions |
| `--bull` / `--bull-dim` | `#22c55e` / `#16331f` | Bullish green / its dim fill |
| `--bear` / `--bear-dim` | `#ef4444` / `#3a1c1c` | Bearish red / its dim fill |
| `--neutral` / `--neutral-dim` | `#9aa7b6` / `#22262d` | Neutral grey / its dim fill |
| `--accent` / `--accent2` | `#29b6f6` / `#2196f3` | Links, eyebrow, section accent bar |
| `--warn` | `#f59e0b` | Warnings, event risk, "fights it" fuel |

Quick value-color helpers: `.pos` (green), `.neg` (red), `.flat` (grey). Use on any element to tint it.

## Layout skeleton

```
.wrap (max 1080px, centered)
 ├─ .header        logos + title block
 ├─ .tldr          "The 60-Second Read"
 ├─ section        Macro strip (.macro-grid of .mcard)
 ├─ section        Ranked bar chart (#rank, JS-driven)
 ├─ section        Bullish drill-down (.drill-grid + .stack of .scard)
 ├─ section        Bearish drill-down
 ├─ section        Cross-currents (.twocol of .panel)
 ├─ section        Event calendar (table)
 ├─ section        Legend (.legend dl)
 └─ .footer        disclaimer + timestamp
```

Every `section` is independent — delete any you don't need.

## Components

**Header** — `.header` flexbox. `.brand-tc` is the left logo (~168px), `.brand-mw` is the small top-right logo (~62px, absolutely positioned). `.head-text` holds `.eyebrow` (accent caps label), `h1`, `.sub` (coverage/horizon), `.stamp` (timestamp + freshness note).

**TL;DR** — `.tldr` gradient box, `h2` headline, `ul` of dashed-divider `li`. Inside each `li`, wrap the lead-in in `<span class="lead">`; add `.pos` / `.neg` / `.t-warn` to tint it.

**Macro strip** — `.macro-grid` auto-fits `.mcard`s. Each card: `.k` (metric label), `.v` (big value — add `.pos`/`.neg`/`.flat`), `.d` (one-line implication). Add as many cards as you like; they wrap automatically.

**Ranked bar chart** — diverging horizontal bars built by the `<script>` at the bottom from the `data` array (`[name, etf, score]`, score −100..+100). Positive bars grow right in green, negative grow left in red, from a center line. ETFs listed in `topBull` / `topBear` get a "Pick" pill. This is the only JS in the file.

**Sector gauge cards** — `.gauge-card.bull` / `.gauge-card.bear` (colored top border). `h3` + `.etf` ticker, `.gnum` big score (`.pos`/`.neg`), `.tag` pills for direction + conviction, `.gmeta` thesis text.

**Stock cards** — `.scard` is the workhorse. Anatomy top to bottom:
- `.top` → `.tk` ticker + `.co` company, and a direction `.tag` (`t-bull` LONG / `t-bear` SHORT).
- `.meter` + `.meter-lbl` → Swing-Conviction bar (set `<i style="width:NN%">`).
- `.meter.health` + `.meter-lbl` → Fundamental Health bar, with a `.fuel` pill (see below).
- `.fund` → one-line fundamentals (rev/EPS/margins/FCF).
- `.thesis` → the play in 1–2 sentences.
- `.pts` → bulleted supporting points.
- `.risk` → red-tinted "biggest risk" callout.
- `.levels` → `.lv` chips for Price / Support / Resistance.
- `.earn` → next-earnings line; flag with amber if inside the swing window.

**Fuel pills** — `.fuel.fuel-add` (⛽ Adds Fuel, green), `.fuel.fuel-neutral` (grey), `.fuel.fuel-fight` (⛽ Fights It, amber). Use the ⛽ glyph `&#9981;`.

**Tags** — `.tag` base + `.t-bull` / `.t-bear` / `.t-neut` / `.t-warn`.

**Panels & cross-currents** — `.panel` generic card; `.twocol` puts two side by side (collapses to one column on mobile).

**Event table** — plain `table` inside a `.panel`. First cell of each row uses `td.dt` (nowrap date column).

**Legend** — `.legend` dashed box with a `dl`. Mostly static; reuse as-is.

**Footer** — `.footer` with a `.disc` disclaimer card and two timestamp lines.

## Conventions worth keeping

- Scores are signed and zero-centered: −100..+100 for direction, 0..100 for stock/health meters.
- Meters are driven by inline `style="width:NN%"` — the number IS the score.
- Earnings inside the swing window get an amber `⚠` so event risk is never hidden.
- Every report ends with the legend + the not-investment-advice disclaimer.
- Emoji are used sparingly as signposts: 🎯 (TL;DR), ⚠ (risk), 📅 (earnings), ⛽ (fuel).

## Re-skinning for a non-trading project

The structure generalizes to any "ranked items with drill-downs" report. To repurpose:
1. Edit the `:root` tokens (swap accent/bull/bear to your palette).
2. Rename the semantic classes in your head only — bull/bear can mean good/bad, up/down, pass/fail.
3. Replace logos (see README) or strip branding.
4. Keep the component grammar: TL;DR → ranked overview → drill-down cards → calendar/table → legend → footer.
