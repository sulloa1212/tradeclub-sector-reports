"""Deterministic SVG gauges + the 'Today's Dashboard' block.

Dials are rendered in Python (correct arc math) and injected into the report at
the <!--DASHBOARD--> marker by the generator — never left to the LLM to draw.
The directional lean is a transparent heuristic (trend + momentum + volatility);
it is a lean, not a prediction, and is labeled as such in the report.
"""
from __future__ import annotations

import math
from typing import Optional


def _color(pct: float) -> str:
    return "#22c55e" if pct >= 55 else ("#ef4444" if pct <= 45 else "#f59e0b")


def gauge(pct: float, big: str, sub: str, color: str) -> str:
    pct = max(0.0, min(100.0, float(pct)))
    th = math.radians(180 * (1 - pct / 100))
    ex = 100 + 78 * math.cos(th)
    ey = 100 - 78 * math.sin(th)
    track = "M 22 100 A 78 78 0 0 1 178 100"
    val = f"M 22 100 A 78 78 0 0 1 {ex:.1f} {ey:.1f}"
    return ('<svg viewBox="0 0 200 120" style="width:100%;max-width:160px;height:auto">'
            f'<path d="{track}" fill="none" stroke="#1d242f" stroke-width="13" stroke-linecap="round"/>'
            f'<path d="{val}" fill="none" stroke="{color}" stroke-width="13" stroke-linecap="round"/>'
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="6.5" fill="{color}"/>'
            f'<text x="100" y="92" text-anchor="middle" font-size="30" font-weight="800" fill="#e8edf3">{big}</text>'
            f'<text x="100" y="112" text-anchor="middle" font-size="12" fill="#9aa7b6">{sub}</text>'
            '</svg>')


def directional_lean(tech: Optional[dict], vix: Optional[float]) -> int:
    """Transparent heuristic -> probability (0-100) of an up move. Blends trend,
    RSI momentum, and price-vs-MA, then dampens conviction toward 50 when VIX is
    elevated. Capped 35-65 (this is a lean, not a forecast)."""
    if not tech:
        return 50
    p = 50.0
    if tech.get("trend") == "up":
        p += 6
    elif tech.get("trend") == "down":
        p -= 6
    rsi = tech.get("rsi14")
    if rsi is not None:
        if rsi > 55:
            p += 4
        elif rsi < 45:
            p -= 4
        if rsi > 70:
            p -= 3          # overbought caution
        elif rsi < 30:
            p += 3          # oversold bounce
    last, ma20 = tech.get("last"), tech.get("ma20")
    if last and ma20:
        p += 3 if last > ma20 else -3
    if vix and vix > 22:    # high vol -> lower conviction
        p = 50 + (p - 50) * 0.6
    return int(round(max(35, min(65, p))))


def _put_call(ovol) -> Optional[float]:
    """Best-effort put/call ratio from a UW options-volume payload (field names
    confirmed on first live run; returns None if not found)."""
    rows = ovol if isinstance(ovol, list) else ([ovol] if isinstance(ovol, dict) else [])
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k in ("put_call_ratio", "putCallRatio", "pc_ratio", "put_call"):
            if r.get(k) is not None:
                try:
                    return float(r[k])
                except (TypeError, ValueError):
                    pass
        pv = r.get("put_volume") or r.get("putVolume") or r.get("puts")
        cv = r.get("call_volume") or r.get("callVolume") or r.get("calls")
        try:
            if pv is not None and cv:
                return float(pv) / float(cv)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return None


def options_implied_lean(tk_data: Optional[dict]) -> Optional[int]:
    """Directional probability (0-100) from options positioning. Uses the put/call
    ratio as the directional tilt (centered ~0.9: lower = bullish, higher =
    bearish) and dampens conviction when ATM IV is very high. Returns None if the
    UW options data isn't present, so the dashboard falls back to the technical
    heuristic. (Upgrade path: add 25-delta risk-reversal skew when available.)"""
    if not tk_data:
        return None
    pcr = _put_call(tk_data.get("options_volume"))
    if pcr is None:
        return None
    p = 50.0 + max(-15.0, min(15.0, (0.90 - pcr) * 30))
    iv = _atm_iv(tk_data.get("interpolated_iv"))
    if iv and iv > 0.35:            # very high IV -> less directional conviction
        p = 50 + (p - 50) * 0.7
    return int(round(max(35, min(65, p))))


def _atm_iv(iv_payload) -> Optional[float]:
    cands = ("atm_iv", "implied_volatility", "iv", "interpolated_iv", "iv_atm")
    rows = iv_payload if isinstance(iv_payload, list) else ([iv_payload] if isinstance(iv_payload, dict) else [])
    for r in rows:
        if isinstance(r, dict):
            for k in cands:
                if r.get(k) is not None:
                    try:
                        v = float(r[k])
                        return v / 100 if v > 3 else v
                    except (TypeError, ValueError):
                        pass
    return None


def _vix(packet: dict) -> Optional[float]:
    rv = (packet.get("macro") or {}).get("rates_vol") or {}
    v = rv.get("VIX")
    return v.get("last") if isinstance(v, dict) else None


def render_dashboard(packet: dict, mode: str = "premarket") -> str:
    """Build the 'Today's Dashboard' section (4 index dials + Fear & Greed)."""
    tech = (packet.get("macro") or {}).get("technicals") or {}
    per_tk = (packet.get("institutional") or {}).get("per_ticker") or {}
    vix = _vix(packet)
    horizon = "next session" if mode == "postmarket" else "today"
    # (display name, symbol, technicals key, UW ETF proxy for options-implied)
    indices = [("S&amp;P 500", "SPX", "S&P 500", "SPY"),
               ("Nasdaq 100", "NDX", "Nasdaq Composite", "QQQ"),
               ("Dow", "DJX", "Dow Jones", "DIA"),
               ("Russell 2000", "RUT", "Russell 2000", "IWM")]
    cards = ""
    used_options = False
    for nm, sym, key, etf in indices:
        p = options_implied_lean(per_tk.get(etf) if isinstance(per_tk, dict) else None)
        if p is not None:
            used_options = True
        else:
            p = directional_lean(tech.get(key) if isinstance(tech, dict) else None, vix)
        cards += (f'<div class="mcard" style="text-align:center">'
                  f'<div class="k">{nm} &middot; <b style="color:var(--ink)">{sym}</b></div>'
                  f'{gauge(p, str(p) + "%", "prob. up", _color(p))}'
                  f'<div class="d">&#9650; {p}% up / &#9660; {100 - p}% down</div></div>')
    fg = (packet.get("sentiment") or {}).get("fear_greed") or {}
    score = fg.get("score")
    if score is not None:
        rating = fg.get("rating") or ("Fear" if score < 45 else "Greed" if score > 55 else "Neutral")
        fg_color = "#ef4444" if score < 45 else "#22c55e" if score > 55 else "#f59e0b"
        cards += ('<div class="mcard" style="text-align:center">'
                  '<div class="k">Fear &amp; Greed</div>'
                  f'{gauge(score, str(int(score)), rating, fg_color)}'
                  '<div class="d">0 = fear &middot; 100 = greed</div></div>')
    method = ("derived from <b>options positioning</b> (put/call &amp; implied vol)"
              if used_options else
              "a blend of trend, momentum and volatility")
    return (
        '\n  <section>\n'
        f'    <div class="sec-title">&#128202; Today\'s Dashboard &mdash; Directional Lean ({horizon}) &amp; Mood</div>\n'
        f'    <div class="macro-grid">{cards}</div>\n'
        '    <p style="font-size:12px;color:var(--faint);margin-top:10px"><b>How to read:</b> '
        f'each dial is the estimated chance of an <b>up move {horizon}</b> for that index, {method}. '
        'A lean, not a prediction; manage risk. Fear &amp; Greed shows market mood.</p>\n'
        '  </section>\n'
    )


# --- Index bias dials (-100 extreme bearish .. +100 extreme bullish) ----------
# A semicircular NEEDLE gauge (distinct from the prob-up arcs above): the hand
# points to a synthesized 1-period directional bias for each index. Injected at
# the <!--INDEX-DIALS--> marker in the Technical Analysis section.

def directional_bias(tech: Optional[dict], vix: Optional[float]) -> int:
    """Transparent heuristic -> directional bias on a -100..+100 scale. Blends
    trend, RSI momentum, and price-vs-MA(20/50); dampens toward 0 when VIX is
    elevated. A lean, not a forecast (capped ±85)."""
    if not tech:
        return 0
    b = 0.0
    trend = tech.get("trend")
    if trend == "up":
        b += 30
    elif trend == "down":
        b -= 30
    rsi = tech.get("rsi14") if tech.get("rsi14") is not None else tech.get("rsi")
    if rsi is not None:
        if rsi > 55:
            b += 12
        elif rsi < 45:
            b -= 12
        if rsi > 70:
            b -= 8          # overbought fade
        elif rsi < 30:
            b += 8          # oversold bounce
    last, ma20, ma50 = tech.get("last"), tech.get("ma20"), tech.get("ma50")
    if last and ma20:
        b += 15 if last > ma20 else -15
    if last and ma50:
        b += 10 if last > ma50 else -10
    if vix and vix > 22:
        b *= 0.7
    return int(round(max(-85, min(85, b))))


def _bias_pt(v: float, R: float) -> tuple[float, float]:
    f = (v + 100) / 200.0
    th = math.radians(180 * (1 - f))
    return (100 + R * math.cos(th), 100 - R * math.sin(th))


def _bias_arc(v1: float, v2: float, R: float, color: str, w: float) -> str:
    x1, y1 = _bias_pt(v1, R)
    x2, y2 = _bias_pt(v2, R)
    return (f'<path d="M {x1:.1f} {y1:.1f} A {R} {R} 0 0 1 {x2:.1f} {y2:.1f}" '
            f'fill="none" stroke="{color}" stroke-width="{w}" stroke-linecap="round"/>')


def bias_dial(value: float, name: str, sym: str) -> str:
    v = max(-100.0, min(100.0, float(value)))
    col = "#22c55e" if v >= 20 else ("#ef4444" if v <= -20 else "#f59e0b")
    if v >= 60:
        lean = "Strongly Bullish"
    elif v >= 20:
        lean = "Bullish"
    elif v > -20:
        lean = "Neutral"
    elif v > -60:
        lean = "Bearish"
    else:
        lean = "Strongly Bearish"
    nx, ny = _bias_pt(v, 58)
    svg = (
        '<svg viewBox="0 0 200 126" style="width:100%;max-width:190px;height:auto">'
        + _bias_arc(-100, -20, 78, "#7f2a2a", 12)
        + _bias_arc(-20, 20, 78, "#7a5a14", 12)
        + _bias_arc(20, 100, 78, "#1f6b37", 12)
        + f'<line x1="100" y1="100" x2="{nx:.1f}" y2="{ny:.1f}" stroke="{col}" stroke-width="3.6" stroke-linecap="round"/>'
        + f'<circle cx="100" cy="100" r="6.5" fill="{col}"/>'
        + '<text x="14" y="112" text-anchor="middle" font-size="9" fill="#6b7787">-100</text>'
        + '<text x="100" y="16" text-anchor="middle" font-size="9" fill="#6b7787">0</text>'
        + '<text x="186" y="112" text-anchor="middle" font-size="9" fill="#6b7787">+100</text>'
        + '</svg>'
    )
    sign = "+" if v > 0 else ""
    return ('<div class="dial-card">'
            f'<div class="dial-top">{name} &middot; <b>{sym}</b></div>'
            f'{svg}'
            f'<div class="dial-val" style="color:{col}">{sign}{int(round(v))}</div>'
            f'<div class="dial-lean" style="color:{col}">{lean}</div>'
            '</div>')


def render_index_bias_dials(packet: dict, mode: str = "premarket") -> str:
    """Row of −100→+100 needle dials for SPX/NDX/RUT/DJX, from computed technicals."""
    tech = (packet.get("macro") or {}).get("technicals") or {}
    vix = _vix(packet)
    horizon = "next session" if mode == "postmarket" else "today"
    indices = [("S&amp;P 500", "SPX", "S&P 500"),
               ("Nasdaq 100", "NDX", "Nasdaq Composite"),
               ("Russell 2000", "RUT", "Russell 2000"),
               ("Dow", "DJX", "Dow Jones")]
    cards = "".join(
        bias_dial(directional_bias(tech.get(key) if isinstance(tech, dict) else None, vix), nm, sym)
        for nm, sym, key in indices
    )
    return (f'<div class="dials">{cards}</div>\n'
            '<p style="font-size:12px;color:var(--faint);margin:-2px 0 14px">'
            '&#9650; Dials show a synthesized <b>directional bias</b> for the '
            f'<b>{horizon}</b> on a <b>-100 (extreme bearish) &rarr; +100 (extreme bullish)</b> '
            'scale (trend, momentum, price-vs-MA, dampened by volatility). A lean, not a prediction.</p>')
