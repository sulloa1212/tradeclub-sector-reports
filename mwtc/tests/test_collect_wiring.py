import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mwtc import main
from mwtc.sources import fmp


def fake_packets():
    macro_p = {"earnings_calendar": [
        {"symbol": "META", "date": "2026-10-21", "hour": "amc", "reported": True,
         "epsActual": 7.40, "epsEstimate": 5.79, "revenueActual": 64.6e9, "revenueEstimate": 63.1e9,
         "eps_surprise_pct": 27.8, "beat_miss": "beat", "revenue_surprise_pct": 2.4,
         "revenue_beat_miss": "beat", "is_today": False, "is_overnight_result": True,
         "timing_unconfirmed": False},
        {"symbol": "CLF", "date": "2026-10-21", "hour": "bmo", "reported": True,
         "epsActual": 0.10, "epsEstimate": 0.05, "eps_surprise_pct": 100.0, "beat_miss": "beat",
         "is_today": False, "is_overnight_result": False, "timing_unconfirmed": False}],
        "etf_spots": {}}
    fmp_p = {"available": True, "gainers": [{"ticker": "OLD", "pct_change": 50}], "losers": [],
             "earnings_calendar": [
                 {"symbol": "META", "date": "2026-10-21", "eps_actual": 7.40,
                  "revenue_actual": 64.6e9, "reported": True},
                 {"symbol": "CLF", "date": "2026-10-21", "eps_actual": 0.10, "reported": True}],
             "analyst_actions": [], "insider": [], "breadth": None, "economic_calendar": None}
    return macro_p, fmp_p


class Wiring(unittest.TestCase):
    def run_collect(self, mode, movers_ret, react_ret):
        macro_p, fmp_p = fake_packets()
        with mock.patch.object(main.macro, "collect", return_value=macro_p), \
             mock.patch.object(main.macro, "sector_breadth", return_value=None), \
             mock.patch.object(main.unusual_whales, "collect", return_value={"available": True}), \
             mock.patch.object(main.unusual_whales, "earnings", return_value=None), \
             mock.patch.object(main.fmp, "collect", return_value=fmp_p), \
             mock.patch.object(main.fmp, "index_universe", return_value=[{"symbol": "NEW", "name": "New"}]), \
             mock.patch.object(main.sentiment, "collect", return_value={}), \
             mock.patch.object(main.fedwatch, "probabilities", return_value=None), \
             mock.patch.object(main.kalshi, "fed_odds", return_value=None), \
             mock.patch.object(main.premarket, "movers", return_value=movers_ret), \
             mock.patch.object(main.premarket, "reactions", return_value=react_ret):
            return main.collect_data(mode=mode)

    def rows(self, d):
        return {r["symbol"]: r for r in d["earnings"]["calendar"]}

    def test_premarket_uses_real_movers_and_reactions(self):
        mv = {"gainers": [{"ticker": "NEW", "pct_change": 3.1}], "losers": [],
              "as_of_et": "8:40 AM ET", "unavailable": None}
        d = self.run_collect("premarket", mv, {"META": {"pct": -2.5, "as_of_et": "8:35 AM ET"}})
        self.assertEqual(d["movers"]["gainers"][0]["ticker"], "NEW")
        o = d["earnings"]["overnight_results"][0]
        self.assertEqual(o["premarket_reaction_pct"], -2.5)
        self.assertIn("pre-market -2.5% at 8:35 AM ET", o["result_line"])
        # yesterday's before-open print already moved yesterday: no reaction part
        self.assertNotIn("pre-market", self.rows(d)["CLF"]["result_line"])

    def test_premarket_movers_unavailable_is_stated_not_replaced(self):
        d = self.run_collect("premarket", None, {})
        self.assertIsNone(d["movers"]["gainers"])
        self.assertTrue(d["movers"]["unavailable"])
        self.assertIn("premarket_movers", d["notes"]["not_wired"])
        self.assertIn("reaction not available", d["earnings"]["overnight_results"][0]["result_line"])

    def test_postmarket_keeps_fmp_session_movers(self):
        d = self.run_collect("postmarket", None, {})
        self.assertEqual(d["movers"]["gainers"][0]["ticker"], "OLD")
        self.assertNotIn("pre-market", d["earnings"]["overnight_results"][0]["result_line"])


class FailureIsolation(unittest.TestCase):
    def test_finnhub_error_body_does_not_kill_the_report(self):
        # A 200 reply without 'earningsCalendar' comes back as the raw dict.
        macro_p, fmp_p = fake_packets()
        macro_p["earnings_calendar"] = {"error": "You don't have access to this resource."}
        with mock.patch.object(main.macro, "collect", return_value=macro_p), \
             mock.patch.object(main.macro, "sector_breadth", return_value=None), \
             mock.patch.object(main.unusual_whales, "collect", return_value={"available": True}), \
             mock.patch.object(main.unusual_whales, "earnings", return_value=None), \
             mock.patch.object(main.fmp, "collect", return_value=fmp_p), \
             mock.patch.object(main.fmp, "index_universe", return_value=[]), \
             mock.patch.object(main.sentiment, "collect", return_value={}), \
             mock.patch.object(main.fedwatch, "probabilities", return_value=None), \
             mock.patch.object(main.kalshi, "fed_odds", return_value=None), \
             mock.patch.object(main.premarket, "movers", return_value=None), \
             mock.patch.object(main.premarket, "reactions", return_value={}):
            d = main.collect_data(mode="premarket")
        self.assertTrue(d["movers"]["unavailable"])

    def test_premarket_step_exception_falls_back(self):
        with mock.patch.object(main.premarket, "reactions", side_effect=RuntimeError("boom")):
            d = self.run_collect_raw("premarket")
        self.assertTrue(d["movers"]["unavailable"])
        self.assertIn("reaction not available", d["earnings"]["overnight_results"][0]["result_line"])

    def run_collect_raw(self, mode):
        macro_p, fmp_p = fake_packets()
        with mock.patch.object(main.macro, "collect", return_value=macro_p), \
             mock.patch.object(main.macro, "sector_breadth", return_value=None), \
             mock.patch.object(main.unusual_whales, "collect", return_value={"available": True}), \
             mock.patch.object(main.unusual_whales, "earnings", return_value=None), \
             mock.patch.object(main.fmp, "collect", return_value=fmp_p), \
             mock.patch.object(main.fmp, "index_universe", return_value=[]), \
             mock.patch.object(main.sentiment, "collect", return_value={}), \
             mock.patch.object(main.fedwatch, "probabilities", return_value=None), \
             mock.patch.object(main.kalshi, "fed_odds", return_value=None), \
             mock.patch.object(main.premarket, "movers", return_value=None):
            return main.collect_data(mode=mode)


class Universe(unittest.TestCase):
    def test_undecodable_file_is_not_fatal(self):
        f = Path(tempfile.mkdtemp()) / "universe.txt"
        f.write_bytes(b"\xff\xfe\x00garbage")
        with mock.patch.object(fmp, "UNIVERSE_FILE", f), \
             mock.patch.object(fmp, "_constituents", return_value=None):
            self.assertEqual(fmp.index_universe(), [])

    def test_universe_dedupes(self):
        with mock.patch.object(fmp, "_constituents", side_effect=[
                [{"symbol": "AAPL", "name": "Apple"}, {"symbol": "BRK.B", "name": "Berkshire"}],
                [{"symbol": "AAPL", "name": "Apple Inc"}, {"symbol": "GOOG", "name": "Alphabet"}]]):
            u = fmp.index_universe()
        self.assertEqual([r["symbol"] for r in u], ["AAPL", "BRK.B", "GOOG"])

    def test_missing_list_adds_the_committed_file(self):
        f = Path(tempfile.mkdtemp()) / "universe.txt"
        fmp.write_universe([{"symbol": "ARM", "name": "Arm Holdings"},
                            {"symbol": "AAPL", "name": "Apple"}], f)
        with mock.patch.object(fmp, "UNIVERSE_FILE", f), \
             mock.patch.object(fmp, "_constituents", side_effect=[[{"symbol": "AAPL", "name": "Apple"}], None]):
            u = fmp.index_universe()
        self.assertEqual([r["symbol"] for r in u], ["AAPL", "ARM"])


if __name__ == "__main__":
    unittest.main()
