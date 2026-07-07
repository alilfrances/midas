import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import midas_hook as mh


def ev(tool, tool_input=None, resp=None):
    return {"session_id": "s", "tool_name": tool,
            "tool_input": tool_input or {}, "tool_response": resp or {}}


class TestPostTool(unittest.TestCase):
    def test_grep_sets_explored(self):
        out, st = mh.handle_event("post_tool", ev("Grep"), mh.default_state())
        self.assertTrue(st["explored"])
        self.assertIsNone(out)

    def test_second_read_sets_explored(self):
        st = mh.default_state()
        _, st = mh.handle_event("post_tool", ev("Read", {"file_path": "/a"}, {"file": {"content": "x"}}), st)
        self.assertFalse(st["explored"])
        _, st = mh.handle_event("post_tool", ev("Read", {"file_path": "/b"}, {"file": {"content": "x"}}), st)
        self.assertTrue(st["explored"])

    def test_large_read_nudges_once(self):
        st = mh.default_state()
        big = {"file": {"content": "line\n" * 500}}
        out, st = mh.handle_event("post_tool", ev("Read", {"file_path": "/a"}, big), st)
        self.assertIsNotNone(out)
        self.assertIn("Grep", out["hookSpecificOutput"]["additionalContext"])
        out2, st = mh.handle_event("post_tool", ev("Read", {"file_path": "/b"}, big), st)
        self.assertIsNone(out2)

    def test_large_read_with_limit_no_nudge(self):
        big = {"file": {"content": "line\n" * 500}}
        out, _ = mh.handle_event(
            "post_tool", ev("Read", {"file_path": "/a", "limit": 500}, big), mh.default_state())
        self.assertIsNone(out)

    def test_edit_increments_pending_verify(self):
        _, st = mh.handle_event("post_tool", ev("Edit", {"file_path": "/a"}), mh.default_state())
        self.assertEqual(st["edits_since_verify"], 1)

    def test_verify_command_resets_counter(self):
        st = mh.default_state()
        st["edits_since_verify"] = 3
        _, st = mh.handle_event("post_tool", ev("Bash", {"command": "python3 -m unittest discover"}), st)
        self.assertEqual(st["edits_since_verify"], 0)

    def test_thrash_two_identical_failures_nudges_once(self):
        st = mh.default_state()
        fail = {"is_error": True, "stdout": "", "stderr": "boom"}
        out, st = mh.handle_event("post_tool", ev("Bash", {"command": "make x"}, fail), st)
        self.assertIsNone(out)
        out, st = mh.handle_event("post_tool", ev("Bash", {"command": "make x"}, fail), st)
        self.assertIsNotNone(out)
        self.assertIn("midas:debug", out["hookSpecificOutput"]["additionalContext"])
        out, st = mh.handle_event("post_tool", ev("Bash", {"command": "make x"}, fail), st)
        self.assertIsNone(out)

    def test_different_failing_commands_no_thrash_nudge(self):
        st = mh.default_state()
        fail = {"is_error": True}
        _, st = mh.handle_event("post_tool", ev("Bash", {"command": "make x"}, fail), st)
        out, st = mh.handle_event("post_tool", ev("Bash", {"command": "make y"}, fail), st)
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()


class TestVerifyDetection(unittest.TestCase):
    def test_git_checkout_not_verify(self):
        st = mh.default_state()
        st["edits_since_verify"] = 3
        _, st = mh.handle_event("post_tool", ev("Bash", {"command": "git checkout main"}), st)
        self.assertEqual(st["edits_since_verify"], 3)

    def test_cd_tests_not_verify(self):
        st = mh.default_state()
        st["edits_since_verify"] = 3
        _, st = mh.handle_event("post_tool", ev("Bash", {"command": "ls tests/"}), st)
        self.assertEqual(st["edits_since_verify"], 3)

    def test_pytest_is_verify(self):
        st = mh.default_state()
        st["edits_since_verify"] = 3
        _, st = mh.handle_event("post_tool", ev("Bash", {"command": "pytest tests/test_x.py -v"}), st)
        self.assertEqual(st["edits_since_verify"], 0)


class TestReadContentShapes(unittest.TestCase):
    def test_plain_content_str(self):
        big = {"content": "line\n" * 500}
        out, _ = mh.handle_event("post_tool", ev("Read", {"file_path": "/a"}, big), mh.default_state())
        self.assertIsNotNone(out)

    def test_text_block_list(self):
        big = [{"type": "text", "text": "line\n" * 500}]
        out, _ = mh.handle_event("post_tool", ev("Read", {"file_path": "/a"}, big), mh.default_state())
        self.assertIsNotNone(out)


class TestMcpExploration(unittest.TestCase):
    def test_cortex_query_sets_explored(self):
        out, st = mh.handle_event(
            "post_tool", ev("mcp__plugin_cortex_cortex__cortex_query"), mh.default_state())
        self.assertTrue(st["explored"])
        self.assertIsNone(out)

    def test_cortex_read_symbol_sets_explored(self):
        _, st = mh.handle_event(
            "post_tool", ev("mcp__plugin_cortex_cortex__cortex_read_symbol"), mh.default_state())
        self.assertTrue(st["explored"])

    def test_non_retrieval_mcp_not_explored(self):
        _, st = mh.handle_event(
            "post_tool", ev("mcp__claude_ai_Gmail__create_draft"), mh.default_state())
        self.assertFalse(st["explored"])
