import datetime as dt
import sys
import types
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd

from mwtc.sources import premarket as pm

ET = ZoneInfo("America/New_York")
MON, TUE = dt.date(2026, 10, 19), dt.date(2026, 10, 20)
AT_840 = dt.datetime(2026, 10, 20, 8, 40, tzinfo=ET)


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return dt.datetime.combine(day, dt.time(h, m), ET)


def bars(prev_day, prev_close, today, prints):
    """5-minute bars the way Yahoo returns them: UTC index, Volume 0 in the
    pre-market. The end of a regular session on prev_day at prev_close, then
    pre-market `prints` = [(HH:MM, close)] on `today`."""
    idx = [at(prev_day, "15:50"), at(prev_day, "15:55")] + [at(today, t) for t, _ in prints]
    close = [prev_close, prev_close] + [c for _, c in prints]
    return pd.DataFrame({"Close": close, "Volume": [0] * len(close)},
                        index=pd.DatetimeIndex(idx).tz_convert("UTC"))


def run_of(end, n, price):
    """n consecutive 5-minute bars ending at `end` (HH:MM), all at `price`."""
    t = dt.datetime.strptime(end, "%H:%M")
    return [((t - dt.timedelta(minutes=5 * i)).strftime("%H:%M"), price) for i in reversed(range(n))]


class Summarize(unittest.TestCase):
    def test_basic(self):
        s = pm.summarize(bars(MON, 100.0, TUE, [("08:00", 102.0), ("08:30", 103.0)]), AT_840)
        self.assertEqual(s["prev_close"], 100.0)
        self.assertEqual(s["last"], 103.0)
        self.assertAlmostEqual(s["pct_change"], 3.0)
        self.assertEqual(s["last_print_et"], "8:35 AM ET")
        self.assertAlmostEqual(s["age_min"], 5.0)
        self.assertEqual(s["trade_windows"], 2)

    def test_no_premarket_prints(self):
        s = pm.summarize(bars(MON, 100.0, TUE, []), AT_840)
        self.assertIsNone(s["pct_change"])
        self.assertEqual(s["trade_windows"], 0)

    def test_run_after_open_uses_930(self):
        b = bars(MON, 100.0, TUE, [("09:25", 99.0), ("09:35", 95.0)])
        s = pm.summarize(b, dt.datetime(2026, 10, 20, 10, 15, tzinfo=ET))
        self.assertEqual(s["last"], 99.0)           # the 9:35 regular bar is ignored
        self.assertEqual(s["last_print_et"], "9:30 AM ET")
        self.assertAlmostEqual(s["age_min"], 0.0)   # measured against 9:30

    def test_prior_close_skips_weekend(self):
        fri = dt.date(2026, 10, 16)
        s = pm.summarize(bars(fri, 50.0, MON, [("08:30", 49.0)]),
                         dt.datetime(2026, 10, 19, 8, 40, tzinfo=ET))
        self.assertEqual(s["prev_close"], 50.0)
        self.assertAlmostEqual(s["pct_change"], -2.0)

    def test_official_close_preferred(self):
        # META 2026-10-21: official close $697.52, last regular bar $698.43.
        # Today's daily row (Yahoo can create it early) must be ignored.
        s = pm.summarize(bars(MON, 100.0, TUE, [("08:30", 99.0)]), AT_840, {MON: 98.0, TUE: 50.0})
        self.assertEqual(s["prev_close"], 98.0)
        self.assertAlmostEqual(s["pct_change"], (99 / 98 - 1) * 100)


def stub(table):
    return lambda symbols: {s: table[s] for s in symbols if s in table}


def no_closes(symbols):
    return {}


class MoversAndReactions(unittest.TestCase):
    def setUp(self):
        self.table = {
            "UP": bars(MON, 100.0, TUE, run_of("08:30", 8, 110.0)),       # +10%, 8 windows
            "DN": bars(MON, 100.0, TUE, run_of("08:30", 8, 92.0)),        # -8%
            "THIN": bars(MON, 100.0, TUE, [("08:20", 130.0), ("08:30", 130.0)]),  # +30%, 2 windows
            "STALE": bars(MON, 100.0, TUE, run_of("07:30", 8, 120.0)),    # last print 65 min old
            "FLAT": bars(MON, 100.0, TUE, []),
        }
        self.uni = [{"symbol": s, "name": s.title()} for s in self.table] + [{"symbol": "UP", "name": "dupe"}]

    def test_movers_filters_and_ranks(self):
        m = pm.movers(self.uni, AT_840, download=stub(self.table), closes=no_closes)
        self.assertEqual([r["ticker"] for r in m["gainers"]], ["UP"])
        self.assertEqual([r["ticker"] for r in m["losers"]], ["DN"])
        self.assertEqual(m["gainers"][0]["name"], "Up")
        self.assertEqual(m["universe_size"], 5)
        self.assertEqual(m["as_of_et"], "8:40 AM ET")
        self.assertIsNone(m["unavailable"])

    def test_no_qualifiers_says_why(self):
        m = pm.movers(self.uni, AT_840, download=stub({k: self.table[k] for k in ("THIN", "FLAT")}),
                      closes=no_closes)
        self.assertEqual(m["gainers"], [])
        self.assertIn("8:40 AM ET", m["unavailable"])

    def test_partial_download(self):
        m = pm.movers(self.uni, AT_840, download=stub({"UP": self.table["UP"]}), closes=no_closes)
        self.assertEqual(m["priced"], 1)
        self.assertEqual([r["ticker"] for r in m["gainers"]], ["UP"])

    def test_download_failure_returns_none(self):
        def boom(symbols):
            raise RuntimeError("rate limited")
        self.assertIsNone(pm.movers(self.uni, AT_840, download=boom, closes=no_closes))

    def test_closes_failure_falls_back_to_bars(self):
        def boom(symbols):
            raise RuntimeError("daily bars down")
        r = pm.reactions(["UP"], AT_840, download=stub(self.table), closes=boom)
        self.assertAlmostEqual(r["UP"]["pct"], 10.0)

    def test_reactions(self):
        r = pm.reactions(["UP", "FLAT", "MISSING"], AT_840, download=stub(self.table), closes=no_closes)
        self.assertEqual(set(r), {"UP"})
        self.assertAlmostEqual(r["UP"]["pct"], 10.0)
        self.assertEqual(r["UP"]["as_of_et"], "8:35 AM ET")

    def test_download_maps_dotted_symbols(self):
        cols = pd.MultiIndex.from_tuples([("BRK-B", "Close"), ("BRK-B", "Volume")])
        frame = pd.DataFrame([[500.0, 0]], columns=cols,
                             index=pd.DatetimeIndex([pd.Timestamp("2026-10-20 12:30", tz="UTC")]))
        seen = []
        def fake_download(tickers, **kw):
            seen.append((tickers, kw))
            return frame
        with mock.patch.dict(sys.modules, {"yfinance": types.SimpleNamespace(download=fake_download)}):
            bars_out = pm._download(["BRK.B"])
            closes_out = pm._daily_closes(["BRK.B"])
        self.assertEqual(pm.yahoo_symbol("BRK.B"), "BRK-B")
        self.assertEqual(seen[0][0], ["BRK-B"])
        self.assertTrue(seen[0][1]["prepost"])
        self.assertEqual(seen[1][1]["interval"], "1d")
        self.assertEqual(list(bars_out), ["BRK.B"])
        self.assertEqual(closes_out, {"BRK.B": {dt.date(2026, 10, 20): 500.0}})


if __name__ == "__main__":
    unittest.main()
