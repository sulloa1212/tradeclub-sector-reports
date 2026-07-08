"""Entry point: collect data, generate the report, save it.

Run locally:   python -m src.main
In CI:         invoked by .github/workflows/daily-report.yml
Flags:         --dry-run   collect data + write the packet JSON, skip the Claude call
               --version standard|institutional   (kept for compatibility)
"""
from __future__ import annotations

import sys
import json
import logging
import argparse
import datetime as dt
from zoneinfo import ZoneInfo

from . import config, checks
from .sources import macro, unusual_whales, fmp, sentiment, fedwatch, kalshi, earnings
from .report import generator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

ET = ZoneInfo("America/New_York")


def _fed_view(uw_packet: dict) -> dict:
    """Fed-relevant headlines (qualitative stance) + two market-implied odds
    sources to cross-reference: CME FedWatch probabilities (fedwatch, when
    entitled) and Kalshi prediction-market odds (kalshi, free/public). Either
    may be null; the report falls back to whatever is present."""
    news = uw_packet.get("news_headlines") or []
    fed_news = []
    if isinstance(news, list):
        for item in news:
            text = json.dumps(item).lower() if not isinstance(item, str) else item.lower()
            if any(k in text for k in config.FED_KEYWORDS):
                fed_news.append(item)
    return {
        "news": fed_news[:12] or None,
        "fedwatch": fedwatch.probabilities(),
        "kalshi": kalshi.fed_odds(),
    }


def _earnings_view(macro_packet: dict, uw_packet: dict, fmp_packet: dict) -> dict:
    """Two-source earnings calendar (Finnhub primary x FMP cross-check) + per-focus
    UW detail. The reconciler flags any report-date / reported-status disagreement
    so the report never asserts an unverified earnings date or actual."""
    per_focus = {}
    for t in config.UW_FOCUS_TICKERS:
        per_focus[t] = unusual_whales.earnings(t)
    verified = earnings.build(
        macro_packet.get("earnings_calendar"),
        fmp_packet.get("earnings_calendar"),
    )
    return {
        "calendar": verified.get("calendar"),
        "verification": verified.get("verification"),
        "second_source": verified.get("second_source"),
        "focus_detail": per_focus,
    }


def collect_data() -> dict:
    report_dt = dt.datetime.now(ET)
    log.info("Collecting macro data (yfinance, free)...")
    macro_packet = macro.collect()
    log.info("Collecting Unusual Whales institutional data (REST)...")
    uw_packet = unusual_whales.collect(config.UW_FOCUS_TICKERS,
                                       etf_spots=macro_packet.get("etf_spots"))
    log.info("Collecting FMP (movers, analyst actions, breadth)...")
    fmp_packet = fmp.collect()
    log.info("Collecting sentiment (Fear & Greed, AAII)...")
    sentiment_packet = sentiment.collect()

    # Economic calendar: prefer FMP (Finnhub gates it behind a paid tier —
    # confirmed empty on the free key during live testing). Finnhub stays fallback.
    macro_packet["economic_calendar"] = (
        fmp_packet.get("economic_calendar") or macro_packet.get("economic_calendar")
    )

    # Breadth: full S&P from FMP if the plan supports batch-quote, else the free
    # sector-ETF proxy from macro.
    breadth = fmp_packet.get("breadth") or macro.sector_breadth()

    earnings_view = _earnings_view(macro_packet, uw_packet, fmp_packet)
    ev = earnings_view.get("verification") or {}
    if ev.get("conflicts"):
        log.warning("Earnings cross-check conflicts (withheld as unverified): %s",
                    ev["conflicts"])

    # Anything still missing a connected feed (transparency for the report).
    not_wired = []
    if not earnings_view.get("second_source"):
        not_wired.append("earnings_second_source(FMP)")
    if not breadth:
        not_wired.append("market_breadth")
    if not (fmp_packet.get("gainers") or fmp_packet.get("losers")):
        not_wired.append("premarket_movers")
    if not fmp_packet.get("analyst_actions"):
        not_wired.append("analyst_actions")
    if not sentiment_packet.get("fear_greed"):
        not_wired.append("fear_and_greed")
    if not sentiment_packet.get("aaii"):
        not_wired.append("aaii")

    return {
        "generated_at_et": report_dt.strftime("%Y-%m-%d %H:%M ET"),
        "report_date": report_dt.strftime("%A, %B %d, %Y"),
        "is_monday": report_dt.weekday() == 0,
        "macro": macro_packet,
        "institutional": uw_packet,
        "movers": {"gainers": fmp_packet.get("gainers"), "losers": fmp_packet.get("losers")},
        "analyst_actions": fmp_packet.get("analyst_actions"),
        "insider": fmp_packet.get("insider") or uw_packet.get("insider"),
        "breadth": breadth,
        "sentiment": sentiment_packet,
        "fed": _fed_view(uw_packet),
        "earnings": earnings_view,
        "notes": {
            "price_levels": "yfinance values are prior-session closes, not live premarket.",
            "uw_available": uw_packet.get("available", False),
            "fmp_available": fmp_packet.get("available", False),
            "not_wired": not_wired or None,
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Collect data and dump the packet; skip the Claude call.")
    parser.add_argument("--check-keys", action="store_true",
                        help="Ping every provider and print a ✅/❌ table (no report, no token cost).")
    parser.add_argument("--mode", choices=["premarket", "postmarket"], default=None,
                        help="premarket (day ahead) or postmarket (closing recap).")
    args = parser.parse_args(argv)

    if args.check_keys:
        return checks.run()

    mode = (args.mode or config.REPORT_MODE or "premarket").lower()
    data = collect_data()
    data["mode"] = mode
    date_iso = dt.datetime.now(ET).strftime("%Y-%m-%d")

    if args.dry_run:
        config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        packet_path = config.REPORTS_DIR / f"_packet_{date_iso}.json"
        packet_path.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")
        log.info("DRY RUN — wrote data packet to %s (no report generated).", packet_path)
        return 0

    html = generator.generate(data, data["report_date"], mode=mode)
    out = generator.save(html, date_iso, mode=mode)
    print(f"Report written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
