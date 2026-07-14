import difflib
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import midas_hook as mh


def fail_ev(command, error="Exit code 2\nboom", is_interrupt=False):
    # Real live-CC PostToolUseFailure payload: top-level preformatted "error"
    # string starting "Exit code N", "is_interrupt" flag, NO tool_response.
    return {
        "hook_event_name": "PostToolUseFailure",
        "session_id": "s",
        "cwd": "/repo",
        "transcript_path": "/tmp/t.jsonl",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": "toolu_01",
        "error": error,
        "is_interrupt": is_interrupt,
    }


def ok_ev(command):
    return {"session_id": "s", "cwd": "/repo", "tool_name": "Bash",
            "tool_input": {"command": command}, "tool_response": {}}


class TestPostToolFailureThrash(unittest.TestCase):
    def test_same_command_twice_nudges_once_with_failure_event_name(self):
        st = mh.default_state()
        lessons = {"v": 1, "lessons": []}
        out, st = mh.handle_event("post_tool_failure", fail_ev("make x"), st, lessons)
        self.assertIsNone(out)
        self.assertEqual(st["bash_fail_streak"], 1)
        out, st = mh.handle_event("post_tool_failure", fail_ev("make x"), st, lessons)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PostToolUseFailure")
        self.assertIn("midas:debug", hso["additionalContext"])
        self.assertEqual(lessons["lessons"][0]["kind"], "thrash")
        self.assertEqual(lessons["lessons"][0]["cmd"], "make x")
        self.assertEqual(lessons["lessons"][0]["err"], "Exit code 2\nboom")
        self.assertEqual(st["pending_fail_cmd"], "make x")
        out, st = mh.handle_event("post_tool_failure", fail_ev("make x"), st, lessons)
        self.assertIsNone(out)

    def test_different_first_token_failures_no_nudge(self):
        st = mh.default_state()
        lessons = {"v": 1, "lessons": []}
        _, st = mh.handle_event("post_tool_failure", fail_ev("pytest a"), st, lessons)
        out, st = mh.handle_event("post_tool_failure", fail_ev("mypy a"), st, lessons)
        self.assertIsNone(out)
        self.assertEqual(st["bash_fail_streak"], 1)
        self.assertEqual(lessons["lessons"], [])

    def test_interrupt_never_feeds_streak_or_lessons(self):
        st = mh.default_state()
        lessons = {"v": 1, "lessons": []}
        ev = fail_ev("sleep 100", error="Exit code 130", is_interrupt=True)
        out, st = mh.handle_event("post_tool_failure", ev, st, lessons)
        out2, st = mh.handle_event("post_tool_failure", ev, st, lessons)
        self.assertIsNone(out)
        self.assertIsNone(out2)
        self.assertEqual(st["bash_fail_streak"], 0)
        self.assertEqual(st["last_fail_cmd"], "")
        self.assertEqual(lessons["lessons"], [])

    def test_non_bash_tool_is_noop(self):
        st = mh.default_state()
        ev = fail_ev("ignored")
        ev["tool_name"] = "Edit"
        out, st = mh.handle_event("post_tool_failure", ev, st, {"v": 1, "lessons": []})
        self.assertIsNone(out)
        self.assertEqual(st["bash_fail_streak"], 0)

    def test_success_resets_streak(self):
        st = mh.default_state()
        _, st = mh.handle_event("post_tool_failure", fail_ev("make x"), st)
        _, st = mh.handle_event("post_tool", ok_ev("ls"), st)
        self.assertEqual(st["bash_fail_streak"], 0)
        self.assertEqual(st["last_fail_cmd"], "")
        out, st = mh.handle_event("post_tool_failure", fail_ev("make x"), st)
        self.assertIsNone(out)
        self.assertEqual(st["bash_fail_streak"], 1)


class TestPostToolFailureFreshness(unittest.TestCase):
    def test_stale_error_nudges_once(self):
        st = mh.default_state()
        ev = fail_ev("python x.py", error="Exit code 1\nModuleNotFoundError: No module named 'foo'")
        ev2 = fail_ev("npm run build", error="Exit code 1\nunknown option --bar")
        out, st = mh.handle_event("post_tool_failure", ev, st)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PostToolUseFailure")
        self.assertIn("stale API knowledge", hso["additionalContext"])
        out2, st = mh.handle_event("post_tool_failure", ev2, st)
        self.assertIsNone(out2)

    def test_thrash_wins_when_same_event_is_stale(self):
        st = mh.default_state()
        ev = fail_ev("npm test", error="Exit code 1\nunknown option --foo")
        _, st = mh.handle_event("post_tool_failure", ev, st)
        st["freshness_fired"] = False
        out, st = mh.handle_event("post_tool_failure", ev, st, {"v": 1, "lessons": []})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Same command failed twice", ctx)
        self.assertNotIn("stale API", ctx)


class TestPostToolFailureFixCapture(unittest.TestCase):
    # v4 rule: a fix is captured ONLY when the failing command itself —
    # possibly arg-tweaked, per _same_command — reran and succeeded.
    # First-token-only and verify-command matches were false-fix bugs.

    def _thrash(self, cmd):
        st = mh.default_state()
        lessons = {"v": 1, "lessons": []}
        _, st = mh.handle_event("post_tool_failure", fail_ev(cmd), st, lessons)
        _, st = mh.handle_event("post_tool_failure", fail_ev(cmd), st, lessons)
        return st, lessons

    def test_tweaked_rerun_success_captures_fix(self):
        # fixture calibration: ratio must clear the 0.8 _same_command bar
        ratio = difflib.SequenceMatcher(None, "pytest -q", "pytest -q -v").ratio()
        self.assertGreaterEqual(ratio, 0.8)
        st, lessons = self._thrash("pytest -q")
        self.assertEqual(st["pending_fail_cmd"], "pytest -q")
        _, st = mh.handle_event("post_tool", ok_ev("pytest -q -v"), st, lessons)
        self.assertEqual(lessons["lessons"][0]["fix"], "pytest -q -v")
        self.assertEqual(st["pending_fail_cmd"], "")

    def test_same_first_token_below_ratio_never_captures(self):
        # live false-fix repro: pytest --version success is not proof of a fix
        ratio = difflib.SequenceMatcher(None, "pytest -q", "pytest --version").ratio()
        self.assertLess(ratio, 0.8)
        st, lessons = self._thrash("pytest -q")
        _, st = mh.handle_event("post_tool", ok_ev("pytest --version"), st, lessons)
        self.assertEqual(lessons["lessons"][0]["fix"], "")
        self.assertEqual(st["pending_fail_cmd"], "pytest -q")

    def test_unrelated_verify_command_never_captures(self):
        # live false-fix repro: make lint matches VERIFY_RE but not the failure
        st, lessons = self._thrash("python app.py")
        st["edits_since_verify"] = 3
        _, st = mh.handle_event("post_tool", ok_ev("make lint"), st, lessons)
        self.assertEqual(lessons["lessons"][0]["fix"], "")
        self.assertEqual(st["pending_fail_cmd"], "python app.py")
        # verify-reset stays untouched: make lint still counts as a verify
        self.assertEqual(st["edits_since_verify"], 0)

    def test_identical_rerun_clears_pending_without_self_fix(self):
        st, lessons = self._thrash("pytest -q")
        lessons["lessons"][0]["ts"] = 123
        _, st = mh.handle_event("post_tool", ok_ev("pytest -q"), st, lessons)
        self.assertEqual(st["pending_fail_cmd"], "")
        self.assertEqual(lessons["lessons"][0]["fix"], "")
        self.assertEqual(lessons["lessons"][0]["n"], 1)
        self.assertEqual(lessons["lessons"][0]["ts"], 123)

    def test_pending_survives_unrelated_success(self):
        st, lessons = self._thrash("pytest -q")
        _, st = mh.handle_event("post_tool", ok_ev("ls"), st, lessons)
        self.assertEqual(st["pending_fail_cmd"], "pytest -q")
        self.assertEqual(lessons["lessons"][0]["fix"], "")

    def test_confirmation_leaves_err_untouched(self):
        st, lessons = self._thrash("pytest -q")
        _, st = mh.handle_event("post_tool", ok_ev("pytest -q -v"), st, lessons)
        self.assertEqual(lessons["lessons"][0]["err"], "Exit code 2\nboom")
        self.assertEqual(lessons["lessons"][0]["fix"], "pytest -q -v")


class TestFuzzyThrashMatching(unittest.TestCase):
    def test_arg_tweaked_retry_trips_thrash(self):
        st = mh.default_state()
        lessons = {"v": 1, "lessons": []}
        _, st = mh.handle_event("post_tool_failure", fail_ev("pytest x"), st, lessons)
        out, st = mh.handle_event("post_tool_failure", fail_ev("pytest x -v"), st, lessons)
        self.assertIsNotNone(out)
        self.assertIn("midas:debug", out["hookSpecificOutput"]["additionalContext"])
        # lesson records the CURRENT (latest) command form
        self.assertEqual(lessons["lessons"][0]["cmd"], "pytest x -v")

    def test_different_first_token_never_fuzzy_matches(self):
        # ratio("pytest a", "mypy a") is irrelevant: first tokens differ
        self.assertFalse(mh._same_command("pytest a", "mypy a"))
        st = mh.default_state()
        _, st = mh.handle_event("post_tool_failure", fail_ev("pytest a"), st)
        out, st = mh.handle_event("post_tool_failure", fail_ev("mypy a"), st)
        self.assertIsNone(out)
        self.assertEqual(st["bash_fail_streak"], 1)

    def test_similar_paths_above_threshold_match(self):
        # ratio == 0.955, above the 0.8 threshold
        a, b = "pytest tests/test_a.py", "pytest tests/test_b.py"
        self.assertTrue(mh._same_command(a, b))
        st = mh.default_state()
        _, st = mh.handle_event("post_tool_failure", fail_ev(a), st)
        out, st = mh.handle_event("post_tool_failure", fail_ev(b), st)
        self.assertIsNotNone(out)

    def test_same_first_token_below_threshold_no_match(self):
        # ratio == 0.222, below the 0.8 threshold
        a, b = "pytest tests/unit/deep/test_alpha_module.py::TestAlpha", "pytest -q"
        self.assertFalse(mh._same_command(a, b))

    def test_exact_match_after_normalize(self):
        self.assertTrue(mh._same_command("make x", " make   x "))

    def test_empty_command_never_matches(self):
        self.assertFalse(mh._same_command("", ""))
        self.assertFalse(mh._same_command("make x", ""))
        self.assertFalse(mh._same_command("", "make x"))


if __name__ == "__main__":
    unittest.main()
