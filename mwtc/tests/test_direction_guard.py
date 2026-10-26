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



def flagged(fragment, **pcts):
    """Tickers the direction check flags for `fragment`; pcts maps each
    overnight reporter to its pre-market % (None = no pre-market data)."""
    rows = []
    for sym, pct in pcts.items():
        row = {"symbol": sym, "result_line": f"{sym} — ..."}
        if pct is not None:
            row["premarket_reaction_pct"] = pct
        rows.append(row)
    data = {"earnings": {"overnight_results": rows}, "movers": {}}
    out = g._coverage_gaps(f"<html><body>{fragment}</body></html>", data)
    return {x.split(" ", 1)[0] for x in out if "direction call" in x or "not gapping" in x}


class RealReportText(unittest.TestCase):
    """Cases from the published 2026-10-22 report and the review's replays."""

    # --- calls that must be caught (the incident and its near variants)
    def test_incident_sentence_without_a_ticker(self):
        p = ("<p>The one setup — or trap — to watch today: don't get sucked into chasing META and "
             "TSLA at the open. Both will gap up on the earnings beats.</p>")
        self.assertEqual(flagged(p, META=None, TSLA=None), {"META", "TSLA"})
        self.assertEqual(flagged(p, META=-2.38, TSLA=-3.65), {"META", "TSLA"})

    def test_table_row_names_the_ticker_once(self):
        row = ("<table><tr><td>META</td><td>Meta Platforms</td><td>BEAT +27.8%</td>"
               "<td>Biggest EPS beat this cycle. Watch for a gap-up open.</td></tr></table>")
        self.assertEqual(flagged(row, META=None), {"META"})

    def test_after_the_quoted_result_line(self):
        li = ("<ul><li>META — EPS (provider figure, may be adjusted) $7.40 vs est. $5.79: BEAT +27.8% "
              "· pre-market reaction not available — verify live. Expect it to gap up.</li></ul>")
        self.assertEqual(flagged(li, META=None), {"META"})

    def test_wider_phrasings(self):
        for s in ("META should open higher.", "META will open sharply higher.",
                  "META will likely open higher.", "META is indicated higher."):
            self.assertEqual(flagged(f"<p>{s}</p>", META=None), {"META"}, s)

    def test_earnings_table_row_with_timing_words(self):
        # The published 10-22 table: "AMC" is the report timing, not a ticker.
        row = ("<table><tr><td>META</td><td>Wed AMC</td><td>$5.79</td><td>$7.40</td><td>+27.8%</td>"
               "<td>Massive beat; revenue slight miss vs. $64.6B — watch for sell-the-news after "
               "gap-up</td></tr></table>")
        self.assertEqual(flagged(row, META=None), {"META"})

    # --- correct prose that must NOT burn the paid retry
    def test_conditional_is_not_a_prediction(self):
        for s in ("If META gaps up and holds, it confirms strength.",
                  "Watch the opening reaction; if META gaps down and bounces, fade it.",
                  "Fade META unless it gaps up on volume."):
            self.assertEqual(flagged(f"<p>{s}</p>", META=None), set(), s)

    def test_index_gap_next_to_a_ticker_move(self):
        p = "<p>S&amp;P futures +0.3% point to a modest gap up even as META slips 2.4% on soft guidance.</p>"
        self.assertEqual(flagged(p, META=-2.4), set())

    def test_negated_call(self):
        self.assertEqual(flagged("<p>Don&#x27;t assume META will gap up just because the EPS line beat.</p>",
                                 META=-2.4), set())

    def test_list_items_stay_apart(self):
        ul = ("<ul><li>CCI — Crown Castle +11.9% — gap-up on guidance raise</li>"
              "<li>META — Meta Platforms -2.4%</li></ul>")
        self.assertEqual(flagged(ul, META=-2.4), set())

    def test_common_words_are_not_tickers(self):
        self.assertEqual(flagged("<p>Right now, S&amp;P futures point to a gap up.</p>", NOW=None), set())
        self.assertEqual(flagged("<p>A gap up at the open looks likely for the indexes.</p>", A=None), set())
        self.assertEqual(flagged("<p>Don&#x27;t chase the gap up in the indexes.</p>", T=None), set())

    def test_market_wrap_reports_what_happened(self):
        # 4:30 PM wrap: the packet has no pre-market figures, and "gapped down
        # this morning" is a fact, not a prediction — no paid retry.
        data = {"mode": "postmarket", "movers": {},
                "earnings": {"overnight_results": [{"symbol": "META", "result_line": "META — ..."}]}}
        page = "<html><body><p>META gapped down 7% at the open and never recovered.</p></body></html>"
        self.assertEqual([x for x in g._coverage_gaps(page, data) if "direction call" in x], [])
        data["mode"] = "premarket"
        self.assertTrue([x for x in g._coverage_gaps(page, data) if "direction call" in x])

    def test_generate_passes_its_mode_to_the_check(self):
        # A wrap whose packet lacks "mode" must still be treated as a wrap.
        import sys, types
        from unittest import mock
        page = "<html><body><p>META gapped down 7% at the open.</p><!--DISCLAIMER--></body></html>"
        calls = []
        class Msgs:
            def create(self, **kw):
                calls.append(kw)
                return types.SimpleNamespace(stop_reason="end_turn", usage=None,
                                             content=[types.SimpleNamespace(type="text", text=page)])
        fake = types.SimpleNamespace(Anthropic=lambda **kw: types.SimpleNamespace(messages=Msgs()))
        data = {"movers": {}, "earnings": {"overnight_results": [{"symbol": "META"}]}}
        with mock.patch.dict(sys.modules, {"anthropic": fake}), \
             mock.patch.object(g.config, "ANTHROPIC_API_KEY", "test-key"):
            g.generate(data, "Monday", mode="postmarket")
        self.assertEqual(len(calls), 1)   # no coverage retry

    def test_level_statement_is_not_an_open_call(self):
        self.assertEqual(flagged("<p>If MSFT loses $500, it will drop toward $480.</p>", MSFT=2.1), set())

    def test_quoted_lines_with_consistent_reads(self):
        ul = ("<ul><li>META — EPS (provider figure, may be adjusted) $7.40 vs est. $5.79: BEAT +27.8% "
              "· pre-market -2.4% at 8:40 AM ET. A beat trading down — expect a soft open.</li>"
              "<li>TSLA — EPS (provider figure, may be adjusted) $0.50 vs est. $0.45: BEAT +11.1% "
              "· pre-market +3.1% at 8:40 AM ET. Strong reaction; watch for a gap up continuation.</li></ul>")
        self.assertEqual(flagged(ul, META=-2.4, TSLA=3.1), set())


if __name__ == "__main__":
    unittest.main()
