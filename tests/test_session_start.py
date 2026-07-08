import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import midas_hook as mh


class TestSessionStart(unittest.TestCase):
    def test_injects_protocol(self):
        out, st = mh.handle_event("session_start", {"session_id": "s"}, mh.default_state())
        self.assertIsNotNone(out)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("EXPLORE", ctx)
        self.assertIn("VERIFY", ctx)
        self.assertIn("midas:debug", ctx)
        self.assertIn("override", ctx)

    def test_protocol_under_budget(self):
        # PLAN_V2 Task 7 allows calibrated pushback line; keep protocol <1100 chars.
        self.assertLess(len(mh.PROTOCOL), 1100)


if __name__ == "__main__":
    unittest.main()
