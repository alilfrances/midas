import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import midas_hook as mh


class TestStopGate(unittest.TestCase):
    def test_edits_without_verify_blocks_once(self):
        st = mh.default_state()
        st["edits_since_verify"] = 2
        out, st = mh.handle_event("stop", {"session_id": "s"}, st)
        self.assertEqual(out["decision"], "block")
        self.assertIn("2 edit", out["reason"])
        out2, _ = mh.handle_event("stop", {"session_id": "s"}, st)
        self.assertIsNone(out2)

    def test_no_edits_no_block(self):
        out, _ = mh.handle_event("stop", {"session_id": "s"}, mh.default_state())
        self.assertIsNone(out)

    def test_stop_hook_active_never_blocks(self):
        st = mh.default_state()
        st["edits_since_verify"] = 5
        out, _ = mh.handle_event("stop", {"session_id": "s", "stop_hook_active": True}, st)
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
