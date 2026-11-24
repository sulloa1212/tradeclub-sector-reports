import re
import unittest

from mwtc.report import generator

PAGE = """<html><body><div class="header">h</div>
<!--SECTION-NAV-->
<section><div class="sec-title">Mike's Morning Take</div><p>a</p></section>
<section id="earnings-calendar"><div class="sec-title">Earnings Calendar</div><p>b</p></section>
<section><div class="sec-title">Options Market &amp; Whale Activity</div><p>c</p></section>
</body></html>"""


class SectionMenu(unittest.TestCase):
    def test_dropdown_lists_every_section_and_links_match_ids(self):
        html = generator._inject_section_nav(PAGE)
        self.assertNotIn("<!--SECTION-NAV-->", html)
        menu = re.search(r'<nav class="secmenu".*?</nav>', html, re.S).group(0)
        links = re.findall(r'<a href="#([^"]+)"[^>]*>([^<]+)</a>', menu)
        import html as h
        self.assertEqual([h.unescape(l) for _, l in links],
                         ["Mike's Morning Take", "Earnings Calendar", "Options Market & Whale Activity"])
        for sid, _ in links:
            self.assertIn(f'<section id="{sid}"', html)

    def test_is_a_details_dropdown_with_sticky_bar_and_live_label(self):
        html = generator._inject_section_nav(PAGE)
        self.assertIn('<details class="secmenu-dd"', html)
        self.assertIn('<summary', html)
        self.assertIn('class="secmenu-cur"', html)
        self.assertIn("secmenu-fix", html)  # pinned by script: sticky fails in a short header
        self.assertIn("addEventListener('scroll'", html)
        # the menu sits above the sections it watches: wait for the parse
        self.assertIn("DOMContentLoaded", html)
        self.assertNotIn('class="secnav"', html)

    def test_full_labels_not_truncated(self):
        html = generator._inject_section_nav(PAGE)
        self.assertIn(">Options Market &amp; Whale Activity</a>", html)

    def test_no_sections_drops_marker(self):
        html = generator._inject_section_nav("<html><!--SECTION-NAV--><p>x</p></html>")
        self.assertNotIn("SECTION-NAV", html)
        self.assertNotIn("secmenu", html)

    def test_replace_existing_nav_for_published_pages(self):
        published = generator._inject_section_nav(PAGE)
        again = generator.rebuild_section_menu(published)
        self.assertEqual(again.count('<nav class="secmenu"'), 1)
        old = PAGE.replace("<!--SECTION-NAV-->",
                           '<nav class="secnav" aria-label="Jump to section"><a href="#a">A</a></nav>')
        swapped = generator.rebuild_section_menu(old)
        self.assertNotIn('class="secnav"', swapped)
        self.assertEqual(swapped.count('<nav class="secmenu"'), 1)

if __name__ == "__main__":
    unittest.main()
