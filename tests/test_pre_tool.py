import os
import sys
import tempfile
import unittest
from unittest import mock

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

    def test_reads_counter_alone_no_longer_unlocks(self):
        # v3: `reads` stays a counter but does not unlock the gate globally
        st = mh.default_state()
        st["reads"] = 2
        with tempfile.NamedTemporaryFile() as f:
            out, _ = mh.handle_event("pre_tool", ev("Edit", f.name), st)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")


def read_ev(path):
    return {"session_id": "s", "tool_name": "Read",
            "tool_input": {"file_path": path},
            "tool_response": {"file": {"content": "x"}}}


def grep_ev(path=None, cwd=None):
    data = {"session_id": "s", "tool_name": "Grep", "tool_input": {}}
    if path:
        data["tool_input"]["path"] = path
    if cwd:
        data["cwd"] = cwd
    return data


class TestPerPathEditGate(unittest.TestCase):
    def test_read_a_then_edit_a_allowed(self):
        st = mh.default_state()
        _, st = mh.handle_event("post_tool", read_ev("/repo/a.py"), st)
        out, _ = mh.handle_event("pre_tool", ev("Edit", "/repo/a.py"), st)
        self.assertIsNone(out)

    def test_read_a_then_edit_b_denied(self):
        st = mh.default_state()
        _, st = mh.handle_event("post_tool", read_ev("/repo/a.py"), st)
        out, _ = mh.handle_event("pre_tool", ev("Edit", "/repo/b.py"), st)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_grep_dir_then_edit_inside_allowed(self):
        st = mh.default_state()
        _, st = mh.handle_event("post_tool", grep_ev(path="/repo/src"), st)
        out, _ = mh.handle_event("pre_tool", ev("Edit", "/repo/src/x.py"), st)
        self.assertIsNone(out)

    def test_grep_dir_prefix_trap_denied(self):
        # /repo/srcx is NOT under /repo/src
        st = mh.default_state()
        _, st = mh.handle_event("post_tool", grep_ev(path="/repo/src"), st)
        out, _ = mh.handle_event("pre_tool", ev("Edit", "/repo/srcx/y.py"), st)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_grep_without_path_counts_cwd(self):
        st = mh.default_state()
        _, st = mh.handle_event("post_tool", grep_ev(cwd="/repo"), st)
        out, _ = mh.handle_event("pre_tool", ev("Edit", "/repo/x.py"), st)
        self.assertIsNone(out)

    def test_mcp_exploration_unlocks_everything(self):
        st = mh.default_state()
        _, st = mh.handle_event(
            "post_tool", {"session_id": "s",
                          "tool_name": "mcp__plugin_cortex_cortex__cortex_query",
                          "tool_input": {}}, st)
        out, _ = mh.handle_event("pre_tool", ev("Edit", "/anywhere/z.py"), st)
        self.assertIsNone(out)

    def test_read_paths_cap_50_evicts_oldest(self):
        st = mh.default_state()
        for i in range(51):
            _, st = mh.handle_event("post_tool", read_ev("/repo/f%d.py" % i), st)
        self.assertEqual(len(st["read_paths"]), 50)
        out, st = mh.handle_event("pre_tool", ev("Edit", "/repo/f0.py"), st)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        out, _ = mh.handle_event("pre_tool", ev("Edit", "/repo/f50.py"), st)
        self.assertIsNone(out)

    def test_symlinked_read_matches_realpath_target(self):
        with tempfile.TemporaryDirectory() as td:
            real = os.path.join(td, "real.py")
            with open(real, "w") as f:
                f.write("x = 1\n")
            link = os.path.join(td, "link.py")
            os.symlink(real, link)
            st = mh.default_state()
            _, st = mh.handle_event("post_tool", read_ev(link), st)
            out, _ = mh.handle_event("pre_tool", ev("Edit", real), st)
        self.assertIsNone(out)

    def test_second_blind_edit_after_gate_fired_allowed(self):
        st = mh.default_state()
        out, st = mh.handle_event("pre_tool", ev("Edit", "/repo/a.py"), st)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        out2, _ = mh.handle_event("pre_tool", ev("Edit", "/repo/b.py"), st)
        self.assertIsNone(out2)


def read_pre_ev(path, **tool_input):
    tool_input["file_path"] = path
    return {"session_id": "s", "tool_name": "Read", "tool_input": tool_input}


class TestPreReadGuard(unittest.TestCase):
    def _make_file(self, td, name, lines):
        path = os.path.join(td, name)
        with open(path, "w") as f:
            f.write("line\n" * lines)
        return path

    def test_unbounded_big_read_denied_once(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._make_file(td, "big.py", 500)
            st = mh.default_state()
            out, st = mh.handle_event("pre_tool", read_pre_ev(path), st)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertIn("400+", out["hookSpecificOutput"]["permissionDecisionReason"])
            self.assertIn("offset/limit", out["hookSpecificOutput"]["permissionDecisionReason"])
            self.assertTrue(st["preread_gate_fired"])
            out2, _ = mh.handle_event("pre_tool", read_pre_ev(path), st)
            self.assertIsNone(out2)

    def test_bounded_read_never_denied(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._make_file(td, "big.py", 500)
            out, st = mh.handle_event(
                "pre_tool", read_pre_ev(path, limit=100), mh.default_state())
            self.assertIsNone(out)
            self.assertFalse(st["preread_gate_fired"])
            out, st = mh.handle_event(
                "pre_tool", read_pre_ev(path, offset=100), mh.default_state())
            self.assertIsNone(out)

    def test_small_file_never_denied(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._make_file(td, "small.py", 100)
            out, st = mh.handle_event("pre_tool", read_pre_ev(path), mh.default_state())
        self.assertIsNone(out)
        self.assertFalse(st["preread_gate_fired"])

    def test_nonexistent_path_never_denied(self):
        out, st = mh.handle_event(
            "pre_tool", read_pre_ev("/nonexistent/midas-xyz.py"), mh.default_state())
        self.assertIsNone(out)
        self.assertFalse(st["preread_gate_fired"])

    def test_binary_file_never_denied(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "blob.bin")
            with open(path, "wb") as f:
                f.write(b"\x00\x01" + b"data\n" * 1000)
            out, st = mh.handle_event("pre_tool", read_pre_ev(path), mh.default_state())
        self.assertIsNone(out)
        self.assertFalse(st["preread_gate_fired"])

    def test_threshold_env_override(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._make_file(td, "mid.py", 20)
            with mock.patch.dict(os.environ, {"MIDAS_READ_NUDGE_LINES": "10"}):
                out, st = mh.handle_event("pre_tool", read_pre_ev(path), mh.default_state())
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("10+", out["hookSpecificOutput"]["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
