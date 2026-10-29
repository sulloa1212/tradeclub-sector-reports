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

    def test_weekend_dated_report_is_overnight(self):
        # Berkshire reports on a Saturday morning: Monday's overnight news.
        for hour in ("", "bmo", "amc"):
            r = self.enrich(row("BRK.B", "2026-10-31", hour), "2026-11-02")
            self.assertTrue(r["is_overnight_result"], hour)
            self.assertFalse(r["timing_unconfirmed"], hour)

    def test_holiday_dated_report_is_overnight(self):
        self.assertTrue(self.enrich(row("XYZ", "2026-11-26", ""), "2026-11-27")["is_overnight_result"])

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
        calls = []
        def fake(path, params, key):
            calls.append(dict(params))
            return []
        with mock.patch.object(macro, "_finnhub_calendar", side_effect=fake):
            macro.earnings_calendar(today=dt.date(2026, 10, 19))
        # Both the wide window and the recent-days call start on Friday, not Sunday.
        self.assertEqual([c["from"] for c in calls], ["2026-10-16", "2026-10-16"])
        self.assertIn("2026-10-19", [c["to"] for c in calls])

    def test_recent_days_survive_a_truncated_wide_window(self):
        # 2026-10-29: the wide window came back without 10/28-10/29 rows.
        wide = [row(f"Z{i}", "2026-11-03", "bmo", eps_a=None) for i in range(50)]
        recent = [row("MSFT", "2026-10-28", "amc"), row("CAT", "2026-10-29", "bmo", eps_a=None)]
        def fake(path, params, key):
            return recent if params["to"] == "2026-10-29" else wide
        with mock.patch.object(macro, "_finnhub_calendar", side_effect=fake):
            out = macro.earnings_calendar(today=dt.date(2026, 10, 29))
        self.assertEqual([r["symbol"] for r in out[:2]], ["MSFT", "CAT"])
        self.assertTrue(out[0]["is_overnight_result"])
        self.assertTrue(out[1]["is_today"])
        self.assertEqual(len(out), 40)

    def test_recent_call_failing_keeps_the_wide_window(self):
        wide = [row("A", "2026-10-28", "amc"), row("B", "2026-11-03", "bmo", eps_a=None)]
        def fake(path, params, key):
            return None if params["to"] == "2026-10-29" else wide
        with mock.patch.object(macro, "_finnhub_calendar", side_effect=fake):
            out = macro.earnings_calendar(today=dt.date(2026, 10, 29))
        self.assertEqual([r["symbol"] for r in out], ["A", "B"])

    def test_duplicate_rows_are_kept_once(self):
        a = row("A", "2026-10-28", "amc")
        def fake(path, params, key):
            return [dict(a)]
        with mock.patch.object(macro, "_finnhub_calendar", side_effect=fake):
            out = macro.earnings_calendar(today=dt.date(2026, 10, 29))
        self.assertEqual([r["symbol"] for r in out], ["A"])


if __name__ == "__main__":
    unittest.main()
