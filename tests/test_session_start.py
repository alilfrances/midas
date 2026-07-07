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

    def test_protocol_under_budget(self):
        # ~4 chars/token heuristic; budget 200 tokens => 800 chars
        self.assertLess(len(mh.PROTOCOL), 800)


if __name__ == "__main__":
    unittest.main()
