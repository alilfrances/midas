import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import midas_hook as mh


def bash_failed(command, stderr):
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"is_error": True, "stderr": stderr, "stdout": ""},
    }


class TestFreshness(unittest.TestCase):
    def test_stale_error_nudges_once(self):
        st = mh.default_state()
        ev = bash_failed("npm test", "unknown option --foo")
        ev2 = bash_failed("npm run build", "unknown option --bar")
        out, st = mh.handle_event("post_tool", ev, st)
        out2, st = mh.handle_event("post_tool", ev2, st)
        self.assertIn("stale API knowledge", out["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(out2)

    def test_thrash_wins_when_same_event_is_stale(self):
        st = mh.default_state()
        ev = bash_failed("npm test", "unknown option --foo")
        _, st = mh.handle_event("post_tool", ev, st)
        st["freshness_fired"] = False
        out, st = mh.handle_event("post_tool", ev, st)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Same command failed twice", ctx)
        self.assertNotIn("stale API", ctx)

    def test_plain_prompt_is_silent(self):
        out, _ = mh.handle_event("user_prompt", {"prompt": "edit this file"}, mh.default_state())
        self.assertIsNone(out)

    def test_freshness_prompt_context_once(self):
        st = mh.default_state()
        data = {"prompt": "upgrade react 17 to 18"}
        out, st = mh.handle_event("user_prompt", data, st)
        out2, st = mh.handle_event("user_prompt", {"prompt": "latest nextjs"}, st)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "UserPromptSubmit")
        self.assertIn("Freshness task", hso["additionalContext"])
        self.assertIsNone(out2)


if __name__ == "__main__":
    unittest.main()
