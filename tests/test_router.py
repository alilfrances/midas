import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import midas_hook as mh


def pre_bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def codex_pre_bash(command):
    data = pre_bash(command)
    data["midas_runtime"] = "codex"
    return data


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

    def test_compounds_are_exempt(self):
        for command in ("cat f | jq .", "sh -c 'grep x f'", "cat f && echo ok",
                        "cat f > out", "cat < in", "echo $(cat f)", "cat f; echo ok"):
            out, _ = mh.handle_event("pre_tool", pre_bash(command), mh.default_state())
            self.assertIsNone(out)

    def test_flag_first_reads_denied(self):
        self.assert_denied_once("cat -n foo.py", "read", "Use Read tool")

    def test_head_with_count_denied(self):
        self.assert_denied_once("head -20 foo.py", "read", "Use Read tool")

    def test_tail_with_count_denied(self):
        self.assert_denied_once("tail -n 5 log.txt", "read", "Use Read tool")

    def test_tail_follow_exempt(self):
        for command in ("tail -f log.txt", "tail -F log.txt"):
            out, st = mh.handle_event("pre_tool", pre_bash(command), mh.default_state())
            self.assertIsNone(out)
            self.assertEqual(st["router_fired"], [])

    def test_find_type_only_denied(self):
        self.assert_denied_once("find src -type f", "find", "Use Glob tool")

    def test_bare_find_exempt(self):
        out, st = mh.handle_event("pre_tool", pre_bash("find ."), mh.default_state())
        self.assertIsNone(out)
        self.assertEqual(st["router_fired"], [])

    def test_lesson_warn_runs_before_router(self):
        st = mh.default_state()
        lessons = {"v": 1, "lessons": [
            {"kind": "thrash", "cmd": "cat foo.py", "err": "", "fix": "", "n": 2, "ts": 1}
        ]}
        out, st = mh.handle_event("pre_tool", pre_bash("cat foo.py"), st, lessons)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual(st["router_fired"], [])


class TestCodexBashRouter(unittest.TestCase):
    def test_rg_is_allowed_in_codex(self):
        out, st = mh.handle_event("pre_tool", codex_pre_bash("rg --files -g '*.py'"), mh.default_state())
        self.assertIsNone(out)
        self.assertEqual(st["router_fired"], [])

    def test_grep_uses_codex_message(self):
        out, st = mh.handle_event("pre_tool", codex_pre_bash("grep -n thing foo.py"), mh.default_state())
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("Use rg -n -C 3", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn("search", st["router_fired"])

    def test_find_uses_codex_message(self):
        out, st = mh.handle_event("pre_tool", codex_pre_bash("find . -name '*.py'"), mh.default_state())
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("Use rg --files -g", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn("find", st["router_fired"])

    def test_full_file_read_uses_codex_message(self):
        out, st = mh.handle_event("pre_tool", codex_pre_bash("cat foo.py"), mh.default_state())
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("bounded read", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn("read", st["router_fired"])

    def test_codex_flag_first_read_denied(self):
        out, st = mh.handle_event("pre_tool", codex_pre_bash("cat -n foo.py"), mh.default_state())
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("read", st["router_fired"])

    def test_codex_tail_follow_exempt(self):
        out, st = mh.handle_event("pre_tool", codex_pre_bash("tail -f log.txt"), mh.default_state())
        self.assertIsNone(out)
        self.assertEqual(st["router_fired"], [])

    def test_codex_find_type_only_denied(self):
        out, st = mh.handle_event("pre_tool", codex_pre_bash("find src -type f"), mh.default_state())
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("find", st["router_fired"])

    def test_codex_bare_find_exempt(self):
        out, st = mh.handle_event("pre_tool", codex_pre_bash("find ."), mh.default_state())
        self.assertIsNone(out)
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

    def test_codex_read_nudge_uses_rg_message(self):
        st = mh.default_state()
        data = {
            "midas_runtime": "codex",
            "tool_name": "Read",
            "tool_input": {},
            "tool_response": {"content": "\n".join(["x"] * 20)},
        }
        with mock.patch.dict(os.environ, {"MIDAS_READ_NUDGE_LINES": "10"}):
            out, st = mh.handle_event("post_tool", data, st)
        self.assertIsNotNone(out)
        self.assertIn("rg -n", out["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()


class TestRouterCoverageMatrix(unittest.TestCase):
    # Locked-in coverage rule: deny ONLY what Read/Grep/Glob can actually
    # replace. Metadata/action finds, tail -f follows, and compounds must
    # stay allowed — pattern edits that change any row here need a plan.

    DENY = (
        # read class
        "cat foo.py", "cat -n foo.py", "head -20 foo.py",
        "tail -n 50 log", "less +100 foo.py", "more foo.py",
        # search class
        "grep -rn pat src", "egrep pat f", "fgrep pat f",
        # find class: Glob-replaceable predicates, with and without a path arg
        "find . -name '*.py'", "find src -iname '*.md'", "find . -path '*/x/*'",
        "find . -ipath '*x*'", "find . -regex '.*py'", "find src -type f",
        "find -name '*.py'", "find -type f",
    )
    ALLOW = (
        # follows have no Read equivalent
        "tail -f app.log", "tail -F app.log",
        # metadata predicates have no Glob equivalent
        "find . -mtime -7", "find . -size +1M", "find . -empty",
        "find . -newer ref.txt", "find . -perm 644", "find / -user root",
        "find . -maxdepth 2",
        # bare exploratory find
        "find .",
        # compounds always exempt
        "cat f | jq .", "grep x f && echo y",
    )

    def _deny(self, command, runtime):
        st = mh.default_state()
        return mh._router_deny(command, st, runtime) is not None

    def test_matrix_both_runtimes(self):
        for runtime in ("claude", "codex"):
            for command in self.DENY:
                self.assertTrue(self._deny(command, runtime),
                                "%s should deny: %r" % (runtime, command))
            for command in self.ALLOW:
                self.assertFalse(self._deny(command, runtime),
                                 "%s should allow: %r" % (runtime, command))

    def test_rg_split_by_runtime(self):
        # rg is the recommended tool on Codex, a Grep-replaceable one on Claude
        self.assertTrue(self._deny("rg pat", "claude"))
        self.assertFalse(self._deny("rg pat", "codex"))
