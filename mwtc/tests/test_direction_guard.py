import unittest

from mwtc.report import generator as g


def data(pct):
    row = {"symbol": "META", "result_line": "META — ..."}
    if pct is not None:
        row["premarket_reaction_pct"] = pct
    return {"earnings": {"overnight_results": [row]}, "movers": {}}


def html(text):
    return f"<html><head><style>p{{color:red}}</style></head><body><p>{text}</p></body></html>"


class DirectionGuard(unittest.TestCase):
    def gaps(self, text, pct):
        return [x for x in g._coverage_gaps(html(text), data(pct))
                if x.startswith("META") and ("direction call" in x or "not gapping" in x)]

    def test_no_data_no_direction_call(self):
        self.assertTrue(self.gaps("META beat estimates. META will gap up at the open.", None))
        self.assertTrue(self.gaps("Both META and TSLA will gap up on the beats.", None))
        self.assertTrue(self.gaps("META is set for a gap-up open.", None))
        self.assertFalse(self.gaps("META beat estimates; pre-market reaction not available — verify live.", None))

    def test_reaction_contradiction(self):
        self.assertTrue(self.gaps("META beat and will gap higher.", -2.5))
        self.assertFalse(self.gaps("META beat but trades -2.5% pre-market; expect a gap down.", -2.5))
        self.assertTrue(self.gaps("META missed and will gap down.", 3.0))
        self.assertFalse(self.gaps("META beat and will gap higher.", 0.4))   # inside ±1%: no call either way

    def test_unrelated_gap_language_is_fine(self):
        self.assertFalse(self.gaps("META beat. Gap-fade risk applies to small caps; AMD will gap up.", None))


if __name__ == "__main__":
    unittest.main()
