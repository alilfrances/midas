import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import midas_hook as mh


def pre_bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


class TestBashRouter(unittest.TestCase):
    def assert_denied_once(self, command, class_name, text):
        st = mh.default_state()
        out, st = mh.handle_event("pre_tool", pre_bash(command), st)
        out2, st = mh.handle_event("pre_tool", pre_bash(command), st)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(text, out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn(class_name, st["router_fired"])
        self.assertIsNone(out2)

    def test_read_class_denies_once(self):
        self.assert_denied_once("cat foo.py", "read", "Use Read tool")

    def test_search_class_denies_once(self):
        self.assert_denied_once("grep -n thing foo.py", "search", "Use Grep tool")

    def test_find_class_denies_once(self):
        self.assert_denied_once("find . -name '*.py'", "find", "Use Glob tool")

    def test_compounds_and_flags_are_exempt(self):
        for command in ("cat f | jq .", "cat -n f", "sh -c 'grep x f'", "cat f && echo ok",
                        "cat f > out", "cat < in", "echo $(cat f)", "cat f; echo ok"):
            out, _ = mh.handle_event("pre_tool", pre_bash(command), mh.default_state())
            self.assertIsNone(out)

    def test_lesson_warn_runs_before_router(self):
        st = mh.default_state()
        lessons = {"v": 1, "lessons": [
            {"kind": "thrash", "cmd": "cat foo.py", "err": "", "fix": "", "n": 2, "ts": 1}
        ]}
        out, st = mh.handle_event("pre_tool", pre_bash("cat foo.py"), st, lessons)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual(st["router_fired"], [])


class TestRouterRelatedPostTool(unittest.TestCase):
    def test_widened_mcp_explore_regex(self):
        for tool in ("mcp__plugin_axon_axon__localize",
                     "mcp__plugin_cortex_cortex__cortex_query"):
            st = mh.default_state()
            _, st = mh.handle_event("post_tool", {"tool_name": tool}, st)
            self.assertTrue(st["explored"], tool)

    def test_read_nudge_threshold_env_override(self):
        st = mh.default_state()
        data = {"tool_name": "Read", "tool_input": {}, "tool_response": {"content": "\n".join(["x"] * 20)}}
        with mock.patch.dict(os.environ, {"MIDAS_READ_NUDGE_LINES": "10"}):
            out, st = mh.handle_event("post_tool", data, st)
        self.assertIsNotNone(out)

    def test_read_nudge_threshold_invalid_falls_back(self):
        st = mh.default_state()
        data = {"tool_name": "Read", "tool_input": {}, "tool_response": {"content": "\n".join(["x"] * 20)}}
        with mock.patch.dict(os.environ, {"MIDAS_READ_NUDGE_LINES": "abc"}):
            out, st = mh.handle_event("post_tool", data, st)
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
