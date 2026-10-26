import unittest

from mwtc.sources import macro, earnings


META = {"symbol": "META", "date": "2026-10-21", "hour": "amc",
        "epsActual": 7.40, "epsEstimate": 5.79,
        "revenueActual": 64_601_000_000, "revenueEstimate": 63_100_000_000}


class Figures(unittest.TestCase):
    def enrich(self, r):
        return macro._enrich_earnings(r, "2026-10-22", "2026-10-21")

    def test_revenue_surprise_sign_comes_from_the_numbers(self):
        r = self.enrich(dict(META))
        self.assertEqual(r["revenue_beat_miss"], "beat")
        self.assertAlmostEqual(r["revenue_surprise_pct"], 2.4, places=1)
        swapped = dict(META, revenueActual=63_100_000_000, revenueEstimate=64_601_000_000)
        self.assertEqual(self.enrich(swapped)["revenue_beat_miss"], "miss")

    def test_result_line_meta(self):
        r = self.enrich(dict(META))
        r.update(premarket_reaction_pct=-2.51, premarket_as_of_et="8:35 AM ET")
        self.assertEqual(
            earnings.result_line(r),
            "META — EPS (provider figure, may be adjusted) $7.40 vs est. $5.79: BEAT +27.8%"
            " · revenue $64.6B vs est. $63.1B: BEAT +2.4%"
            " · pre-market -2.5% at 8:35 AM ET")

    def test_result_line_without_reaction_says_so(self):
        r = self.enrich(dict(META))
        self.assertTrue(earnings.result_line(r).endswith(
            "pre-market reaction not available — verify live"))
        self.assertNotIn("pre-market", earnings.result_line(r, include_reaction=False))

    def test_result_line_only_for_reported(self):
        r = self.enrich(dict(META, epsActual=None, revenueActual=None))
        self.assertIsNone(earnings.result_line(r))

    def test_revenue_mismatch_across_sources_is_flagged(self):
        p = macro._enrich_earnings(dict(META, date="2026-10-22", hour="bmo"), "2026-10-22", "2026-10-21")
        fmp = [{"symbol": "META", "date": "2026-10-22", "eps_actual": 7.40,
                "revenue_actual": 63_100_000_000, "reported": True}]
        out = earnings.reconcile([p], fmp)[0]
        self.assertEqual(out["confidence"], "medium")
        self.assertIn("revenue actual differs", out["verify_note"])


if __name__ == "__main__":
    unittest.main()
