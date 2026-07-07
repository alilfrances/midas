import json
import os
import unittest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import midas_hook as mh


class TestState(unittest.TestCase):
    def test_default_state_keys(self):
        st = mh.default_state()
        for key in ("explored", "reads", "edit_gate_fired", "read_nudge_fired",
                    "thrash_nudge_fired", "stop_gate_fired", "edits_since_verify",
                    "last_bash", "bash_fail_streak"):
            self.assertIn(key, st)

    def test_state_path_sanitizes_session_id(self):
        p = mh.state_path("../../etc/passwd")
        self.assertNotIn("..", os.path.basename(p))
        self.assertNotIn("/", os.path.basename(p).replace("midas-", "").replace(".json", ""))

    def test_roundtrip(self):
        sid = "unittest-roundtrip"
        st = mh.default_state()
        st["reads"] = 3
        mh.save_state(sid, st)
        loaded = mh.load_state(sid)
        self.assertEqual(loaded["reads"], 3)
        os.unlink(mh.state_path(sid))

    def test_load_missing_returns_default(self):
        loaded = mh.load_state("unittest-never-saved-xyz")
        self.assertEqual(loaded, mh.default_state())

    def test_load_corrupt_returns_default(self):
        sid = "unittest-corrupt"
        with open(mh.state_path(sid), "w") as f:
            f.write("{not json")
        loaded = mh.load_state(sid)
        self.assertEqual(loaded, mh.default_state())
        os.unlink(mh.state_path(sid))

    def test_handle_event_unknown_is_noop(self):
        out, st = mh.handle_event("bogus", {}, mh.default_state())
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
