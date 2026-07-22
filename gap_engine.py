"""gap_engine.py — deterministic Gap Risk report engine (v2).

v1 ported the standalone builder's drift+skew math (2026-07-14). v2 ports the
v12 developer handoff (2026-07-20):

  • COMPUTED dials — variance-consistent thresholds shared across horizons;
    the model's dial_bump is gone ("a dial that silently keeps yesterday's
    setting is worse than no dial").
  • COMPUTED gamma — the regime now arrives from the feed (UW greek-exposure),
    not from model judgment; the cushion copy follows it (no cushion line on
    thin, "dealers amplify" on negative).
  • The computed-vs-judgment invariant: every number that appears in prose is
    derived from the computed stats here; the model supplies ONLY the STORY
    (headline / catalyst / watch / per-index driver+gapfill+tail), levels,
    catalyst nudges and the search-verified calendar/clock/playbook.
  • Breakeven calculator v2 — three horizons including REST OF DAY (local
    clock, 1-day IV, U-shaped intraday variance), touch odds via first-passage
    math, spot re-anchoring, IV point-change what-ifs, staleness warnings.

Orchestrated by build.py:
  1. gap_feed.fetch_all()  -> levels/trend/futures (yfinance) + chain IV +
                              gamma regime (Unusual Whales, with fallbacks)
  2. run_context() + compute(tolerant=True) -> preliminary math
  3. data_packet()         -> injected into the model prompt
  4. model returns the STORY-shaped content JSON
  5. compute() again with catalyst nudges + any estimates -> final numbers
  6. render()              -> the full standalone HTML page

Model text may embed tokens {LEAN_NDX} {LEAN_SPX} {LEAN_RUT} {LEAN_DJX}
{LEAN_LO} {LEAN_HI} — substituted with FINAL computed leans at render time.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# ── tunables (identical to the standalone v11 builder) ──────────────────────
OVN = 0.57                       # overnight share of full-session variance
ON_THR = [0.5, 1.0, 1.5, 2.0]    # overnight band thresholds (%)
WK_THR = [1.0, 2.0, 3.0, 4.0]    # weekly band thresholds (%)
BIGMOVE_THR = 3.0                # rank weekly by P(|move| > 3%)
SKEW_TENOR = 0.65                # weekly skew flattens: r_wk = 1 + (r-1)*this
DRIFT_K = 0.45                   # max center shift for a full ±1 signal (in σ)
DRIFT_TENOR = 0.55               # damp a one-night signal at the weekly tenor

# Rest-of-day horizon (breakeven calculator only)
RD_SKEW_TENOR = 0.80             # intraday skew a bit flatter than overnight
RD_DRIFT = 0.35                  # over a few hours drift is mostly noise
UVOL_K = 1.50                    # intraday variance U-shape (open/close heavy)
VOL_BETA = 1.0                   # spot-down ≈ +1 vol pt per −1% (suggestion only)

SIG_W_PREOPEN = dict(fut=0.45, day=0.15, trend=0.22, mom=0.10, gamma=0.08)
SIG_W_NOFUT = dict(fut=0.00, day=0.50, trend=0.22, mom=0.12, gamma=0.16)

# Skew ratios per index (put-skew steepness) — hand-set by design: UW's 25Δ
# risk reversal does not map cleanly onto the two-piece-normal r.
R_DEFAULT = {"ndx": 1.28, "rut": 1.24, "spx": 1.22, "djx": 1.16}
BAR = {"ndx": "#ef4444", "rut": "#f97316", "spx": "#f97316", "djx": "#f59e0b"}
NAMES = {
    "ndx": ("NDX", "Nasdaq-100 &middot; QQQ", "QQQ"),
    "rut": ("RUT", "Russell 2000 &middot; IWM", "IWM"),
    "spx": ("SPX", "S&amp;P 500 &middot; SPY", "SPY"),
    "djx": ("DJX", "Dow Jones &middot; DIA", "DIA"),
}
BOARD_ORDER = ["ndx", "rut", "spx", "djx"]
BE_ORDER = ["spx", "ndx", "rut", "djx"]

# ── risk dials: COMPUTED, variance-consistent across horizons ───────────────
# The two dials describe the same vol regime at different horizons, so the
# overnight thresholds are derived from the weekly ones rather than invented:
# wk_sig = vol*sqrt(5/252) uses cuts 1.5/2.5/3.5; on_sig = vol*sqrt(1/252)*OVN
# gets the SAME vol cuts rescaled (~0.38/0.64/0.89). "Elevated" therefore means
# the same underlying vol on both rows. on_sig carries the weekend bump, so a
# Friday run can read one notch higher than the same vol midweek — intended.
DIALS = ["Calm", "Elevated", "High", "Extreme"]
_WK_CUTS = (1.5, 2.5, 3.5)
_ON_CUTS = tuple(c / math.sqrt(5 / 252) * (math.sqrt(1 / 252) * OVN) for c in _WK_CUTS)
DIALCOLOR = {"Extreme": "#ef4444", "High": "#f97316", "Elevated": "#f59e0b", "Calm": "#22c55e"}
DIALPILL = {"Extreme": "d-ext", "High": "d-high", "Elevated": "d-elev", "Calm": "d-calm"}
NEEDLE = {"Calm": (70, 40), "Elevated": (122, 26), "High": (160, 40), "Extreme": (190, 70)}

# US market full-closure holidays (maintain yearly; half-days not modelled).
HOLIDAYS = {
    (2026, 1, 1), (2026, 1, 19), (2026, 2, 16), (2026, 4, 3), (2026, 5, 25), (2026, 6, 19),
    (2026, 7, 3), (2026, 9, 7), (2026, 11, 26), (2026, 12, 25),
    (2027, 1, 1), (2027, 1, 18), (2027, 2, 15), (2027, 3, 26), (2027, 5, 31), (2027, 6, 18),
    (2027, 7, 5), (2027, 9, 6), (2027, 11, 25), (2027, 12, 24),
}
OPEN_T, CLOSE_T = time(9, 30), time(16, 0)


def Phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _clip(x, a=-1.0, b=1.0):
    return max(a, min(b, x))


# ── run context ─────────────────────────────────────────────────────────────
def _is_trading_day(d):
    return d.weekday() < 5 and (d.year, d.month, d.day) not in HOLIDAYS


def _session_label(dt):
    if not _is_trading_day(dt.date()):
        return "WEEKEND / HOLIDAY RUN"
    t = dt.time()
    if t < OPEN_T:
        return "PRE-MARKET RUN"
    if t < CLOSE_T:
        return "LIVE MID-SESSION RUN"
    return "POST-MARKET RUN"


def _next_session_open(dt):
    d = dt.date()
    if _is_trading_day(d) and dt.time() < OPEN_T:
        return datetime.combine(d, OPEN_T, ET)
    nd = d + timedelta(days=1)
    while not _is_trading_day(nd):
        nd += timedelta(days=1)
    return datetime.combine(nd, OPEN_T, ET)


def run_context(now_et: datetime | None = None) -> dict:
    dt = now_et or datetime.now(ET)
    label = _session_label(dt)
    nxt = _next_session_open(dt)
    closed = (nxt.date() - dt.date()).days
    next_day = nxt.strftime("%A")
    if closed == 0:
        phrase = "gap into today&rsquo;s open"
    elif closed == 1:
        phrase = f"overnight gap into {next_day}"
    elif next_day == "Monday":
        phrase = "weekend gap into Monday"
    else:
        phrase = f"holiday gap into {next_day}"
    gap_word = ("Weekend" if next_day == "Monday" else "Holiday") if closed >= 2 else "Overnight"
    h12 = dt.hour % 12 or 12
    return dict(
        dt=dt, label=label, next_day=next_day, phrase=phrase,
        weekend=1.25 if closed >= 2 else 1.0, gap_word=gap_word,
        premarket=(label == "PRE-MARKET RUN"),
        long_date=f'{dt.strftime("%A, %B")} {dt.day}, {dt.year}',
        time_str=f'~{h12}:{dt.minute:02d} {"AM" if dt.hour < 12 else "PM"} ET',
        gen_date=dt.strftime("%Y-%m-%d"),
        gen_iso=dt.strftime("%Y-%m-%dT%H:%M:00"),
    )


# ── drift + skew math (verbatim port) ───────────────────────────────────────
def compute_signal(vol, day_pct, fut_pct=None, above_sma20=True, above_sma50=True,
                   ma_rising=True, mom5_pct=0.0, gamma="thin", catalyst_adj=0.0):
    day_sig = vol * math.sqrt(1 / 252)
    f = _clip(fut_pct / day_sig) if fut_pct is not None else 0.0
    d = _clip(day_pct / day_sig)
    t = (0.5 * (1 if above_sma20 else -1) + 0.3 * (1 if above_sma50 else -1)
         + 0.2 * (1 if ma_rising else -1))
    mo = _clip(mom5_pct / (2 * day_sig))
    g = {"pos": 0.30, "neg": -0.30}.get(gamma, 0.0)
    W = SIG_W_PREOPEN if fut_pct is not None else SIG_W_NOFUT
    raw = W["fut"] * f + W["day"] * d + W["trend"] * t + W["mom"] * mo + W["gamma"] * g + catalyst_adj
    return round(_clip(raw), 3)


def fmt(v, lvl):
    return f"{v:,.1f}" if lvl < 1000 else f"{v:,.0f}"


def split_stats(lvl, sig_pct, r, thresholds, signal=0.0):
    """One CDF of the drift-shifted two-piece normal -> lean + disjoint bands."""
    su = 2 * sig_pct / (1 + r)
    sd = r * su
    wd = sd / (sd + su)
    wu = su / (sd + su)
    mu = DRIFT_K * signal * sig_pct

    def F(L):
        if L <= mu:
            return wd * (2 * Phi((L - mu) / sd))
        return wd + wu * (2 * Phi((L - mu) / su) - 1)

    lean_dn = F(0.0)
    rows = []
    prev = 0.0
    for x in thresholds:
        pdn = F(-prev) - F(-x)
        pup = F(x) - F(prev)
        label = (f"&le;{x:g}%" if prev == 0 else f"{prev:g}&ndash;{x:g}%")
        rows.append(dict(
            x=x, pdn=pdn, pup=pup, first=(prev == 0), label=label,
            pdn_pct=round(pdn * 100), pup_pct=round(pup * 100),
            price_dn=fmt(lvl * (1 - x / 100), lvl), price_up=fmt(lvl * (1 + x / 100), lvl),
            wdn=min(round(pdn * 100 * 2), 100), wup=min(round(pup * 100 * 2), 100)))
        prev = x
    xl = thresholds[-1]
    pdn = F(-xl)
    pup = 1 - F(xl)
    rows.append(dict(
        x=None, pdn=pdn, pup=pup, first=False, tail=True, label=f"&gt;{xl:g}%",
        pdn_pct=round(pdn * 100), pup_pct=round(pup * 100),
        price_dn="&lt;" + fmt(lvl * (1 - xl / 100), lvl),
        price_up="&gt;" + fmt(lvl * (1 + xl / 100), lvl),
        wdn=min(round(pdn * 100 * 2), 100), wup=min(round(pup * 100 * 2), 100)))
    return dict(rows=rows, lean_dn=lean_dn, lean_up=1 - lean_dn, sd=sd, su=su, mu=mu)


def _dial(sig, cuts):
    lo, mid, hi = cuts
    return "Calm" if sig < lo else "Elevated" if sig < mid else "High" if sig < hi else "Extreme"


def compute(feed: dict, ctx: dict, judgment: dict | None = None,
            tolerant: bool = False) -> dict:
    """feed = gap_feed.fetch_all() output (levels/trend + chain IV + gamma).
    judgment (optional) = per-index model fields {catalyst_adj, lvl_est,
    day_est, vol_est}. Gamma is NOT a judgment field — it comes from the feed.
    tolerant=True (preliminary pass) skips indices missing level/vol instead
    of raising so the data packet can flag them to the model."""
    judgment = judgment or {}
    out = {}
    for key in BOARD_ORDER:
        f = dict(feed[key])
        j = judgment.get(key, {})
        if f["lvl"] is None and j.get("lvl_est") is not None:
            f["lvl"], f["day"], f["est"] = float(j["lvl_est"]), float(j.get("day_est") or 0.0), True
        if f["vol"] is None and j.get("vol_est") is not None:
            f["vol"], f["vol_live"] = float(j["vol_est"]), False
            f["vol_src"] = "est:model"
        if f["lvl"] is None or f["vol"] is None:
            if tolerant:
                continue
            raise ValueError(f"{key}: missing level/vol and no model estimate supplied")
        nm, co, etf = NAMES[key]
        r = R_DEFAULT[key]
        gamma = f.get("gamma") or "thin"
        adj = _clip(float(j.get("catalyst_adj") or 0.0), -0.3, 0.3)
        sig = compute_signal(
            f["vol"], f["day"] or 0.0, f["fut_pct"],
            bool(f["above_sma20"]) if f["above_sma20"] is not None else True,
            bool(f["above_sma50"]) if f["above_sma50"] is not None else True,
            bool(f["ma_rising"]) if f["ma_rising"] is not None else True,
            f["mom5_pct"] or 0.0, gamma, adj)
        lvl, vol = f["lvl"], f["vol"]
        on_sig = vol / 100 * math.sqrt(1 / 252) * OVN * ctx["weekend"] * 100
        wk_sig = vol / 100 * math.sqrt(5 / 252) * 100
        r_wk = 1 + (r - 1) * SKEW_TENOR
        ix = dict(
            key=key, nm=nm, co=co, etf=etf, bar=BAR[key],
            lvl=lvl, day=f["day"] or 0.0, est=f["est"], vol=vol,
            vol_live=f["vol_live"], vol_src=f.get("vol_src"),
            vol1d=f.get("vol1d"), vn=f["vn"], r=r, r_wk=r_wk, sig=sig,
            gamma=gamma, catalyst_adj=adj,
            fut_pct=f["fut_pct"], above_sma20=f["above_sma20"],
            above_sma50=f["above_sma50"], ma_rising=f["ma_rising"],
            mom5_pct=f["mom5_pct"],
            on_sig=on_sig, on_pts=lvl * on_sig / 100,
            wk_sig=wk_sig, wk_pts=lvl * wk_sig / 100,
            on=split_stats(lvl, on_sig, r, ON_THR, signal=sig),
            wk=split_stats(lvl, wk_sig, r_wk, WK_THR, signal=sig * DRIFT_TENOR),
            p_big=2 * (1 - Phi(BIGMOVE_THR / wk_sig)),
        )
        ix["on_lo"] = fmt(lvl * (1 - on_sig / 100), lvl)
        ix["on_hi"] = fmt(lvl * (1 + on_sig / 100), lvl)
        ix["wk_lo"] = fmt(lvl * (1 - wk_sig / 100), lvl)
        ix["wk_hi"] = fmt(lvl * (1 + wk_sig / 100), lvl)
        ix["on_dial"] = _dial(on_sig, _ON_CUTS)
        ix["wk_dial"] = _dial(wk_sig, _WK_CUTS)
        ix["disp"] = fmt(lvl, lvl)
        out[key] = ix
    return out


def leans(IX: dict) -> dict:
    on = {k: round(IX[k]["on"]["lean_dn"] * 100) for k in IX}
    return dict(on=on, lo=min(on.values()), hi=max(on.values()))


def data_packet(IX: dict, ctx: dict) -> str:
    """Compact JSON the model sees: the computed math it must write around."""
    ln = leans(IX) if IX else {"on": {}, "lo": 0, "hi": 0}
    missing = [k for k in BOARD_ORDER if k not in IX]
    pkt = {
        "run": {"label": ctx["label"], "date": ctx["long_date"], "time": ctx["time_str"],
                "gap": ctx["phrase"], "next_open": ctx["next_day"],
                "weekend_bump": ctx["weekend"]},
        "note": ("gamma and dials are COMPUTED from live data — do not supply them. "
                 "prelim leans assume catalyst_adj=0; your nudges shift them, so write "
                 "{LEAN_*} tokens in prose, never literal lean numbers"),
        "missing_indices": missing,
        "missing_note": ("supply lvl_est/day_est/vol_est (searched live prints) for these "
                         "indices — the engine cannot run without them" if missing else ""),
        "indices": {},
    }
    for k, ix in IX.items():
        pkt["indices"][k] = {
            "nm": ix["nm"], "level": ix["lvl"], "level_est": ix["est"],
            "day_pct": ix["day"], "vol_30d_iv": ix["vol"], "vol_src": ix["vol_src"],
            "vol_1d_iv": ix["vol1d"],
            "gamma_regime_computed": ix["gamma"],
            "overnight_1sd_pct": round(ix["on_sig"], 2),
            "overnight_1sd_pts": round(ix["on_pts"], 1),
            "week_1sd_pct": round(ix["wk_sig"], 2),
            "prelim_lean_down_pct": ln["on"][k],
            "p_big_week_pct": round(ix["p_big"] * 100),
            "on_dial": ix["on_dial"], "wk_dial": ix["wk_dial"],
            "trend": {"above_sma20": ix["above_sma20"], "above_sma50": ix["above_sma50"],
                      "ma_rising": ix["ma_rising"], "mom5_pct": ix["mom5_pct"],
                      "fut_pct": ix["fut_pct"]},
            "one_sd_range_overnight": f'{ix["on_lo"]} - {ix["on_hi"]}',
            "one_sd_range_week": f'{ix["wk_lo"]} - {ix["wk_hi"]}',
        }
    return json.dumps(pkt, indent=1)


# ── derived display helpers (v12 invariant: numbers in prose are computed) ──
def _day_plain(ix):
    d = ix["day"]
    return f'{"&minus;" if d < 0 else "+"}{abs(d):.2f}%'


def _day_span(ix, parens=True):
    d = ix["day"]
    cls = "neg" if d < 0 else "pos"
    s = f'<span class="{cls}">{"&minus;" if d < 0 else "+"}{abs(d):.2f}%</span>'
    return f"({s})" if parens else s


def _vol_disp(ix):
    est = " (est.)" if (ix.get("vol_src") or "").startswith("est") else ""
    v1 = f' &middot; 1-day {ix["vol1d"]:.1f}' if ix.get("vol1d") else ""
    return f'Vol: {ix["vn"]} <b>{ix["vol"]:.2f}</b>{est}{v1}'


def _derived(IX: dict, content: dict) -> dict:
    """Everything computed that the narrative composition needs."""
    ln = leans(IX)
    by_day = sorted(IX, key=lambda k: IX[k]["day"])
    worst, best = by_day[0], by_day[-1]
    all_red = all(IX[k]["day"] < 0 for k in IX)
    all_green = all(IX[k]["day"] > 0 for k in IX)
    breadth = "broad" if (all_red or all_green) else "mixed"
    dirword = "red" if all_red else ("green" if all_green else "mixed")
    close_line = " / ".join(f'{IX[k]["nm"]} {_day_plain(IX[k])}' for k in BE_ORDER)
    gradient = " &gt; ".join(f'{IX[k]["nm"]} {_day_plain(IX[k])}'
                            for k in sorted(IX, key=lambda x: -IX[x]["day"]))
    vixv = f'{IX["spx"]["vol"]:.2f}'
    by_on = sorted(IX, key=lambda k: -IX[k]["on_sig"])
    widest, calmest = by_on[0], by_on[-1]
    above20 = sum(1 for k in IX if IX[k].get("above_sma20"))
    above50 = sum(1 for k in IX if IX[k].get("above_sma50"))
    rising = sum(1 for k in IX if IX[k].get("ma_rising"))
    if above20 == 4 and above50 == 4:
        trend_note = ('all four sit above their 20- and 50-day averages'
                      + (' and those averages are rising' if rising >= 3 else '')
                      + f', so dips with implied vol at {vixv} have tended to get bought.')
    elif above20 == 0 and above50 == 0:
        trend_note = ('all four are below their 20- and 50-day averages &mdash; rallies into '
                      'those levels have tended to be sold. Treat bounces with caution.')
    else:
        trend_note = (f'the picture is split: {above20} of 4 sit above their 20-day average and '
                      f'{above50} of 4 above their 50-day. Mixed trend &mdash; weaker evidence for '
                      'either buying dips or selling rallies.')
    story = content.get("story") or {}
    cat = (story.get("catalyst") or "").strip()
    return dict(
        ln=ln, worst=worst, best=best, breadth=breadth, dirword=dirword,
        close_line=close_line, gradient=gradient, vixv=vixv,
        widest=widest, calmest=calmest, trend_note=trend_note,
        cat=cat,
        cat_row=(f'<b>{cat}</b>' if cat else 'No single identified driver'),
    )


# ── rendering ───────────────────────────────────────────────────────────────
def _gauge(dial, lbl):
    dcol = DIALCOLOR[dial]
    nx, ny = NEEDLE[dial]
    return f'''<div class="gaugebox">
          <svg viewBox="0 0 220 122" width="160" height="89" xmlns="http://www.w3.org/2000/svg">
            <path d="M 20 110 A 90 90 0 0 1 110 20" stroke="#22c55e" stroke-width="15" fill="none" stroke-linecap="round"/>
            <path d="M 110 20 A 90 90 0 0 1 200 110" stroke="#ef4444" stroke-width="15" fill="none" stroke-linecap="round"/>
            <path d="M 75 32 A 90 90 0 0 1 145 32" stroke="#f59e0b" stroke-width="15" fill="none" stroke-linecap="round"/>
            <line x1="110" y1="110" x2="{nx}" y2="{ny}" stroke="#e8edf3" stroke-width="3" stroke-linecap="round"/>
            <circle cx="110" cy="110" r="7" fill="#e8edf3"/>
            <text x="20" y="120" font-size="11" fill="#6b7787">Calm</text><text x="170" y="120" font-size="11" fill="#6b7787">Risky</text>
          </svg>
          <div class="gauge-value" style="color:{dcol}">{dial.upper()}</div>
          <div class="gauge-label">{lbl}</div>
        </div>'''


def _odds_table(h4, lean_dn, stats, note_html):
    rows = ""
    for rr in stats["rows"]:
        cls = "dgrid qrow" if rr.get("first") else "dgrid"
        rows += f'''
          <div class="{cls}">
            <span class="dlab">{rr['label']}</span><span class="dprice dn">{rr['price_dn']}</span><span class="dp dn">{rr['pdn_pct']}%</span>
            <span class="dtrack"><span class="dhalf l"><span class="df dfdn" style="width:{rr['wdn']}%"></span></span><span class="dmid"></span><span class="dhalf r"><span class="df dfup" style="width:{rr['wup']}%"></span></span></span>
            <span class="dp up">{rr['pup_pct']}%</span><span class="dprice up">{rr['price_up']}</span>
          </div>'''
    return f'''<div class="block">
          <h4>{h4}</h4>
          <div class="leanrow">Lean (direction: futures/trend + skew): <b class="dn">~{round(lean_dn*100)}% down</b> &nbsp;/&nbsp; <b class="up">~{100-round(lean_dn*100)}% up</b></div>
          <div class="dgrid dghead">
            <span>band</span><span class="dn" style="text-align:left">worst lvl</span><span class="dn" style="text-align:left">odds</span>
            <span class="ctr">&#9664; down &nbsp;|&nbsp; up &#9654;</span>
            <span class="up" style="text-align:right">odds</span><span class="up" style="text-align:right">worst lvl</span>
          </div>{rows}
          <div class="note">{note_html}</div>
        </div>'''


def _be_calc(IX, ctx):
    """Breakeven calculator v2: rest-of-day horizon (local clock, 1-day IV,
    U-shaped intraday variance), touch odds (first-passage), spot re-anchor,
    IV what-ifs, staleness warnings. 100% client-side."""
    params = {k: {"nm": IX[k]["nm"], "co": IX[k]["co"], "C": round(IX[k]["lvl"], 2),
                  "vol": round(IX[k]["vol"], 3), "r": round(IX[k]["r"], 4),
                  "sg": round(IX[k].get("sig", 0.0), 4), "vn": IX[k]["vn"],
                  "v1": (round(IX[k]["vol1d"], 3) if IX[k].get("vol1d") else None),
                  "v1n": IX[k]["vn"] + "1D",
                  "on": {"sd": round(IX[k]["on"]["sd"], 4), "su": round(IX[k]["on"]["su"], 4),
                         "mu": round(IX[k]["on"]["mu"], 4), "sd1": round(IX[k]["on_sig"], 3)},
                  "wk": {"sd": round(IX[k]["wk"]["sd"], 4), "su": round(IX[k]["wk"]["su"], 4),
                         "mu": round(IX[k]["wk"]["mu"], 4), "sd1": round(IX[k]["wk_sig"], 3)}}
              for k in IX}
    cfg = {"dk": DRIFT_K, "rdskew": RD_SKEW_TENOR, "rddrift": RD_DRIFT, "uk": UVOL_K,
           "vbeta": VOL_BETA, "gen": ctx["gen_iso"], "genlbl": ctx["time_str"],
           "gday": ctx["gen_date"]}
    blob = json.dumps({"ix": params, "cfg": cfg})
    vn30 = ", ".join(dict.fromkeys(IX[k]["vn"] for k in BE_ORDER))
    vn1d = ", ".join(dict.fromkeys(IX[k]["vn"] + "1D" for k in BE_ORDER))
    opts = "".join(f'<option value="{k}">{IX[k]["nm"]} &mdash; {IX[k]["co"]}</option>' for k in BE_ORDER)
    section = f'''
  <section id="becalc">
    <h2 class="sec-h"><span class="num" style="background:var(--accent);color:#08121e">&#x1F3AF;</span> Breakeven Calculator</h2>
    <p style="color:var(--muted);font-size:13.5px;margin:0 0 10px">Enter <b>any two price levels</b> &mdash; your expiration breakevens, T+0 breakevens, or the support/resistance you&rsquo;d adjust at &mdash; and this returns the odds the index stays between them.</p>

    <div class="panel becalc">
      <div class="berow">
        <label>Index<select id="beIx">{opts}</select></label>
        <label>Horizon<span class="beseg"><button type="button" class="beh on" data-h="rd">Rest of day</button><button type="button" class="beh" data-h="on">Overnight</button><button type="button" class="beh" data-h="wk">1-Week</button></span></label>
        <label class="behrs">Hours (override)<input id="beHrs" type="number" step="any" min="0.25" max="6.5" inputmode="decimal" placeholder="auto"></label>
        <span class="bemeta" id="beMeta"></span>
      </div>
      <div class="berow">
        <label>Current price<input id="beSpot" type="number" step="any" inputmode="decimal"></label>
        <label>Lower level<input id="beLo" type="number" step="any" inputmode="decimal" placeholder="e.g. 7,396"></label>
        <label>Upper level<input id="beHi" type="number" step="any" inputmode="decimal" placeholder="e.g. 7,605"></label>
        <label><span id="beIvLab">IV pt chg</span><input id="beIv" type="number" step="any" inputmode="decimal" value="0"></label>
      </div>
      <div class="bebase" id="beBase"></div>
      <div class="bezrow" id="beZ"></div>
      <div class="beout">
        <div class="becard bein"><div class="bev" id="beSafe">&mdash;</div><div class="bel">Never touches either</div></div>
        <div class="becard bedn"><div class="bev" id="beTLo">&mdash;</div><div class="bel">Touches lower</div></div>
        <div class="becard beup"><div class="bev" id="beTHi">&mdash;</div><div class="bel">Touches upper</div></div>
        <div class="becard belo"><div class="bev" id="beTouch">&mdash;</div><div class="bel">Touches either</div></div>
      </div>
      <div class="beterm" id="beTerm"></div>
      <div class="note" id="beHint">Enter both levels to see the odds. &ldquo;Rest of day&rdquo; runs from now to today&rsquo;s 4:00 PM ET close and shrinks on its own as the session runs.</div>
      <div class="bewarn" id="beStale"></div>
    </div>
    <div class="bewhat">
      <div class="bewhat-h">How it works</div>
      <p>It prices the <b>implied move</b> from the index&rsquo;s own option-market volatility, tilts it for <b>put skew</b> (downside tails are fatter than upside) and for the <b>directional lean</b> in tonight&rsquo;s read, then measures where your two levels fall on that distribution. The horizon sets the vol it uses: <b>Rest of day</b> prices off the 1-day number ({vn1d}) and shrinks as the session runs down; <b>Overnight</b> and <b>1-Week</b> use the 30-day number ({vn30}).</p>
      <p><b>Touch odds are the headline.</b> &ldquo;Never touches either&rdquo; asks whether price stays inside your range the whole way &mdash; not merely where it finishes. That matters because a level that gets tagged intraday has already forced your decision, even if price closes back inside. Closing odds flatter a range; touch odds tell you what you&rsquo;ll actually live through.</p>
      <p><b>The IV point-change box</b> is a what-if. Type &minus;2 to ask &ldquo;how does this range look if vol drops two points?&rdquo; It moves whichever vol the selected horizon uses, and the label changes to match.</p>
      <p class="bewarn"><b>&#9888; These are estimates, and they age.</b> Volatility, skew and the directional lean are <b>frozen at the {ctx["time_str"]} run</b> that produced this page &mdash; only your inputs and the clock keep updating. Run this during the session that follows and it&rsquo;s working from a live picture. Run it a day later, or after a gap or a volatility spike, and the inputs behind it are stale even though the numbers still move. <b>Check back for the next report for anything current.</b> Options-implied probabilities are a description of what the market is pricing, not a forecast &mdash; and nothing here accounts for your position size, spreads or fills.</p>
      <p class="bequiet">Runs entirely in your browser. Nothing is sent anywhere, and it makes no network calls.</p>
    </div>
  </section>'''
    script = '''
<script>
(function(){
  var BLOB=__BE_JSON__, BE=BLOB.ix, CFG=BLOB.cfg;
  function erf(x){var s=x<0?-1:1;x=Math.abs(x);var a1=0.254829592,a2=-0.284496736,a3=1.421413741,a4=-1.453152027,a5=1.061405429,p=0.3275911;var t=1/(1+p*x);var y=1-(((((a5*t+a4)*t+a3)*t+a2)*t+a1)*t*Math.exp(-x*x));return s*y;}
  function Phi(z){return 0.5*(1+erf(z/Math.SQRT2));}
  function F(pp,L){var wd=pp.sd/(pp.sd+pp.su),wu=pp.su/(pp.sd+pp.su);if(L<=pp.mu){return wd*(2*Phi((L-pp.mu)/pp.sd));}return wd+wu*(2*Phi((L-pp.mu)/pp.su)-1);}
  function touchDn(a,mu,s){if(a>=0)return 1;return Math.min(1,Phi((a-mu)/s)+Math.exp(2*mu*a/(s*s))*Phi((a+mu)/s));}
  function touchUp(b,mu,s){if(b<=0)return 1;return Math.min(1,(1-Phi((b-mu)/s))+Math.exp(2*mu*b/(s*s))*(1-Phi((b+mu)/s)));}
  function etNow(){
    try{var p=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour12:false,hour:'2-digit',minute:'2-digit',weekday:'short'}).formatToParts(new Date());
      var o={};p.forEach(function(x){o[x.type]=x.value;});return {h:parseInt(o.hour,10)%24,m:parseInt(o.minute,10),wd:o.weekday};
    }catch(e){var d=new Date();return {h:d.getHours(),m:d.getMinutes(),wd:''};}
  }
  function W(t,k){return t + k*(Math.pow(2*t-1,3)+1)/6;}
  function varRemain(){
    var n=etNow(), mins=n.h*60+n.m, o=9*60+30, c=16*60, k=CFG.uk;
    if(mins<=o) return {f:1,hrs:6.5,pre:true};
    if(mins>=c) return {f:0,hrs:0,pre:false,post:true};
    var t=(mins-o)/(c-o), tot=W(1,k);
    return {f:(tot-W(t,k))/tot, hrs:(c-mins)/60, pre:false};
  }
  function volFor(d,h){return (h==='rd'&&d.v1)?{v:d.v1,nm:d.v1n,short:true}:{v:d.vol,nm:d.vn,short:false};}
  function params(d,h,ivchg,rem){
    var base=volFor(d,h), volE=Math.max(1,base.v+(ivchg||0)), sc=volE/base.v;
    if(h==='rd'){
      var S=volE/100*Math.sqrt(1/252)*Math.sqrt(Math.max(rem.f,1e-6))*100;
      var r=1+(d.r-1)*CFG.rdskew, su=2*S/(1+r), sd=r*su;
      return {sd:sd,su:su,mu:CFG.dk*(d.sg*CFG.rddrift)*S,sd1:S,vsrc:base};
    }
    var p=d[h];
    return {sd:p.sd*sc,su:p.su*sc,mu:p.mu*sc,sd1:p.sd1*sc,vsrc:base};
  }
  var $=function(id){return document.getElementById(id);};
  var ixSel=$('beIx'),spotI=$('beSpot'),loI=$('beLo'),hiI=$('beHi'),ivI=$('beIv'),hrsI=$('beHrs'),
      elSafe=$('beSafe'),elTLo=$('beTLo'),elTHi=$('beTHi'),elTouch=$('beTouch'),
      elZ=$('beZ'),elTerm=$('beTerm'),elBase=$('beBase'),meta=$('beMeta'),hint=$('beHint'),stale=$('beStale');
  var horizon='rd', spotTouched=false;
  function pct(v){return (Math.round(v*1000)/10).toFixed(1)+'%';}
  function fnum(n){return Math.abs(n)>=1000?Math.round(n).toLocaleString():n.toFixed(1);}
  function HNAME(h){return h==='rd'?'rest of day':(h==='on'?'overnight':'1-week');}
  function dash(){[elSafe,elTLo,elTHi,elTouch].forEach(function(e){e.textContent='\\u2014';});elZ.innerHTML='';elTerm.innerHTML='';}
  spotI.addEventListener('input',function(){spotTouched=true;});
  function syncSpot(){ var d=BE[ixSel.value];
    if(!spotTouched) spotI.value=d.C;
    $('beIvLab').textContent=d.vn+' pt chg';
  }
  function calc(){
    var d=BE[ixSel.value], ivchg=parseFloat(ivI.value)||0, rem=varRemain();
    var hOv=parseFloat(hrsI.value);
    if(horizon==='rd'&&!isNaN(hOv)&&hOv>0){ rem={f:Math.min(hOv/6.5,1), hrs:hOv, manual:true}; }
    var p=params(d,horizon,ivchg,rem), C=parseFloat(spotI.value);
    if(isNaN(C)||C<=0) C=d.C;
    var mtxt=HNAME(horizon)+' 1SD &plusmn;'+p.sd1.toFixed(2)+'%';
    if(horizon==='rd') mtxt+=' &middot; '+(rem.manual?rem.hrs.toFixed(1)+'h (manual)':(rem.f<=0?'market closed':rem.hrs.toFixed(1)+'h to the bell'));
    meta.innerHTML=mtxt;
    $('beIvLab').textContent=p.vsrc.nm+' pt chg';
    var dPct=(C-d.C)/d.C*100, vBase=p.vsrc.v, vNow=vBase+ivchg;
    function sgn(x,dp){return (x>0?'+':'')+x.toFixed(dp);}
    var pxCls=Math.abs(dPct)<0.05?'bbflat':(dPct<0?'bbdn':'bbup');
    var ivCls=Math.abs(ivchg)<0.005?'bbflat':(ivchg>0?'bbdn':'bbup');
    elBase.innerHTML=
      '<span class="bbgrp"><em>At the '+CFG.genlbl+' run</em>'
        +'<b>'+d.nm+' '+fnum(d.C)+'</b>'
        +'<b>'+d.vn+' '+d.vol.toFixed(2)+'</b>'
        +(d.v1?'<b>'+d.v1n+' '+d.v1.toFixed(2)+'</b>':'<b class="bbnull">'+d.v1n+' not set</b>')
      +'</span>'
      +'<span class="bbarrow">&rarr;</span>'
      +'<span class="bbgrp"><em>Your assumption now</em>'
        +'<b>'+d.nm+' '+fnum(C)+' <i class="'+pxCls+'">'+sgn(dPct,2)+'%</i></b>'
        +'<b>'+p.vsrc.nm+' '+vNow.toFixed(2)+' <i class="'+ivCls+'">'+sgn(ivchg,2)+'</i></b>'
      +'</span>';
    var vwarn=(horizon==='rd'&&!p.vsrc.short)
      ? ' Rest-of-day is using the 30-day '+d.vn+' because no '+d.v1n+' was available at generation &mdash; on a steep term structure that misprices a few-hour horizon.' : '';
    var drift=(C-d.C)/d.C*100, lvl=0;
    var sm='Vol, skew and drift are frozen at the '+CFG.genlbl+' run &mdash; only your inputs and the clock update.';
    var today=(function(){try{return new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());}catch(e){return '';}})();
    if(today&&CFG.gday&&today>CFG.gday){
      lvl=2; sm='<b>This report was built '+CFG.gday+' &mdash; it is not today\\u2019s.</b> Every baked number (vol, skew, drift, levels) is from that run. '
        +'Open today\\u2019s report before trusting these odds.';
    } else if(Math.abs(drift)>=0.35){
      lvl=1;
      var sug=-CFG.vbeta*drift;
      sm+=' Your reference is <b>'+(drift>0?'+':'')+drift.toFixed(2)+'%</b> off the baked '+fnum(d.C)+'.';
      if(Math.abs(sug)>=0.3&&!ivchg) sm+=' A move that size usually shifts '+d.vn+' about <b>'+(sug>0?'+':'')+sug.toFixed(1)
        +'</b> point'+(Math.abs(sug)>=1.5?'s':'')+' &mdash; consider putting that in IV pt chg, or vol here is stale in the flattering direction.';
    }
    stale.className='bewarn'+(lvl===2?' lvl2':(lvl===1?' lvl1':''));
    stale.innerHTML=sm+vwarn;
    if(horizon==='rd'&&rem.f<=0&&!rem.manual){dash();hint.innerHTML='<b>Market is closed.</b> Switch to Overnight, or type an hours override.';return;}
    var lo=parseFloat(loI.value),hi=parseFloat(hiI.value);
    if(isNaN(lo)||isNaN(hi)){dash();hint.innerHTML='Enter both levels to see the odds.';return;}
    if(lo>=hi){dash();hint.innerHTML='<b style="color:#f87171">Lower level must be below the upper.</b>';return;}
    var a=(lo-C)/C*100, b=(hi-C)/C*100;
    var tLo=(a>=0)?1:touchDn(a,p.mu,p.sd), tHi=(b<=0)?1:touchUp(b,p.mu,p.su);
    var tAny=Math.min(1,tLo+tHi);
    var below=F(p,a), above=1-F(p,b), inside=Math.max(0,1-below-above);
    elSafe.textContent=pct(1-tAny);elTLo.textContent=pct(tLo);elTHi.textContent=pct(tHi);elTouch.textContent=pct(tAny);
    var zL=(a-p.mu)/p.sd, zH=(b-p.mu)/p.su;
    function zcls(z){var m=Math.abs(z);return m<1.25?'zhot':(m<2.25?'zmid':'zcold');}
    elZ.innerHTML='<span class="zchip '+zcls(zL)+'"><b>'+fnum(lo)+'</b> = '+zL.toFixed(2)+'&sigma; <i>('+a.toFixed(2)+'%)</i></span>'
                 +'<span class="zchip '+zcls(zH)+'"><b>'+fnum(hi)+'</b> = +'+zH.toFixed(2)+'&sigma; <i>(+'+b.toFixed(2)+'%)</i></span>'
                 +'<span class="zlab">distance from '+fnum(C)+' in 1SD units &mdash; the closer side is the one in play</span>';
    elTerm.innerHTML='<b>Where it ends up</b> (ignoring the path): stays between <b>'+pct(inside)+'</b> &middot; ends below '+fnum(lo)+' '+pct(below)+' &middot; ends above '+fnum(hi)+' '+pct(above)+'. Always kinder than the touch odds above &mdash; use it only if you would hold to the horizon rather than adjust on a tag.';
    hint.innerHTML='<b>'+d.nm+' '+HNAME(horizon)+'</b>: about a <b>'+pct(tAny)+'</b> chance price tags '+fnum(lo)+' or '+fnum(hi)+' before the horizon '
      +(tLo>tHi*3?'&mdash; almost entirely the <b>downside</b>':(tHi>tLo*3?'&mdash; almost entirely the <b>upside</b>':'&mdash; risk is two-sided'))
      +'. Rough guide, not a guarantee.';
  }
  function pick(h){horizon=h;Array.prototype.forEach.call(document.querySelectorAll('.beh'),function(x){x.classList.toggle('on',x.getAttribute('data-h')===h);});
    document.querySelector('.behrs').style.visibility=(h==='rd')?'visible':'hidden';calc();}
  ixSel.addEventListener('change',function(){syncSpot();calc();});
  [spotI,loI,hiI,ivI,hrsI].forEach(function(e){e.addEventListener('input',calc);});
  Array.prototype.forEach.call(document.querySelectorAll('.beh'),function(b){b.addEventListener('click',function(){pick(b.getAttribute('data-h'));});});
  syncSpot();pick('rd');
  setInterval(function(){if(horizon==='rd'&&isNaN(parseFloat(hrsI.value)))calc();},60000);
})();
</script>'''.replace("__BE_JSON__", blob)
    return section, script


def _daycell(d):
    if d > 0:
        return f'<td class="num pos">+{d:.1f}%</td>'
    if d < 0:
        return f'<td class="num neg">&minus;{abs(d):.1f}%</td>'
    return '<td class="num flat">+0.0%</td>'


def _cushion(ix, levels, lean_pct):
    """Cushion copy follows the COMPUTED gamma regime (v12 invariant)."""
    nm, g = ix["nm"], ix.get("gamma", "thin")
    if g == "thin":
        return ("thin", '&#x1F6E1;&#xFE0F; A note on the cushion (gamma)',
                f'Reliable dealer-positioning (gamma) data is <b>thin for {nm}</b> right now, so '
                'there&rsquo;s no clean &ldquo;cushion line&rdquo; here &mdash; this read leans on '
                'the implied band and the broad tape rather than a positioning level.')
    line = (levels.get("sup") or ["&mdash;"])[0]
    if g == "pos":
        return ("", '&#x1F6E1;&#xFE0F; What &ldquo;the cushion&rdquo; means (gamma, in plain English)',
                f'On a calm day big options dealers <b>buy dips and sell rips</b> &mdash; a shock '
                f'absorber that fades moves (a <b class="pos">positive</b> cushion). {nm} is near its '
                f'<b>~{line} cushion line</b>. Hold above it and dip-buying keeps pullbacks shallow; '
                f'lose it overnight and the shock absorber weakens. Tonight&rsquo;s lean sits at '
                f'~{lean_pct}% down.')
    return ("", '&#x1F6E1;&#xFE0F; What &ldquo;the cushion&rdquo; means (gamma, in plain English)',
            f'Dealer positioning in {nm} currently reads <b class="neg">negative</b> &mdash; instead of '
            f'absorbing moves, dealers amplify them, so pushes tend to extend rather than fade. '
            f'<b>~{line}</b> is the level to watch; losing it overnight would deepen the move. '
            f'Tonight&rsquo;s lean sits at ~{lean_pct}% down.')


def _card(ix, story_ix, levels, ln, ctx, on_note, wk_note_html):
    res = (levels.get("res") or ["&mdash;", "&mdash;"]) + ["&mdash;"] * 2
    sup = (levels.get("sup") or ["&mdash;", "&mdash;"]) + ["&mdash;"] * 2
    cclass, chead, ctext = _cushion(ix, levels, ln["on"][ix["key"]])
    special = ""
    if ix.get("gamma", "thin") != "thin":
        special = (f'<div class="lvrow"><span class="lab">Cushion line</span>'
                   f'<span class="chip f">~{sup[0]}</span></div>')
    est_tag = ", est." if ix["est"] else ""
    pts = f'{ix["on_pts"]:,.1f}' if ix["lvl"] < 1000 else f'{ix["on_pts"]:,.0f}'
    dsub = (f'Live <b>{ix["disp"]}</b> {_day_span(ix)}{est_tag} &nbsp;&middot;&nbsp; '
            f'overnight 1SD <b>&plusmn;{ix["on_sig"]:.2f}%</b> (&plusmn;{pts} pts) &nbsp;&middot;&nbsp; '
            f'1-week 1SD <b>&plusmn;{ix["wk_sig"]:.2f}%</b> &nbsp;&middot;&nbsp; '
            f'{story_ix.get("tail", "")}')
    on_block = _odds_table(
        f'{ctx["gap_word"]} gap &mdash; odds {ctx["next_day"]} opens DOWN vs UP (from {ix["disp"]})',
        ix["on"]["lean_dn"], ix["on"], on_note.replace("{vol}", _vol_disp(ix)))
    wk_block = _odds_table(
        f'1-Week move &mdash; odds the index closes DOWN vs UP over the next ~5 sessions (from {ix["disp"]})',
        ix["wk"]["lean_dn"], ix["wk"], wk_note_html)
    return f'''
  <section id="{ix['key']}">
    <div class="cardnav"><a href="#board">&uarr; Gap Board</a></div>
    <div class="drill" style="border-top-color:{ix['bar']}">
      <div class="dhead" style="margin-bottom:2px">
        <div>
          <div class="dtitle">{ix['nm']} <small>{ix['co']}</small></div>
          <div class="dsub">{dsub}</div>
        </div>
      </div>
      <div class="cardgrid">
        <div class="cmain">
          {on_block}
          <div class="cushion {cclass}">
            <span class="h">{chead}</span>
            {ctext}
          </div>
          <div class="weekhead">&#x1F4C6; 1-Week Outlook <small>&mdash; next ~5 trading sessions; ranked in the Big Move section below</small></div>
          {wk_block}
        </div>
        <div class="crail">
          <div class="railbox">{_gauge(ix["on_dial"], "Overnight gap risk")}</div>
          <div class="block">
            <h4>Key whole-number levels</h4>
            <div class="lvls">
              <div class="lvrow"><span class="lab">Resistance</span><span class="chip r">{res[0]}</span><span class="chip r">{res[1]}</span></div>
              <div class="lvrow"><span class="lab">Live</span><span class="chip ">{ix['disp']}</span></div>
              <div class="lvrow"><span class="lab">Overnight 1SD</span><span class="chip f">{ix['on_lo']} &ndash; {ix['on_hi']}</span></div>
              <div class="lvrow"><span class="lab">1-week 1SD</span><span class="chip f">{ix['wk_lo']} &ndash; {ix['wk_hi']}</span></div>
              <div class="lvrow"><span class="lab">Support</span><span class="chip s">{sup[0]}</span><span class="chip s">{sup[1]}</span></div>
              {special}
            </div>
            <div class="note" style="margin-top:8px">Round numbers act as magnets &mdash; option open-interest clusters there. Re-verify live.</div>
          </div>
          <div class="railbox">
            <div class="rsum">1-week move <b>&plusmn;{ix['wk_sig']:.2f}%</b> (&plusmn;{ix['wk_pts']:,.0f} pts)<br>chance of a &gt;3% week: <b>{round(ix['p_big']*100)}%</b><br>range {ix['wk_lo']} &ndash; {ix['wk_hi']}</div>
            {_gauge(ix["wk_dial"], "1-week move risk")}
          </div>
        </div>
      </div>
      <div class="metarow">
        <div>{story_ix.get("driver", "")}</div>
        <div>{story_ix.get("gapfill", "")}</div>
      </div>
    </div>
  </section>'''


def render(IX: dict, content: dict, ctx: dict, style: str,
           tc_logo: str = "/assets/tradeclub-ai.png",
           mw_logo: str = "/assets/mw.png") -> str:
    """Full standalone HTML page: computed numbers + STORY-shaped judgment."""
    D = _derived(IX, content)
    ln = D["ln"]
    story = content.get("story") or {}
    levels_all = content.get("levels") or {}

    def toks(s):
        if not isinstance(s, str):
            return s
        for k in IX:
            s = s.replace("{LEAN_" + k.upper() + "}", str(ln["on"][k]))
        return s.replace("{LEAN_LO}", str(ln["lo"])).replace("{LEAN_HI}", str(ln["hi"]))

    bump_note = " the band is bumped ~25% for the extra closed-market days;" if ctx["weekend"] > 1 else ""
    on_note = (f'This is a <b>{ctx["label"].lower()}</b> into a <b>{ctx["phrase"]}</b> '
               f'(this session&rsquo;s close &rarr; {ctx["next_day"]} open) &mdash;{bump_note} '
               'the lean blends the directional read (overnight futures + short-term trend + gamma regime) '
               'with the options put-skew that shapes the tails. Each row is a <b>band</b> (a slice of where '
               f'{ctx["next_day"]}&rsquo;s open could land) and the <b>odds it lands in that slice</b>; '
               'the <b>worst lvl</b> is the far edge of the slice. Because the bands don&rsquo;t overlap, '
               'the <b>odds add up</b> &mdash; all down bands sum to the down lean, all up bands to the up '
               'lean, everything to 100%. For a level between the marks, use the <b>Breakeven Calculator</b> '
               'up top. {vol}')
    wk_note_html = ('This is the <b>1-week outlook</b> &mdash; the implied move over the <b>next ~5 trading '
                    'sessions</b> from each index&rsquo;s own option-implied vol (no weekend bump; '
                    'full-session variance). Each row is a <b>band</b> and the <b>odds it lands in that '
                    'slice</b>; the <b>worst lvl</b> is the far edge. The bands don&rsquo;t overlap, so the '
                    '<b>odds add up</b>. Probabilities are options-implied estimates, not predictions '
                    '&mdash; verify the live catalysts before acting.')

    board = "\n".join(
        f'''      <tr>
        <td class="inst"><a href="#{k}"><b>{IX[k]['nm']}</b> <small>({IX[k]['etf']})</small></a></td>
        <td class="num">{IX[k]['disp']}{' <small style="color:var(--faint)">est</small>' if IX[k]['est'] else ''}</td>
        {_daycell(IX[k]['day'])}
        <td class="num">&plusmn;{IX[k]['on_sig']:.2f}% <small style="color:var(--faint)">{fmt(IX[k]['on_pts'], IX[k]['lvl'])}p</small></td>
        <td class="num"><b class="dn">{round(IX[k]['on']['lean_dn']*100)}%</b> <small style="color:var(--faint)">down</small></td>
        <td><span class="dialpill {DIALPILL[IX[k]['on_dial']]}">{IX[k]['on_dial']}</span></td>
        <td class="lvls">S {((levels_all.get(k) or {}).get('sup') or ['&mdash;','&mdash;'])[1]} / {((levels_all.get(k) or {}).get('sup') or ['&mdash;','&mdash;'])[0]} &middot; R {((levels_all.get(k) or {}).get('res') or ['&mdash;','&mdash;'])[0]} / {((levels_all.get(k) or {}).get('res') or ['&mdash;','&mdash;'])[1]}</td>
      </tr>''' for k in BOARD_ORDER)

    ranked = sorted(IX.values(), key=lambda a: -a["p_big"])
    bigboard = "\n".join(
        f'''      <tr>
        <td class="num" style="color:var(--faint)">#{i+1}</td>
        <td class="inst"><a href="#{ix['key']}"><b>{ix['nm']}</b> <small>({ix['etf']})</small></a></td>
        <td class="num">&plusmn;{ix['wk_sig']:.2f}% <small style="color:var(--faint)">{ix['wk_pts']:,.0f}p</small></td>
        <td style="white-space:nowrap"><span class="pbar"><i style="width:{min(round(ix['p_big']*100)*2, 100)}%"></i></span><b>{round(ix['p_big']*100)}%</b></td>
        <td class="num"><b class="dn">{round(ix['wk']['lean_dn']*100)}%</b> <small style="color:var(--faint)">down</small></td>
        <td><span class="dialpill {DIALPILL[ix['wk_dial']]}">{ix['wk_dial']}</span></td>
      </tr>''' for i, ix in enumerate(ranked))

    cards = "".join(_card(IX[k], story.get(k) or {}, levels_all.get(k) or {}, ln, ctx,
                          on_note, wk_note_html) for k in BOARD_ORDER)
    be_section, be_script = _be_calc(IX, ctx)

    # ── composed narrative (numbers computed, words from STORY) ─────────────
    wix, bix = IX[D["worst"]], IX[D["best"]]
    widest, calmest = IX[D["widest"]], IX[D["calmest"]]
    heads_b = (f'{story.get("headline", "")} Closes: {D["close_line"]}. The drift+skew lean '
               f'<b>spreads {ln["lo"]}&ndash;{ln["hi"]}% down</b> across the four, tracking each '
               'index&rsquo;s own read rather than a single pinned number.')
    tldr_items = [
        f'<span class="lead">How the four closed.</span> {wix["nm"]} was weakest at {_day_plain(wix)}; '
        f'{bix["nm"]} was strongest at {_day_plain(bix)}. This was a <b>{D["breadth"]}</b> session.',
        f'<span class="lead neg">Highest gap risk: {widest["nm"]}</span> (overnight '
        f'&plusmn;{widest["on_sig"]:.2f}%, 1-week &plusmn;{widest["wk_sig"]:.2f}%). '
        f'<span class="lead pos">Calmest: {calmest["nm"]}</span> (overnight &plusmn;{calmest["on_sig"]:.2f}%).',
        f'<span class="lead">The lean moves with the tape.</span> The drift+skew engine spreads the '
        f'overnight lean from <b>~{ln["lo"]}% down ({IX[min(IX, key=lambda k: ln["on"][k])]["nm"]})</b> to '
        f'<b>~{ln["hi"]}% down ({IX[max(IX, key=lambda k: ln["on"][k])]["nm"]})</b> &mdash; each index&rsquo;s '
        'own read, not one pinned number.',
    ]
    for extra in (content.get("tldr_extra") or [])[:3]:
        tldr_items.append(extra)
    tldr_items.append(f'&#9888; <b>Watch:</b> {story.get("watch", "")} SPX 30-day implied vol is <b>{D["vixv"]}</b>.')
    tldr = "".join(f"      <li>{b}</li>\n" for b in tldr_items)

    breadth_read = (f'this was a <b>{D["breadth"]}</b> session. The <b>gradient</b>, strongest to '
                    f'weakest: {D["gradient"]}. When all four move together it points to a genuine '
                    'risk shift; when they split, it is more often rotation or positioning than a '
                    'change in the overall tape.')
    top2 = ranked[:2]
    bigmove_note = (f'<b>{top2[0]["nm"]} and {top2[1]["nm"]} top the list</b> '
                    f'(~{round(top2[0]["p_big"]*100)}% and ~{round(top2[1]["p_big"]*100)}% chance of a '
                    f'&gt;3% week) on their richer vol; <b>{ranked[-1]["nm"]} is the anchor</b> '
                    f'(~{round(ranked[-1]["p_big"]*100)}%).')

    clock_rows = "".join(f'      <div class="ce"><span class="t">{r.get("t", "")}</span>'
                         f'<span class="w">{r.get("w", "")}</span></div>\n'
                         for r in content.get("clock", []))
    clock_intro = (f'The next open is <b>{ctx["next_day"]}&rsquo;s</b> ({ctx["phrase"]}). '
                   'Here&rsquo;s where the gap gets made:')

    lvl_ndx = ((levels_all.get("ndx") or {}).get("sup") or ["&mdash;"])[0]
    lvl_spx = ((levels_all.get("spx") or {}).get("sup") or ["&mdash;"])[0]
    cal_rows = (
        f'        <tr><td class="dt" style="color:#f87171">Now &middot; live</td><td>{D["cat_row"]}</td>'
        f'<td>{"The identified driver for the current tape." if D["cat"] else "No single driver was identified for this session."}</td></tr>\n'
        f'        <tr class="done"><td class="dt">Latest closes</td><td>Cash session</td>'
        f'<td>{D["close_line"]}. SPX 30-day implied vol {D["vixv"]}.</td></tr>\n'
        f'        <tr><td class="dt">Into {ctx["next_day"]}&rsquo;s open</td><td>Futures + Asia/Europe trade</td>'
        f'<td>First live read on the overnight tone. Watch NDX ~{lvl_ndx} and SPX ~{lvl_spx} at the open.</td></tr>\n')
    for r in (content.get("calendar_extra") or [])[:4]:
        dt_style = ' style="color:#f87171"' if r.get("hot") else ""
        cal_rows += (f'        <tr><td class="dt"{dt_style}>{r.get("when", "")}</td>'
                     f'<td>{r.get("event", "")}</td><td>{r.get("why", "")}</td></tr>\n')

    do_lis = "".join(f"        <li>{x}</li>\n" for x in (content.get("playbook") or {}).get("do", []))
    dont_lis = "".join(f"        <li>{x}</li>\n" for x in (content.get("playbook") or {}).get("dont", []))
    trend_li = f'        <li>Respect the <b>trend context</b> &mdash; {D["trend_note"]}</li>\n'

    banner_title = (f'{story.get("headline", "")} {wix["nm"]} weakest at {_day_plain(wix)}, '
                    f'{bix["nm"]} strongest at {_day_plain(bix)}; the drift+skew lean spreads '
                    f'{ln["lo"]}&ndash;{ln["hi"]}% down across the four.')
    banner_body = (f'Watch NDX ~{lvl_ndx} and SPX ~{lvl_spx}, let overnight futures give the first '
                   'clean read, and lean on the Big Move Ranking for weekly risk.')

    vol_bits = []
    for k in BOARD_ORDER:
        src = IX[k].get("vol_src") or ""
        vol_bits.append(f'{IX[k]["vn"]} ' + ("chain IV" if src.startswith("uw") else
                                             ("quote" if src.startswith("yf") else "est.")))
    footer_note = (f'Index levels and % moves are live index prints ({D["close_line"]}). Implied vols: '
                   + ", ".join(vol_bits) + '. The dealer-gamma regime is computed from live options '
                   'positioning data where available. The per-index directional <b>signal</b> is '
                   'mechanical (trend/momentum/futures), with a small analyst catalyst nudge.')

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Daily AI {ctx["gap_word"]} Gap Risk Report &mdash; {ctx["label"].title()} &mdash; {ctx["long_date"]} &middot; Trade Club AI</title>
{style}
</head>
<body><div class="wrap">

  <div class="header">
    <img class="brand-tc" alt="Trade Club AI" src="{tc_logo}">
    <div class="head-text">
      <div class="eyebrow">Trade Club AI &middot; {ctx["gap_word"]} Gap Risk &middot; {ctx["label"].title()}</div>
      <h1>Daily AI {ctx["gap_word"]} Gap Risk Report</h1>
      <div class="sub">SPX &middot; NDX &middot; DJX &middot; RUT &mdash; gap into the next open + 1-week outlook</div>
      <div class="stamp">{ctx["long_date"]} &middot; {ctx["time_str"]} &nbsp;|&nbsp; <b style="color:var(--accent)">{ctx["label"]}</b> &middot; {ctx["phrase"]}{(" &middot; " + content["risk_phrase"]) if content.get("risk_phrase") else ""}</div>
    </div>
    <img class="brand-mw" alt="Michael Wade Trade Coaching" src="{mw_logo}">
  </div>

  <div class="nav">
    <span class="lab">Jump to</span>
    <a class="board" href="#board">Gap Board</a>
    <a href="#ndx">NDX</a><a href="#rut">RUT</a><a href="#spx">SPX</a><a href="#djx">DJX</a>
    <a href="#becalc">Breakevens</a><a href="#bigmove">Big Move</a><a href="#clock">Clock</a><a href="#calendar">Calendar</a><a href="#playbook">Playbook</a>
  </div>

  <div class="heads">
    <div class="icon">&#x26A0;&#xFE0F;</div>
    <div><p class="t">{story.get("headline", "")}</p>
    <p class="b">{heads_b}</p></div>
  </div>

  <div class="tldr">
    <h2>&#x1F3AF; The 60-Second Read</h2>
    <ul>
{tldr}    </ul>
  </div>

  <section id="board">
    <h2 class="sec-h"><span class="num">1</span> The Gap Board &mdash; Tap An Index To Jump To Its Card</h2>
    <table class="board">
      <tr><th class="inst">Index (ETF)</th><th>Live</th><th>Day %</th><th>Impl. Overnight Move</th><th>Lean</th><th>Overnight Gap Dial</th><th class="lvls" style="text-align:left">Key Whole-# Levels</th></tr>
{board}
    </table>
    <div class="breadth"><b>Breadth read:</b> {breadth_read}</div>
  </section>
{be_section}
{cards}

  <section id="bigmove">
    <h2 class="sec-h"><span class="num">2</span> Big Move Ranking with Probabilities &mdash; 1-Week Horizon</h2>
    <p style="color:var(--muted);font-size:13.5px;margin:0 0 12px">Which index is most likely to make a <b>big move</b> over the <b>next ~5 trading sessions</b>? Ranked by the options-implied probability of a <b>&gt;3% move in either direction</b> this week (each index&rsquo;s own option-implied vol). Each row links to that index&rsquo;s full 1-week odds table above.</p>
    <div class="panel" style="padding:6px 18px">
      <table class="board">
        <tr><th class="num" style="width:36px">Rank</th><th class="inst">Index (ETF)</th><th>1-Week 1SD</th><th>Prob. of a &gt;3% week</th><th>Lean</th><th>1-Week Dial</th></tr>
{bigboard}
      </table>
    </div>
    <div class="breadth" style="margin-top:12px"><b>How to read it:</b> the ranking is about <b>size, not direction</b> &mdash; it says where the widest swings are most likely, not which way. {bigmove_note} It&rsquo;s still a modest tilt, not a forecast. Pair this with the per-index 1-week tables above for the full down/up split and price targets.</div>
  </section>

  <section id="clock">
    <h2 class="sec-h"><span class="num">3</span> The Overnight Clock &mdash; Where {ctx["next_day"]}&rsquo;s Gap Gets Made</h2>
    <p style="color:var(--muted);font-size:13.5px;margin:0 0 12px">{clock_intro}</p>
    <div class="clock">
{clock_rows}    </div>
  </section>

  <section id="calendar">
    <h2 class="sec-h"><span class="num">4</span> Event Calendar &mdash; Next Few Sessions</h2>
    <div class="panel" style="padding:6px 18px">
      <table class="cal">
        <tr><th class="dt">When</th><th>Event</th><th>Why it matters for the gap</th></tr>
{cal_rows}      </table>
    </div>
    <div class="note" style="margin-top:8px">&#9888; <b>Honesty note:</b> the hard-confirmed items above are the closing levels and implied vols, fetched live at generation. {content.get("calendar_note", "Forward rows come from research at run time — confirm every event and figure against a primary source before acting.")}</div>
  </section>

  <section id="playbook">
    <h2 class="sec-h"><span class="num">5</span> Overnight + 1-Week Playbook</h2>
    <div class="dodont">
      <div class="col do"><h3>&#x2705; DO</h3><ul>
{do_lis}{trend_li}      </ul></div>
      <div class="col dont"><h3>&#x274C; DON&rsquo;T</h3><ul>
{dont_lis}      </ul></div>
    </div>
  </section>

  <section>
    <div class="banner">
      <div class="icon">&#x1F4CC;</div>
      <div>
        <p class="title">{banner_title}</p>
        <p class="body">{banner_body}</p>
      </div>
    </div>
  </section>

  <section>
    <h2 class="sec-h">How To Read This Report</h2>
    <div class="legend">
      <dl style="margin:0">
        <dt>Run type</dt><dd><b>Pre-market</b>: overnight futures already trading &mdash; the direction read is sharpest. <b>Mid-session / post-market</b>: the direction read is driven by the <b>drift signal</b> (short-term trend + today&rsquo;s tape + gamma) plus skew; it sharpens as overnight futures trade.</dd>
        <dt>{ctx["gap_word"]} gap</dt><dd>This session&rsquo;s close &rarr; the next session&rsquo;s open{" &mdash; the band is bumped ~25% for the extra closed-market days" if ctx["weekend"] > 1 else " (~1 closed night), so the implied band is the plain overnight 1SD"}.</dd>
        <dt>1-Week implied move</dt><dd>The one-standard-deviation band over the <b>next ~5 trading sessions</b>, from each index&rsquo;s own option-implied vol (full-session variance, no weekend bump). A size, not a direction.</dd>
        <dt>Odds bands</dt><dd>Each row is a <b>slice</b> of where the open could land and the <b>odds it lands in that slice</b>. The <b>worst lvl</b> is the far (outer) edge of the slice; the near edge is the row above it. The slices don&rsquo;t overlap, so the <b>odds add up</b> &mdash; all down bands sum to the down lean, all up bands to the up lean, everything to 100%. For a level <i>between</i> the marks (like your actual breakeven), use the Breakeven Calculator.</dd>
        <dt>Direction Split</dt><dd>The band sliced into a down leg and an up leg by a model with two inputs: a <i>directional drift</i> (overnight futures + short-term trend + gamma regime) that sets which way it leans, and a <i>downside skew</i> that keeps the down tail fatter. The legs sum back to the band total. It&rsquo;s a modest, conditional lean &mdash; not a forecast of what will happen.</dd>
        <dt>Big Move Ranking with Probabilities</dt><dd>The four indices ranked by the options-implied chance of a <b>&gt;3% move (either direction)</b> over the next ~5 sessions. A <b>size</b> ranking &mdash; where the widest swings are most likely, not which way.</dd>
        <dt>Breakeven Calculator</dt><dd>Enter <b>any two price levels</b> &mdash; expiration breakevens, T+0 breakevens, or support/resistance &mdash; and it returns the odds the index stays between them, evaluated at <i>your exact levels</i> rather than the round-percent band marks. Three horizons: <b>rest of day</b> (now &rarr; today&rsquo;s 4:00 PM ET close, taken from your computer&rsquo;s clock, so it tightens on its own through the afternoon), <b>overnight</b>, and <b>1-week</b>.</dd>
        <dt>Touch vs. ends-up</dt><dd>These are different questions and the gap between them is wide. <b>Touch</b> = the odds price <i>reaches</i> your level at any point before the horizon. <b>Ends up</b> = the odds it&rsquo;s past your level when the horizon arrives. Touch is roughly <b>double</b> ends-up, because price can tag a level and come back. If you adjust or exit when a level trades, <b>touch is your number</b> &mdash; ends-up will flatter the position.</dd>
        <dt>&sigma; distance</dt><dd>Each level is also shown as a distance in <b>standard deviations</b> from your reference price. This is usually the fastest read in the whole tool: a level 1&sigma; away is genuinely in play, one 3&sigma; away is background noise. When your two levels sit at very different &sigma;, the risk isn&rsquo;t two-sided &mdash; it&rsquo;s all on the near side.</dd>
        <dt>Current price / IV pt chg</dt><dd>The calculator starts from the price baked in at generation, but you can type the <b>live price</b> from your platform and everything re-computes around it. <b>IV pt chg</b> does the same for volatility &mdash; type <b>+2</b> if the vol index has risen two points since the run. Both are manual on purpose: the report never calls out to the internet.</dd>
        <dt>Risk dials</dt><dd>Calm / Elevated / High / Extreme &mdash; computed from the implied move size, with matching thresholds at both horizons so &ldquo;Elevated&rdquo; means the same vol regime on the overnight and 1-week rows.</dd>
        <dt>The Cushion (gamma)</dt><dd><b>Positive</b> = dealers buy dips/sell rips, moves fade. <b>Negative</b> = dealers amplify moves; pushes extend. <b>Thin</b> = no reliable positioning read. Computed from live options data where available.</dd>
        <dt>Whole-number levels</dt><dd>Round numbers act as magnets (option open-interest clusters there). Approximate &mdash; re-verify live.</dd>
        <dt>Breadth read</dt><dd>The spread between the four indices is a signal: a narrow tech move is positioning; a broad one is real risk-on/off.</dd>
      </dl>
    </div>
  </section>

  <div class="footer">
    <div class="disc">
      <div class="disc-text">
        <p><b>Educational purposes only &mdash; not investment advice.</b> The Freedom Management Group, Inc. d/b/a Michael Wade Trade Coaching is not a broker, adviser, or fiduciary. All trades are at your own risk; past performance does not guarantee future results. Options involve substantial risk and you can lose more than your investment &mdash; always paper trade first before risking real money. <b>This report is generated with the assistance of artificial intelligence, and AI can make mistakes.</b> The analysis, prices, technical levels, earnings dates, probabilities, and figures herein are produced by automated models that may misinterpret data, rely on sources that are outdated or inaccurate, or generate confident-sounding output that is simply wrong. Probabilities are options-implied estimates, not predictions, and real-world tails are fatter than a normal curve. Nothing here has been independently verified by a licensed professional. Always confirm every data point, price, and date against your own brokerage and primary sources before acting, and treat this report as a starting point for your own research &mdash; never as a substitute for your own judgment. By using our services, you agree to our <a href="https://www.mwtradecoach.com/terms-and-conditions">Terms &amp; Conditions</a> and <a href="https://www.mwtradecoach.com/privacy-policy">Privacy Policy</a>.</p>
      </div>
    </div>
    <p style="margin-top:12px">{ctx["label"].title()}, <b>time-stamped {ctx["long_date"]}, {ctx["time_str"]}</b>, into a {ctx["phrase"]} with a 1-week outlook &mdash; it goes stale quickly. {footer_note} Re-verify before trading. Nothing here is a directive to trade.</p>
    <p style="margin-top:10px;color:var(--faint)">Daily AI {ctx["gap_word"]} Gap Risk Report &middot; deterministic engine v2 &middot; drift+skew lean &middot; disjoint bands &middot; breakeven calculator (touch odds + intraday clock) &middot; Trade Club AI &middot; Generated {ctx["gen_date"]} ({ctx["label"].lower()}) &middot; mwtradecoach.com</p>
  </div>

{be_script}
</div></body></html>'''
    return toks(html)
