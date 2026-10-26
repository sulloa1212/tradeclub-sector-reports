import unittest
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "mwtc.yml"


class Workflow(unittest.TestCase):
    def steps(self):
        return {s["name"]: (i, s) for i, s in enumerate(yaml.safe_load(WF.read_text())["jobs"]["mwtc"]["steps"])}

    def test_trace_upload_never_gates_publishing(self):
        # A failed artifact upload must not skip Commit & push or the Health
        # check-in of a report that is already built (and announced).
        st = self.steps()
        i_up, up = st["Keep this run's trace (artifact, 14 days)"]
        self.assertGreater(i_up, st["Commit & push the rebuilt site (race-safe)"][0])
        self.assertGreater(i_up, st["Check in to MWTC Health"][0])
        self.assertTrue(up.get("continue-on-error"))
        self.assertEqual(up.get("if"), "always()")


if __name__ == "__main__":
    unittest.main()
