# CONTENT-FILL CONTRACT — templated report (AUTHORITATIVE)

This report uses a FIXED design template. The page shell — `<!doctype>`, `<html>`,
`<head>` and ALL its CSS, the header with both logos, the section nav, any chart
`<script>`, the footer, and the legal disclaimer — is fixed and assembled
automatically. Your job is to write ONLY the body content that fills the template,
using the EXACT component markup in the BLUEPRINT below.

## Output — in this order, and NOTHING else
1. **The section HTML** that replaces the content marker: the report's sections, in
   the blueprint's order, each built with the blueprint's EXACT markup (same tags,
   same class names, same structure, same colors/dials/meters). Fill every data
   field from your live research.
   - Do NOT emit `<!doctype>`, `<html>`, `<head>`, `<style>`, `<header>`, `<nav>`,
     `<footer>`, `<script>`, the logos, or the disclaimer — those are FIXED.
   - Do NOT write a decorative header comment, and NEVER reproduce or mention the
     `<!--REPORT-CONTENT-->` marker text — begin directly with the first section.
   - Your section `id`s MUST match the nav buttons already in the template (the
     blueprint lists the exact ids). Do not rename or drop them.
   - For any chart the blueprint says is script-driven, emit ONLY the data it
     specifies (e.g. the ranking `data`/`topBull`/`topBear` arrays or the host
     element) — never hand-write the generated rows, and never restate the script.
2. **One fenced ```json block — the SIDECAR — as the VERY LAST thing**, exact shape:
```json
{
  "report": "<report name>",
  "date": "YYYY-MM-DD",
  "status_label": "<short badge text>",
  "accent": "bull | bear | neutral | warn",
  "headline": "<today's one-line takeaway>",
  "metric": { "type": "gauge", "value": 0, "min": -100, "max": 100 },
  "title": "<the exact H1 title text for today>",
  "run_type": "<short run label, e.g. Pre-Open / Weekend / Holiday / Mid-Session>",
  "stamp": "<the short stamp tagline shown under the date>"
}
```
- `metric`: gauge `{"type":"gauge","value":<int -100..100>,...}` for a directional
  read, else `{"type":"text","value":"<short>"}`.
- `title` / `run_type` / `stamp` fill the template's header placeholders — write
  them exactly as they should appear, short.

## Rules (authoritative)
- The **BLUEPRINT below is the authoritative output structure.** Where the report's
  methodology prompt describes a header, nav, footer, sections, classes, or any
  other chrome, IGNORE that and DEFER to the blueprint's exact markup — the goal is
  a page visually identical to the approved design.
- Reproduce the design's classes and structure EXACTLY. Never invent markup or
  styling; the fixed CSS only styles the design's own classes, so an off-spec class
  renders unstyled.
- **Write for a novice first:** lead each section with one plain-English sentence on
  what it means before any table/number; explain a term in ≤6 words the first time;
  keep prose tight. Simplify the wording — never drop a required row, section, or
  number the design/methodology calls for.
- **Bold discipline:** `<b>`/`<strong>` only on SHORT anchors — a ticker, a
  price/level, or a 2–5 word lead-in label ending in a colon. Never bold a whole
  sentence or any run longer than ~40 characters (the verbatim disclaimer's own
  bold spans are the only exception); for sentence-level emphasis use the design's
  color classes without bold, sparingly.
- Never fabricate a price, level, ratio, or date. If something can't be verified,
  say so plainly rather than inventing it.
