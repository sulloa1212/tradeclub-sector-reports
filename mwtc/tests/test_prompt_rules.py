import unittest

from mwtc.report import prompt


class PromptRules(unittest.TestCase):
    def test_no_direction_rule_covers_every_section(self):
        # The 2026-10-22 "Both will gap up" sentence was in Mike's Morning Take,
        # not the Executive Summary: the rule belongs to the data protocol.
        system, user = prompt.build_messages("premarket", "{}", "<html></html>", "Monday")
        self.assertIn("NO DIRECTION WITHOUT DATA", system)
        self.assertIn("Mike's Morning Take", system[system.index("NO DIRECTION WITHOUT DATA"):][:400])

    def test_avgo_rule_follows_the_reaction(self):
        system, user = prompt.build_messages("premarket", "{}", "<html></html>", "Monday")
        self.assertIn("report the result AND that move together — a beat trading DOWN", system + user)

    def test_section_rules(self):
        s, u = prompt.build_messages("premarket", "{}", "<html></html>", "Monday")
        self.assertIn("never substitute yesterday", s + u)
        self.assertIn("timing_unconfirmed", s + u)
        s, u = prompt.build_messages("postmarket", "{}", "<html></html>", "Monday")
        self.assertNotIn("day-session vs after-hours", s + u)


if __name__ == "__main__":
    unittest.main()
