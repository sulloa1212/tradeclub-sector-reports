"""NYSE session arithmetic for the "since last close" window."""
from __future__ import annotations

import datetime as dt
import logging

log = logging.getLogger("sessions")


def _nyse_sessions(start: dt.date, end: dt.date) -> list:
    import pandas_market_calendars as mcal
    sched = mcal.get_calendar("NYSE").schedule(start_date=start.isoformat(),
                                               end_date=end.isoformat())
    return [ts.date() for ts in sched.index]


def previous_trading_day(d: dt.date) -> dt.date:
    """The NYSE session before `d`: weekends and exchange holidays skipped.
    Falls back to plain weekday arithmetic if the calendar is unavailable."""
    try:
        days = _nyse_sessions(d - dt.timedelta(days=14), d - dt.timedelta(days=1))
        if days:
            return days[-1]
    except Exception as e:  # noqa: BLE001
        log.warning("NYSE calendar unavailable (%s) — weekday fallback", e)
    p = d - dt.timedelta(days=1)
    while p.weekday() >= 5:
        p -= dt.timedelta(days=1)
    return p
