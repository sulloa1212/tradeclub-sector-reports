import datetime as dt
import unittest
from unittest import mock

from mwtc.sources import sessions, macro


class PreviousTradingDay(unittest.TestCase):
    def test_previous_trading_day_cases(self):
        cases = {
            "2026-10-20": "2026-10-19",   # Tue -> Mon
            "2026-10-19": "2026-10-16",   # Mon -> Fri
            "2026-11-27": "2026-11-25",   # day after Thanksgiving -> Wed
            "2026-11-30": "2026-11-27",   # Mon after the 11-27 half day -> Fri
            "2026-09-08": "2026-09-04",   # Tue after Labor Day -> Fri
        }
        for d, want in cases.items():
            got = sessions.previous_trading_day(dt.date.fromisoformat(d)).isoformat()
            self.assertEqual(got, want, d)

    def test_weekday_fallback_without_calendar(self):
        with mock.patch.object(sessions, "_nyse_sessions", side_effect=RuntimeError("no calendar")):
            self.assertEqual(sessions.previous_trading_day(dt.date(2026, 10, 19)).isoformat(), "2026-10-16")


def row(sym, date, hour, eps_a=1.0, eps_e=0.9):
    return {"symbol": sym, "date": date, "hour": hour, "epsActual": eps_a, "epsEstimate": eps_e}


class OvernightWindow(unittest.TestCase):
    def enrich(self, r, today):
        prev = sessions.previous_trading_day(dt.date.fromisoformat(today)).isoformat()
        return macro._enrich_earnings(r, today, prev)

    def test_2026_10_20_replay(self):
        # Monday 10-19: CLF and FITB reported before the open; STLD, AERO, WRB
        # and CCK after the close. Only the after-close ones are overnight news
        # on Tuesday 10-20.
        today = "2026-10-20"
        flags = {s: self.enrich(row(s, "2026-10-19", h), today)["is_overnight_result"]
                 for s, h in (("CLF", "bmo"), ("FITB", "bmo"), ("STLD", "amc"),
                              ("AERO", "amc"), ("WRB", "amc"), ("CCK", "amc"))}
        self.assertEqual(flags, {"CLF": False, "FITB": False, "STLD": True,
                                 "AERO": True, "WRB": True, "CCK": True})

    def test_monday_includes_friday_after_close(self):
        r = self.enrich(row("ABC", "2026-10-16", "amc"), "2026-10-19")
        self.assertTrue(r["is_overnight_result"])

    def test_day_after_thanksgiving(self):
        # Thanksgiving 2026-11-26 is a holiday: Wednesday's after-close
        # reporters are Friday's overnight news.
        self.assertTrue(self.enrich(row("XYZ", "2026-11-25", "amc"), "2026-11-27")["is_overnight_result"])

    def test_today_before_open_and_untimed(self):
        self.assertTrue(self.enrich(row("A", "2026-10-20", "bmo"), "2026-10-20")["is_overnight_result"])
        self.assertTrue(self.enrich(row("B", "2026-10-20", ""), "2026-10-20")["is_overnight_result"])

    def test_previous_day_untimed_is_unconfirmed(self):
        r = self.enrich(row("C", "2026-10-19", ""), "2026-10-20")
        self.assertFalse(r["is_overnight_result"])
        self.assertTrue(r["timing_unconfirmed"])

    def test_not_reported_is_never_overnight(self):
        r = self.enrich(row("D", "2026-10-19", "amc", eps_a=None), "2026-10-20")
        self.assertFalse(r["is_overnight_result"])
        self.assertFalse(r["timing_unconfirmed"])

    def test_finnhub_window_starts_at_previous_trading_day(self):
        seen = {}
        def fake(path, params, key):
            seen.update(params)
            return []
        with mock.patch.object(macro, "_finnhub_calendar", side_effect=fake):
            macro.earnings_calendar(today=dt.date(2026, 10, 19))
        self.assertEqual(seen["from"], "2026-10-16")


if __name__ == "__main__":
    unittest.main()
