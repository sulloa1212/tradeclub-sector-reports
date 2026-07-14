"""gap_engine.py — deterministic Gap Risk report engine.

Ported from the standalone `build_gap_report.py` ("v10 — drift+skew lean,
disjoint bands, breakeven calculator", 2026-07-13). ALL math — signals, leans,
band odds, dials, big-move ranking, calculator params — is computed here in
code; the model supplies ONLY narrative + judgment fields via the `content`
dict (see prompts/gap-risk.md for the contract). Orchestrated by build.py:

  1. gap_feed.fetch_all()            -> mechanical inputs (levels/vols/trend)
  2. run_context() + compute()       -> preliminary math (gamma "thin", adj 0)
  3. data_packet()                   -> injected into the model prompt
  4. model returns the content JSON  -> gamma/catalyst_adj/levels/prose
  5. compute() again with the model's judgment fields -> final numbers
  6. render()                        -> the full standalone HTML page

Narrative strings may embed tokens {LEAN_NDX} {LEAN_SPX} {LEAN_RUT} {LEAN_DJX}
{LEAN_LO} {LEAN_HI} — substituted with the FINAL computed leans at render time
so prose never contradicts the math.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# ── tunables (identical to the standalone builder) ──────────────────────────
OVN = 0.57                       # overnight share of full-session variance
ON_THR = [0.5, 1.0, 1.5, 2.0]    # overnight band thresholds (%)
WK_THR = [1.0, 2.0, 3.0, 4.0]    # weekly band thresholds (%)
BIGMOVE_THR = 3.0                # rank weekly by P(|move| > 3%)
SKEW_TENOR = 0.65                # weekly skew flattens: r_wk = 1 + (r-1)*this
DRIFT_K = 0.45                   # max center shift for a full ±1 signal (in σ)
DRIFT_TENOR = 0.55               # damp a one-night signal at the weekly tenor

SIG_W_PREOPEN = dict(fut=0.45, day=0.15, trend=0.22, mom=0.10, gamma=0.08)
SIG_W_NOFUT = dict(fut=0.00, day=0.50, trend=0.22, mom=0.12, gamma=0.16)

# Default skew ratios per index (put-skew steepness); model may override ±0.1.
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

DIALS = ["Calm", "Elevated", "High", "Extreme"]
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


# ── run context (run-type / gap phrase / weekend bump from the ET clock) ────
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
    )


# ── the drift + skew math (verbatim port) ───────────────────────────────────
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


def _size_dial_on(on_sig):
    return "Calm" if on_sig < 0.4 else "Elevated" if on_sig < 0.7 else "High" if on_sig < 1.05 else "Extreme"


def _size_dial_wk(wk_sig):
    return "Calm" if wk_sig < 1.5 else "Elevated" if wk_sig < 2.5 else "High" if wk_sig < 3.5 else "Extreme"


def _bump(dial, n):
    i = _clip(DIALS.index(dial) + int(n or 0), 0, len(DIALS) - 1)
    return DIALS[int(i)]


def compute(feed: dict, ctx: dict, judgment: dict | None = None,
            tolerant: bool = False) -> dict:
    """feed = gap_feed.fetch_all() output. judgment (optional) = per-index model
    fields {gamma, catalyst_adj, r, dial_bump, lvl_est?, vol_est?}. Missing
    mechanical inputs (level None) MUST be filled via judgment lvl_est/vol_est
    before the FINAL pass; tolerant=True (preliminary pass) skips those indices
    instead of raising so the data packet can flag them to the model."""
    judgment = judgment or {}
    out = {}
    for key in BOARD_ORDER:
        f = dict(feed[key])
        j = judgment.get(key, {})
        if f["lvl"] is None and j.get("lvl_est") is not None:
            f["lvl"], f["day"], f["est"] = float(j["lvl_est"]), float(j.get("day_est") or 0.0), True
        if f["vol"] is None and j.get("vol_est") is not None:
            f["vol"], f["vol_live"] = float(j["vol_est"]), False
        if f["lvl"] is None or f["vol"] is None:
            if tolerant:
                continue
            raise ValueError(f"{key}: missing level/vol and no model estimate supplied")
        nm, co, etf = NAMES[key]
        r = float(j.get("r") or R_DEFAULT[key])
        r = _clip(r, R_DEFAULT[key] - 0.1, R_DEFAULT[key] + 0.1)
        gamma = j.get("gamma") or "thin"
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
            vol_live=f["vol_live"], vn=f["vn"], r=r, r_wk=r_wk, sig=sig,
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
        ix["on_dial"] = _bump(_size_dial_on(on_sig), j.get("dial_bump"))
        ix["wk_dial"] = _size_dial_wk(wk_sig)
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
        "note": ("prelim leans assume gamma=thin, catalyst_adj=0 — your gamma/catalyst "
                 "fields will shift them; write {LEAN_*} tokens in prose, never literal lean numbers"),
        "missing_indices": missing,
        "missing_note": ("supply lvl_est/day_est/vol_est (searched live prints) for these "
                         "indices — the engine cannot run without them" if missing else ""),
        "indices": {},
    }
    for k, ix in IX.items():
        pkt["indices"][k] = {
            "nm": ix["nm"], "level": ix["lvl"], "level_est": ix["est"],
            "day_pct": ix["day"], "vol_index": ix["vn"], "vol": ix["vol"],
            "vol_live": ix["vol_live"],
            "overnight_1sd_pct": round(ix["on_sig"], 2),
            "overnight_1sd_pts": round(ix["on_pts"], 1),
            "week_1sd_pct": round(ix["wk_sig"], 2),
            "prelim_lean_down_pct": ln["on"][k],
            "p_big_week_pct": round(ix["p_big"] * 100),
            "on_dial_size_anchor": ix["on_dial"], "wk_dial": ix["wk_dial"],
            "trend": {"above_sma20": ix["above_sma20"], "above_sma50": ix["above_sma50"],
                      "ma_rising": ix["ma_rising"], "mom5_pct": ix["mom5_pct"],
                      "fut_pct": ix["fut_pct"]},
            "one_sd_range_overnight": f'{ix["on_lo"]} - {ix["on_hi"]}',
            "one_sd_range_week": f'{ix["wk_lo"]} - {ix["wk_hi"]}',
        }
    return json.dumps(pkt, indent=1)


# ── rendering (verbatim port, content-parameterized) ────────────────────────
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


def _be_calc(IX):
    params = {k: {"nm": IX[k]["nm"], "co": IX[k]["co"], "C": round(IX[k]["lvl"], 2),
                  "on": {"sd": round(IX[k]["on"]["sd"], 4), "su": round(IX[k]["on"]["su"], 4),
                         "mu": round(IX[k]["on"]["mu"], 4), "sd1": round(IX[k]["on_sig"], 3)},
                  "wk": {"sd": round(IX[k]["wk"]["sd"], 4), "su": round(IX[k]["wk"]["su"], 4),
                         "mu": round(IX[k]["wk"]["mu"], 4), "sd1": round(IX[k]["wk_sig"], 3)}}
              for k in IX}
    opts = "".join(f'<option value="{k}">{IX[k]["nm"]} &mdash; {IX[k]["co"]}</option>' for k in BE_ORDER)
    section = f'''
  <section id="becalc">
    <h2 class="sec-h"><span class="num" style="background:var(--accent);color:#08121e">&#x1F3AF;</span> Breakeven Probability Calculator</h2>
    <p style="color:var(--muted);font-size:13.5px;margin:0 0 12px">Enter your trade&rsquo;s two breakevens; this shows the odds the index <b>stays between them</b> (your profit zone) vs gaps out either side, using tonight&rsquo;s drift+skew model. It runs entirely in your browser &mdash; nothing is sent anywhere &mdash; on the numbers baked in when this report was generated.</p>
    <div class="panel becalc">
      <div class="berow">
        <label>Index<select id="beIx">{opts}</select></label>
        <label>Horizon<span class="beseg"><button type="button" class="beh on" data-h="on">Overnight</button><button type="button" class="beh" data-h="wk">1-Week</button></span></label>
        <span class="bemeta" id="beMeta"></span>
      </div>
      <div class="berow">
        <label>Lower breakeven (price)<input id="beLo" type="number" step="any" inputmode="decimal" placeholder="e.g. 7,430"></label>
        <label>Upper breakeven (price)<input id="beHi" type="number" step="any" inputmode="decimal" placeholder="e.g. 7,600"></label>
      </div>
      <div class="beout">
        <div class="becard bein"><div class="bev" id="beInside">&mdash;</div><div class="bel">Stay between (profit)</div></div>
        <div class="becard bedn"><div class="bev" id="beBelow">&mdash;</div><div class="bel">Gap below lower</div></div>
        <div class="becard beup"><div class="bev" id="beAbove">&mdash;</div><div class="bel">Gap above upper</div></div>
        <div class="becard belo"><div class="bev" id="beLoss">&mdash;</div><div class="bel">Breach either (loss)</div></div>
      </div>
      <div class="note" id="beHint">Enter both breakevens as price levels to see the odds. Overnight = this session&rsquo;s close &rarr; next open.</div>
    </div>
  </section>'''
    script = '''
<script>
(function(){
  var BE=__BE_JSON__;
  function erf(x){var s=x<0?-1:1;x=Math.abs(x);var a1=0.254829592,a2=-0.284496736,a3=1.421413741,a4=-1.453152027,a5=1.061405429,p=0.3275911;var t=1/(1+p*x);var y=1-(((((a5*t+a4)*t+a3)*t+a2)*t+a1)*t*Math.exp(-x*x));return s*y;}
  function Phi(z){return 0.5*(1+erf(z/Math.SQRT2));}
  function F(pp,L){var wd=pp.sd/(pp.sd+pp.su),wu=pp.su/(pp.sd+pp.su);if(L<=pp.mu){return wd*(2*Phi((L-pp.mu)/pp.sd));}return wd+wu*(2*Phi((L-pp.mu)/pp.su)-1);}
  var $=function(id){return document.getElementById(id);};
  var ixSel=$('beIx'),loI=$('beLo'),hiI=$('beHi'),elIn=$('beInside'),elLo=$('beBelow'),elUp=$('beAbove'),elLoss=$('beLoss'),meta=$('beMeta'),hint=$('beHint');
  var horizon='on';
  function pct(v){return (Math.round(v*1000)/10).toFixed(1)+'%';}
  function fnum(n){return n>=1000?Math.round(n).toLocaleString():n.toFixed(1);}
  function metaUpd(){var d=BE[ixSel.value],p=d[horizon];meta.innerHTML='close <b>'+fnum(d.C)+'</b> &middot; '+(horizon==='on'?'overnight':'1-week')+' 1SD &plusmn;'+p.sd1.toFixed(2)+'%';}
  function calc(){
    var d=BE[ixSel.value],p=d[horizon],C=d.C;metaUpd();
    var lo=parseFloat(loI.value),hi=parseFloat(hiI.value);
    if(isNaN(lo)||isNaN(hi)){elIn.textContent='\\u2014';elLo.textContent='\\u2014';elUp.textContent='\\u2014';elLoss.textContent='\\u2014';hint.innerHTML='Enter both breakevens as price levels to see the odds.';return;}
    if(lo>=hi){hint.innerHTML='<b style="color:#f87171">Lower breakeven must be below the upper.</b>';return;}
    var below=F(p,(lo-C)/C*100), above=1-F(p,(hi-C)/C*100), inside=1-below-above;
    if(inside<0)inside=0;
    elIn.textContent=pct(inside);elLo.textContent=pct(below);elUp.textContent=pct(above);elLoss.textContent=pct(below+above);
    hint.innerHTML='<b>'+d.nm+'</b> '+(horizon==='on'?'overnight':'1-week')+': <b>'+pct(inside)+'</b> chance it stays between '+fnum(lo)+' and '+fnum(hi)+' (from close '+fnum(C)+'). Snapshot as of the report&rsquo;s run time; re-check against live prices.';
  }
  ixSel.addEventListener('change',calc);loI.addEventListener('input',calc);hiI.addEventListener('input',calc);
  Array.prototype.forEach.call(document.querySelectorAll('.beh'),function(b){b.addEventListener('click',function(){horizon=b.getAttribute('data-h');Array.prototype.forEach.call(document.querySelectorAll('.beh'),function(x){x.classList.remove('on');});b.classList.add('on');calc();});});
  metaUpd();
})();
</script>'''.replace("__BE_JSON__", json.dumps(params))
    return section, script


def _daycell(d):
    if d > 0:
        return f'<td class="num pos">+{d:.1f}%</td>'
    if d < 0:
        return f'<td class="num neg">&minus;{abs(d):.1f}%</td>'
    return '<td class="num flat">+0.0%</td>'


def _card(ix, c, ctx, on_note, wk_note_html):
    """One index drill card. c = that index's content dict from the model."""
    lvls = c.get("levels") or {}
    res = (lvls.get("res") or ["&mdash;", "&mdash;"]) + ["&mdash;"] * 2
    sup = (lvls.get("sup") or ["&mdash;", "&mdash;"]) + ["&mdash;"] * 2
    special = ""
    if c.get("cushion_line"):
        special = (f'<div class="lvrow"><span class="lab">Cushion line</span>'
                   f'<span class="chip f">{c["cushion_line"]}</span></div>')
    cclass = "thin" if c.get("cushion_thin") else ""
    day = ix["day"]
    day_html = (f'<span class="pos">+{day:.2f}%</span>' if day > 0
                else f'<span class="neg">&minus;{abs(day):.2f}%</span>' if day < 0
                else '<span class="flat">+0.00%</span>')
    est_tag = ", est." if ix["est"] else ""
    dsub = (f'Live <b>{ix["disp"]}</b> ({day_html}{est_tag}) &nbsp;&middot;&nbsp; '
            f'overnight 1SD <b>&plusmn;{ix["on_sig"]:.2f}%</b> (&plusmn;{ix["on_pts"]:,.0f} pts) &nbsp;&middot;&nbsp; '
            f'1-week 1SD <b>&plusmn;{ix["wk_sig"]:.2f}%</b> &nbsp;&middot;&nbsp; {c.get("character", "")}')
    pts_fmt = f"{ix['on_pts']:,.1f}" if ix["lvl"] < 1000 else f"{ix['on_pts']:,.0f}"
    on_block = _odds_table(
        f'{ctx["gap_word"]} gap &mdash; odds {ctx["next_day"]} opens DOWN vs UP (from {ix["disp"]})',
        ix["on"]["lean_dn"], ix["on"], on_note.replace("{vol}", c.get("vol_note", "")))
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
            <span class="h">{c.get("cushion_head", "")}</span>
            {c.get("cushion_text", "")}
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
        <div>{c.get("driver", "")}</div>
        <div>{c.get("gapfill", "")}</div>
      </div>
    </div>
  </section>'''


def render(IX: dict, content: dict, ctx: dict, style: str,
           tc_logo: str = "/assets/tradeclub-ai.png",
           mw_logo: str = "/assets/mw.png") -> str:
    """Full standalone HTML page from computed stats + model content."""
    ln = leans(IX)

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
               'with the options put-skew that shapes the tails; on a night-before run it will sharpen as '
               'futures fill in. Each row is a <b>band</b> (a slice of where '
               f'{ctx["next_day"]}&rsquo;s open could land) and the <b>odds it lands in that slice</b>; '
               'the <b>worst lvl</b> is the far edge of the slice. Because the bands don&rsquo;t overlap, '
               'the <b>odds add up</b> &mdash; all down bands sum to the down lean, all up bands to the up '
               'lean, everything to 100%. For a level between the marks, use the <b>Breakeven Calculator</b> '
               'up top. {vol}')
    wk_note_html = ('This is the <b>1-week outlook</b> &mdash; the implied move over the <b>next ~5 trading '
                    'sessions</b> from each index&rsquo;s own vol index (no weekend bump; full-session '
                    'variance). Each row is a <b>band</b> and the <b>odds it lands in that slice</b>; the '
                    '<b>worst lvl</b> is the far edge. The bands don&rsquo;t overlap, so the <b>odds add '
                    'up</b>. Probabilities are options-implied estimates, not predictions &mdash; verify the '
                    'live catalysts before acting.')

    board = "\n".join(
        f'''      <tr>
        <td class="inst"><a href="#{k}"><b>{IX[k]['nm']}</b> <small>({IX[k]['etf']})</small></a></td>
        <td class="num">{IX[k]['disp']}{' <small style="color:var(--faint)">est</small>' if IX[k]['est'] else ''}</td>
        {_daycell(IX[k]['day'])}
        <td class="num">&plusmn;{IX[k]['on_sig']:.2f}% <small style="color:var(--faint)">{fmt(IX[k]['on_pts'], IX[k]['lvl'])}p</small></td>
        <td class="num"><b class="dn">{round(IX[k]['on']['lean_dn']*100)}%</b> <small style="color:var(--faint)">down</small></td>
        <td><span class="dialpill {DIALPILL[IX[k]['on_dial']]}">{IX[k]['on_dial']}</span></td>
        <td class="lvls">S {((content['indices'].get(k, {}).get('levels') or {}).get('sup') or ['&mdash;','&mdash;'])[1]} / {((content['indices'].get(k, {}).get('levels') or {}).get('sup') or ['&mdash;','&mdash;'])[0]} &middot; R {((content['indices'].get(k, {}).get('levels') or {}).get('res') or ['&mdash;','&mdash;'])[0]} / {((content['indices'].get(k, {}).get('levels') or {}).get('res') or ['&mdash;','&mdash;'])[1]}</td>
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

    cards = "".join(_card(IX[k], content["indices"].get(k, {}), ctx, on_note, wk_note_html)
                    for k in BOARD_ORDER)
    be_section, be_script = _be_calc(IX)

    tldr = "".join(f"      <li>{b}</li>\n" for b in content.get("tldr", []))
    clock = "".join(f'      <div class="ce"><span class="t">{r.get("t", "")}</span>'
                    f'<span class="w">{r.get("w", "")}</span></div>\n'
                    for r in content.get("clock", []))
    cal_rows = ""
    for r in content.get("calendar", []):
        cls = ' class="done"' if r.get("done") else ""
        dt_style = ' style="color:#f87171"' if r.get("hot") else ""
        cal_rows += (f'        <tr{cls}><td class="dt"{dt_style}>{r.get("when", "")}</td>'
                     f'<td>{r.get("event", "")}</td><td>{r.get("why", "")}</td></tr>\n')
    do_lis = "".join(f"        <li>{x}</li>\n" for x in (content.get("playbook") or {}).get("do", []))
    dont_lis = "".join(f"        <li>{x}</li>\n" for x in (content.get("playbook") or {}).get("dont", []))
    heads = content.get("heads") or {}
    banner = content.get("banner") or {}

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
    <a href="#becalc">Breakeven</a><a href="#bigmove">Big Move</a><a href="#clock">Clock</a><a href="#calendar">Calendar</a><a href="#playbook">Playbook</a>
  </div>

  <div class="heads">
    <div class="icon">&#x26A0;&#xFE0F;</div>
    <div><p class="t">{heads.get("t", "")}</p>
    <p class="b">{heads.get("b", "")}</p></div>
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
    <div class="breadth"><b>Breadth read:</b> {content.get("breadth", "")}</div>
  </section>
{be_section}
{cards}

  <section id="bigmove">
    <h2 class="sec-h"><span class="num">2</span> Big Move Ranking with Probabilities &mdash; 1-Week Horizon</h2>
    <p style="color:var(--muted);font-size:13.5px;margin:0 0 12px">Which index is most likely to make a <b>big move</b> over the <b>next ~5 trading sessions</b>? Ranked by the options-implied probability of a <b>&gt;3% move in either direction</b> this week (each index&rsquo;s own vol index). Each row links to that index&rsquo;s full 1-week odds table above.</p>
    <div class="panel" style="padding:6px 18px">
      <table class="board">
        <tr><th class="num" style="width:36px">Rank</th><th class="inst">Index (ETF)</th><th>1-Week 1SD</th><th>Prob. of a &gt;3% week</th><th>Lean</th><th>1-Week Dial</th></tr>
{bigboard}
      </table>
    </div>
    <div class="breadth" style="margin-top:12px"><b>How to read it:</b> the ranking is about <b>size, not direction</b> &mdash; it says where the widest swings are most likely, not which way. {content.get("bigmove_note", "")} Pair this with the per-index 1-week tables above for the full down/up split and price targets.</div>
  </section>

  <section id="clock">
    <h2 class="sec-h"><span class="num">3</span> The Overnight Clock &mdash; Where {ctx["next_day"]}&rsquo;s Gap Gets Made</h2>
    <p style="color:var(--muted);font-size:13.5px;margin:0 0 12px">{content.get("clock_intro", "")}</p>
    <div class="clock">
{clock}    </div>
  </section>

  <section id="calendar">
    <h2 class="sec-h"><span class="num">4</span> Event Calendar &mdash; Next Few Sessions</h2>
    <div class="panel" style="padding:6px 18px">
      <table class="cal">
        <tr><th class="dt">When</th><th>Event</th><th>Why it matters for the gap</th></tr>
{cal_rows}      </table>
    </div>
    <div class="note" style="margin-top:8px">&#9888; <b>Honesty note:</b> {content.get("calendar_note", "confirm every event and figure against a primary source before acting.")}</div>
  </section>

  <section id="playbook">
    <h2 class="sec-h"><span class="num">5</span> Overnight + 1-Week Playbook</h2>
    <div class="dodont">
      <div class="col do"><h3>&#x2705; DO</h3><ul>
{do_lis}      </ul></div>
      <div class="col dont"><h3>&#x274C; DON&rsquo;T</h3><ul>
{dont_lis}      </ul></div>
    </div>
  </section>

  <section>
    <div class="banner">
      <div class="icon">&#x1F4CC;</div>
      <div>
        <p class="title">{banner.get("title", "")}</p>
        <p class="body">{banner.get("body", "")}</p>
      </div>
    </div>
  </section>

  <section>
    <h2 class="sec-h">How To Read This Report</h2>
    <div class="legend">
      <dl style="margin:0">
        <dt>Run type</dt><dd><b>Pre-market</b>: overnight futures already trading &mdash; the direction read is sharpest. <b>Mid-session / post-market</b>: the direction read is driven by the <b>drift signal</b> (short-term trend + today&rsquo;s tape + gamma) plus skew; it sharpens as overnight futures trade.</dd>
        <dt>{ctx["gap_word"]} gap</dt><dd>This session&rsquo;s close &rarr; the next session&rsquo;s open{" &mdash; the band is bumped ~25% for the extra closed-market days" if ctx["weekend"] > 1 else " (~1 closed night), so the implied band is the plain overnight 1SD"}.</dd>
        <dt>1-Week implied move</dt><dd>The one-standard-deviation band over the <b>next ~5 trading sessions</b>, from each index&rsquo;s own vol index (full-session variance, no weekend bump). A size, not a direction.</dd>
        <dt>Odds bands</dt><dd>Each row is a <b>slice</b> of where the open could land and the <b>odds it lands in that slice</b>. The <b>worst lvl</b> is the far (outer) edge of the slice; the near edge is the row above it. The slices don&rsquo;t overlap, so the <b>odds add up</b> &mdash; all down bands sum to the down lean, all up bands to the up lean, everything to 100%. For a level <i>between</i> the marks (like your actual breakeven), use the Breakeven Calculator.</dd>
        <dt>Direction Split</dt><dd>The band sliced into a down leg and an up leg by a model with two inputs: a <i>directional drift</i> (overnight futures + short-term trend + gamma regime) that sets which way it leans, and a <i>downside skew</i> that keeps the down tail fatter. The legs sum back to the band total. It&rsquo;s a modest, conditional lean &mdash; not a forecast of what will happen.</dd>
        <dt>Big Move Ranking with Probabilities</dt><dd>The four indices ranked by the options-implied chance of a <b>&gt;3% move (either direction)</b> over the next ~5 sessions. A <b>size</b> ranking &mdash; where the widest swings are most likely, not which way.</dd>
        <dt>Breakeven Calculator</dt><dd>Enter your trade&rsquo;s two breakevens and it returns the odds the index <b>stays between them</b> (profit) vs gaps out either side, for the overnight or 1-week horizon. Uses the same drift+skew model, evaluated at <i>your exact levels</i>. Runs entirely in your browser on the numbers baked in at generation &mdash; it&rsquo;s a snapshot, so re-check against live prices.</dd>
        <dt>Risk dials</dt><dd>Calm / Elevated / High / Extreme &mdash; blend implied size with fragility (cushion/gamma and catalysts). Each index has an <b>overnight</b> dial and a <b>1-week</b> dial.</dd>
        <dt>The Cushion (gamma)</dt><dd><b>Positive</b> = dealers buy dips/sell rips, moves fade. <b>Off</b> (below the &ldquo;cushion line&rdquo;) = they sell into weakness, moves snowball. <b>Thin</b> = sitting right at the line.</dd>
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
    <p style="margin-top:12px">{ctx["label"].title()}, <b>time-stamped {ctx["long_date"]}, {ctx["time_str"]}</b>, into a {ctx["phrase"]} with a 1-week outlook &mdash; it goes stale quickly. {content.get("footer_note", "")} The per-index directional <b>signal</b> is mechanical (trend/momentum/futures from live market data; gamma/catalyst from an analyst read), not a calibrated model. Re-verify before trading. Nothing here is a directive to trade.</p>
    <p style="margin-top:10px;color:var(--faint)">Daily AI {ctx["gap_word"]} Gap Risk Report &middot; deterministic engine &middot; drift+skew lean &middot; disjoint bands &middot; breakeven calculator &middot; Trade Club AI &middot; Generated {ctx["gen_date"]} ({ctx["label"].lower()}) &middot; mwtradecoach.com</p>
  </div>

{be_script}
</div></body></html>'''
    return toks(html)
