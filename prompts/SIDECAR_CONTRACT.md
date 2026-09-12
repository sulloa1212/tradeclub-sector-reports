# Common sidecar contract (v1) — APPROVED

Every report's prompt MUST end its reply with a single fenced ```json block (the
"sidecar"). `build.py` parses it to build that report's card on the hub. Same
shape for every report, regardless of the report's internal content.

## Fields

| Field | Type | Card use |
|---|---|---|
| `report` | string | card title, e.g. `"Gap Scout Report"` |
| `date` | string | `YYYY-MM-DD`, the date stamp |
| `status_label` | string | the badge text, e.g. `"ELEVATED"`, `"12 SETUPS"`, `"BULLISH TILT"` |
| `accent` | string | one of `bull` \| `bear` \| `neutral` \| `warn` — drives the badge/border color |
| `headline` | string | today's one-line takeaway (changes every run) |
| `metric` | object | the headline figure; see the two shapes below |

### `metric` shapes

**Gauge** — for reports with a −100..+100 score (Sector Intelligence, or a
directional lean). Renders the red→amber→green bar with a marker at `value`
(the same component the hub sector cards already use):
```json
"metric": { "type": "gauge", "value": 58, "min": -100, "max": 100 }
```

**Text** — for reports without a single bidirectional score (Mean Reversion):
```json
"metric": { "type": "text", "value": "12 setups" }
```

(A `dial` type — Calm→Elevated→High→Extreme — may be added later for Gap Risk.)

## Full example (Sector Intelligence)
```json
{
  "report": "Sector Intelligence Report",
  "date": "2026-06-24",
  "status_label": "BULLISH TILT",
  "accent": "bull",
  "headline": "Tech leads; energy the lone laggard.",
  "metric": { "type": "gauge", "value": 58, "min": -100, "max": 100 }
}
```

## Notes
- The static "what is this report" blurb is NOT in the sidecar — it lives in
  `reports.json` (`description`), unchanged day to day.
- `headline` is the dynamic, per-run summary; `description` is the fixed blurb.
- Keep the sidecar as the LAST thing in the reply, after the closing `</html>`
  is forbidden — the HTML report comes first, the ```json sidecar last (same
  rule the current sector prompt already enforces).
