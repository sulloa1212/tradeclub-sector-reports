"""Real pre-market prices from yfinance (5-minute bars incl. extended hours).

FMP's biggest-gainers/losers rank the LAST REGULAR SESSION, so at 8:40 they are
yesterday's movers (CLF/IRON/SLN shown as "pre-market" on 2026-10-20). This
module reads this morning's actual pre-market prints instead.

Yahoo facts (measured 2026-10-26): the index comes back in UTC, and every
pre-market bar has Volume 0 — so liquidity is measured by how many separate
5-minute windows traded, not by dollar volume. A bar exists only if at least
one trade printed in it. Everything degrades to None/{} on failure.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Callable, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("premarket")

ET = ZoneInfo("America/New_York")
PRE_OPEN, OPEN, CLOSE = dt.time(4, 0), dt.time(9, 30), dt.time(16, 0)
MIN_TRADE_WINDOWS = 6   # 5-minute windows with a trade since 4:00 AM ET
MAX_AGE_MIN = 30        # the last trade must be this fresh
MIN_DISTINCT_RECENT = 3  # distinct prices among the last 6 traded windows
REF_TOL = 0.005         # $: a print this close to the prior close may be a reference print...
REF_AWAY = 0.005        # ...when the print before it traded more than 0.5% away
BAR = dt.timedelta(minutes=5)


def yahoo_symbol(sym: str) -> str:
    return str(sym).strip().upper().replace(".", "-")


def _fmt(t: dt.datetime) -> str:
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'} ET"


def _download(symbols: list, period: str = "5d") -> dict:
    """{symbol: DataFrame[Close]} of 5-minute bars incl. pre/post market, keyed
    by the caller's spelling (BRK.B), fetched under Yahoo's (BRK-B)."""
    import pandas as pd
    import yfinance as yf
    ys = [yahoo_symbol(s) for s in symbols]
    df = yf.download(ys, period=period, interval="5m", prepost=True, group_by="ticker",
                     auto_adjust=False, progress=False, threads=True)
    out = {}
    for s, y in zip(symbols, ys):
        try:
            sub = df[y] if isinstance(df.columns, pd.MultiIndex) else df
        except KeyError:
            continue
        sub = sub[["Close"]].dropna()
        if not sub.empty:
            out[s] = sub
    return out


def _daily_closes(symbols: list, period: str = "5d") -> dict:
    """{symbol: {date: official close}} from daily bars. The closing auction
    can differ from the last regular 5-minute bar by a few tenths of a percent
    (META 2026-10-21: $697.52 official vs $698.43 at 15:55)."""
    import pandas as pd
    import yfinance as yf
    ys = [yahoo_symbol(s) for s in symbols]
    df = yf.download(ys, period=period, interval="1d", group_by="ticker",
                     auto_adjust=False, progress=False, threads=True)
    out = {}
    for s, y in zip(symbols, ys):
        try:
            col = (df[y] if isinstance(df.columns, pd.MultiIndex) else df)["Close"].dropna()
        except KeyError:
            continue
        out[s] = {ts.date(): float(v) for ts, v in col.items()}
    return out


def summarize(bars, now_et: dt.datetime, official: Optional[dict] = None) -> Optional[dict]:
    """Prior close and this morning's last pre-market print, measured at
    min(now, 9:30) so a late re-run still reads the pre-market. The prior close
    is the official one (`official` = {date: close}) when known, else the last
    regular 5-minute bar. None when neither exists."""
    if bars is None or len(bars) == 0:
        return None
    b = bars.copy()
    b.index = b.index.tz_convert(ET) if b.index.tz is not None else b.index.tz_localize(ET)
    today = now_et.date()
    ref = min(now_et, dt.datetime.combine(today, OPEN, ET))
    times, dates = b.index.time, b.index.date
    regular = b[(dates < today) & (times >= OPEN) & (times < CLOSE)]
    prior = [d for d in (official or {}) if d < today]   # Yahoo may add today's row early
    if prior:
        prev_close = float(official[max(prior)])
    elif not regular.empty:
        prev_close = float(regular["Close"].iloc[-1])
    else:
        return None
    pre = b[(dates == today) & (times >= PRE_OPEN) & (times < OPEN) & (b.index < ref)]
    base = {"prev_close": round(prev_close, 2), "last": None, "pct_change": None,
            "last_print_et": None, "age_min": None, "trade_windows": len(pre),
            "distinct_recent": 0}
    if pre.empty or prev_close <= 0:
        return base
    closes = [float(c) for c in pre["Close"]]
    # Yahoo's tape carries prints at EXACTLY the prior close while the stock
    # trades elsewhere (RTX 2026-10-19: ~190.5 all morning, one 193.92 print at
    # 8:38), which would read as +0.0%. Step back over such prints.
    k = len(closes) - 1
    while (k > 0 and abs(closes[k] - prev_close) <= REF_TOL
           and abs(closes[k - 1] / prev_close - 1) > REF_AWAY):
        k -= 1
    last = closes[k]
    base["distinct_recent"] = len({round(c, 4) for c in closes[max(0, k - 5):k + 1]})
    printed = min(pre.index[k].to_pydatetime() + BAR, ref)   # end of the last traded bar
    base.update(last=round(last, 2), pct_change=(last / prev_close - 1) * 100,
                last_print_et=_fmt(printed),
                age_min=(ref - printed).total_seconds() / 60)
    return base


def snapshot(symbols: list, now_et: Optional[dt.datetime] = None,
             download: Optional[Callable] = None, closes: Optional[Callable] = None) -> dict:
    now_et = now_et or dt.datetime.now(ET)
    download = download or _download
    closes = closes or _daily_closes
    try:
        frames = download(list(symbols))
    except Exception as e:  # noqa: BLE001
        log.warning("pre-market download failed: %s", e)
        return {}
    try:
        official = closes(list(frames)) if frames else {}
    except Exception as e:  # noqa: BLE001
        log.warning("official closes unavailable (%s) — using the last regular 5-minute bar", e)
        official = {}
    out = {}
    for s, f in frames.items():
        try:
            v = summarize(f, now_et, official.get(s))
        except Exception as e:  # noqa: BLE001
            log.warning("pre-market summary failed for %s: %s", s, e)
            continue
        if v is not None:
            out[s] = v
    return out


def movers(universe: list, now_et: Optional[dt.datetime] = None, n: int = 10,
           download: Optional[Callable] = None,
           closes: Optional[Callable] = None) -> Optional[dict]:
    """Top pre-market gainers/losers in the universe. None when no prices came
    back at all; `unavailable` explains an empty list."""
    now_et = now_et or dt.datetime.now(ET)
    names, syms = {}, []
    for u in universe or []:
        s = str((u or {}).get("symbol") or "").strip().upper()
        if s and s not in names:
            names[s] = u.get("name")
            syms.append(s)
    snap = snapshot(syms, now_et, download, closes)
    if not snap:
        return None
    rows = []
    for s, v in snap.items():
        # Activity AND price variation: one stuck price printed in every window
        # is not trading (LYV 2026-10-14: #1 "gainer" +3.7%, opened -1.3%).
        if (v["pct_change"] is None or v["trade_windows"] < MIN_TRADE_WINDOWS
                or v["age_min"] > MAX_AGE_MIN or v["distinct_recent"] < MIN_DISTINCT_RECENT):
            continue
        rows.append({"ticker": s, "name": names.get(s), "pct_change": round(v["pct_change"], 2),
                     "price": v["last"], "prev_close": v["prev_close"],
                     "last_print_et": v["last_print_et"], "trade_windows": v["trade_windows"]})
    as_of = _fmt(min(now_et, dt.datetime.combine(now_et.date(), OPEN, ET)))
    gainers = sorted((r for r in rows if r["pct_change"] > 0), key=lambda r: -r["pct_change"])[:n]
    losers = sorted((r for r in rows if r["pct_change"] < 0), key=lambda r: r["pct_change"])[:n]
    log.info("pre-market movers: %d in universe, %d priced, %d qualified",
             len(syms), len(snap), len(rows))
    return {
        "gainers": gainers, "losers": losers, "as_of_et": as_of,
        "universe_size": len(syms), "priced": len(snap), "qualified": len(rows),
        "unavailable": None if (gainers or losers) else (
            f"no S&P 500 or Nasdaq-100 stock traded in at least {MIN_TRADE_WINDOWS} separate "
            f"5-minute windows with a trade in the {MAX_AGE_MIN} minutes before {as_of}"),
        "source": "yfinance 5-minute bars incl. pre-market",
    }


def reactions(symbols: list, now_et: Optional[dt.datetime] = None,
              download: Optional[Callable] = None, closes: Optional[Callable] = None) -> dict:
    """{symbol: {"pct", "as_of_et"}} for names with a fresh pre-market print."""
    out = {}
    for s, v in snapshot(list(dict.fromkeys(symbols)), now_et, download, closes).items():
        if v["pct_change"] is not None and v["age_min"] <= MAX_AGE_MIN:
            out[s] = {"pct": round(v["pct_change"], 2), "as_of_et": v["last_print_et"]}
    return out
