import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import midas_hook as mh


def ev(tool, path):
    return {"session_id": "s", "tool_name": tool, "tool_input": {"file_path": path}}


class TestPreToolGate(unittest.TestCase):
    def test_unexplored_edit_denied_once(self):
        with tempfile.NamedTemporaryFile() as f:
            out, st = mh.handle_event("pre_tool", ev("Edit", f.name), mh.default_state())
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertTrue(st["edit_gate_fired"])
            out2, _ = mh.handle_event("pre_tool", ev("Edit", f.name), st)
            self.assertIsNone(out2)

    def test_explored_edit_allowed(self):
        st = mh.default_state()
        st["explored"] = True
        with tempfile.NamedTemporaryFile() as f:
            out, _ = mh.handle_event("pre_tool", ev("Edit", f.name), st)
        self.assertIsNone(out)

    def test_write_new_file_allowed(self):
        out, st = mh.handle_event(
            "pre_tool", ev("Write", "/nonexistent/midas-test-xyz.txt"), mh.default_state())
        self.assertIsNone(out)
        self.assertFalse(st["edit_gate_fired"])

    def test_two_reads_allowed(self):
        st = mh.default_state()
        st["reads"] = 2
        with tempfile.NamedTemporaryFile() as f:
            out, _ = mh.handle_event("pre_tool", ev("Edit", f.name), st)
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
