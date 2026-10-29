import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mwtc import publish

PACKET = {"report_date": "Monday", "mode": "premarket", "generated_at_et": "x",
          "movers": {"gainers": []}, "premarket_reactions": None, "notes": {},
          "earnings": {"calendar": [], "overnight_results": None, "focus_detail": {"SPY": "uw"}},
          "institutional": {"paid": "Unusual Whales"}, "fed": {"fedwatch": "CME"}}


class Modes(unittest.TestCase):
    def run_publish(self, **kw):
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(publish, "OUT", tmp), \
             mock.patch.object(publish.mwtc_main, "collect_data", return_value=dict(PACKET)), \
             mock.patch.object(publish, "derive_sidecar", return_value={}), \
             mock.patch.object(publish.generator, "generate", return_value="<html>ok</html>"), \
             mock.patch.object(publish, "publish_html", return_value=tmp / "p.html") as pub, \
             mock.patch.object(publish, "notify") as note:
            rc = publish.run("premarket", **kw)
        return rc, tmp, pub, note

    def test_preview_writes_files_and_publishes_nothing(self):
        rc, tmp, pub, note = self.run_publish(preview=True)
        self.assertEqual(rc, 0)
        pub.assert_not_called()
        note.assert_not_called()
        self.assertEqual(sorted(p.suffix for p in tmp.iterdir()), [".html", ".json"])

    def test_trace_keeps_no_paid_feed_data(self):
        _, tmp, pub, _ = self.run_publish()
        pub.assert_called_once()
        saved = json.loads(next(tmp.glob("*.json")).read_text())
        self.assertNotIn("institutional", saved)
        self.assertNotIn("fed", saved)
        self.assertNotIn("focus_detail", saved["earnings"])
        self.assertIn("movers", saved)


if __name__ == "__main__":
    unittest.main()
