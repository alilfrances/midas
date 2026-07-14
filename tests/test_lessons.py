import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import midas_hook as mh

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestLessonsStore(unittest.TestCase):
    def test_lessons_path_prefers_midas_config_dir(self):
        with tempfile.TemporaryDirectory() as midas_cfg, tempfile.TemporaryDirectory() as claude_cfg:
            with mock.patch.dict(
                os.environ,
                {"MIDAS_CONFIG_DIR": midas_cfg, "CLAUDE_CONFIG_DIR": claude_cfg},
                clear=True,
            ):
                path = mh.lessons_path("/repo")
        self.assertTrue(path.startswith(os.path.join(midas_cfg, "midas-data")))
        self.assertFalse(path.startswith(claude_cfg))

    def test_lessons_path_uses_config_dir_ignores_plugin_data(self):
        # $CLAUDE_PLUGIN_DATA must be ignored: it is another plugin's dir during
        # a bare Bash `midas-lesson` call, so keying off it would split the store
        # and pollute siblings. $CLAUDE_CONFIG_DIR/midas-data is used instead.
        with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as other:
            with mock.patch.dict(
                os.environ,
                {"CLAUDE_CONFIG_DIR": cfg, "CLAUDE_PLUGIN_DATA": other},
                clear=True,
            ):
                path = mh.lessons_path("/repo")
        self.assertTrue(path.startswith(os.path.join(cfg, "midas-data")))
        self.assertFalse(path.startswith(other))
        self.assertTrue(os.path.basename(path).startswith("lessons-"))
        self.assertTrue(path.endswith(".json"))

    def test_lessons_path_missing_cwd_is_none(self):
        self.assertIsNone(mh.lessons_path(""))
        self.assertIsNone(mh.lessons_path(None))

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            lessons = {"v": 1, "lessons": []}
            mh.record_lesson(lessons, "thrash", "pytest")
            saved = mh.save_lessons("/repo", lessons, base_dir=td)
            loaded = mh.load_lessons("/repo", base_dir=td)
        self.assertTrue(saved)
        self.assertEqual(loaded["lessons"][0]["cmd"], "pytest")

    def test_save_lessons_failure_returns_false(self):
        with tempfile.NamedTemporaryFile() as f:
            lessons = {"v": 1, "lessons": []}
            mh.record_lesson(lessons, "note", "cannot save")
            saved = mh.save_lessons("/repo", lessons, base_dir=f.name)
        self.assertFalse(saved)

    def test_corrupt_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            path = mh.lessons_path("/repo", base_dir=td)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("{bad")
            loaded = mh.load_lessons("/repo", base_dir=td)
        self.assertEqual(loaded, {"v": 1, "lessons": []})

    def test_cap_40_evicts_oldest(self):
        with tempfile.TemporaryDirectory() as td:
            lessons = {"v": 1, "lessons": []}
            for i in range(45):
                mh.record_lesson(lessons, "thrash", "cmd %s" % i)
                lessons["lessons"][-1]["ts"] = i
            mh.save_lessons("/repo", lessons, base_dir=td)
            loaded = mh.load_lessons("/repo", base_dir=td)
        cmds = [item["cmd"] for item in loaded["lessons"]]
        self.assertEqual(len(cmds), 40)
        self.assertNotIn("cmd 0", cmds)
        self.assertIn("cmd 44", cmds)

    def test_dedupe_bumps_count_and_fix(self):
        lessons = {"v": 1, "lessons": []}
        mh.record_lesson(lessons, "thrash", "pytest", err="first")
        mh.record_lesson(lessons, "thrash", "pytest", fix="python -m pytest")
        self.assertEqual(len(lessons["lessons"]), 1)
        self.assertEqual(lessons["lessons"][0]["n"], 2)
        self.assertEqual(lessons["lessons"][0]["fix"], "python -m pytest")

    def test_normalize_cmd(self):
        self.assertEqual(mh.normalize_cmd("  python   -m   unittest  "), "python -m unittest")
        self.assertEqual(len(mh.normalize_cmd("x" * 200)), 120)

    def test_top_lessons_filters_and_orders(self):
        lessons = {"v": 1, "lessons": [
            {"kind": "thrash", "cmd": "once", "err": "", "fix": "", "n": 1, "ts": 3},
            {"kind": "note", "cmd": "remember docs", "err": "", "fix": "", "n": 1, "ts": 2},
            {"kind": "thrash", "cmd": "twice", "err": "", "fix": "", "n": 2, "ts": 4},
            {"kind": "thrash", "cmd": "fixed", "err": "", "fix": "pytest", "n": 1, "ts": 1},
        ]}
        top = mh.top_lessons(lessons, k=3)
        self.assertEqual([item["cmd"] for item in top], ["twice", "remember docs", "fixed"])


class TestMidasLessonCli(unittest.TestCase):
    def run_cli(self, cwd, *args, env=None):
        cli = os.path.join(ROOT, "bin", "midas-lesson")
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        return subprocess.run(
            [cli, *args],
            cwd=cwd,
            env=run_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _data_dir(self, cfg):
        return os.path.join(cfg, "midas-data")

    def test_cli_records_note_and_dedupes(self):
        with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as cwd:
            env = {"CLAUDE_CONFIG_DIR": cfg}
            first = self.run_cli(cwd, "  remember   docs  ", env=env)
            second = self.run_cli(cwd, "remember docs", env=env)
            lessons = mh.load_lessons(os.path.realpath(cwd), base_dir=self._data_dir(cfg))
        self.assertEqual(first.stdout, "lesson saved\n")
        self.assertEqual(second.stdout, "lesson saved\n")
        self.assertEqual(lessons["lessons"][0]["kind"], "note")
        self.assertEqual(lessons["lessons"][0]["cmd"], "remember docs")
        self.assertEqual(lessons["lessons"][0]["n"], 2)

    def test_cli_ignores_plugin_data_no_cross_plugin_pollution(self):
        # CLI must write under CONFIG_DIR, never into $CLAUDE_PLUGIN_DATA.
        with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as other, \
                tempfile.TemporaryDirectory() as cwd:
            env = {"CLAUDE_CONFIG_DIR": cfg, "CLAUDE_PLUGIN_DATA": other}
            self.run_cli(cwd, "note text", env=env)
            in_cfg = mh.load_lessons(os.path.realpath(cwd), base_dir=self._data_dir(cfg))
            self.assertEqual(os.listdir(other), [])
        self.assertEqual(in_cfg["lessons"][0]["cmd"], "note text")

    def test_cli_empty_and_disabled_are_noops(self):
        with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as cwd:
            env = {"CLAUDE_CONFIG_DIR": cfg}
            empty = self.run_cli(cwd, "   ", env=env)
            disabled = self.run_cli(cwd, "note", env={**env, "MIDAS_DISABLE": "1"})
            lessons = mh.load_lessons(os.path.realpath(cwd), base_dir=self._data_dir(cfg))
        self.assertEqual(empty.stdout, "")
        self.assertEqual(disabled.stdout, "")
        self.assertEqual(lessons["lessons"], [])

    def test_cli_does_not_confirm_when_save_fails(self):
        with tempfile.NamedTemporaryFile() as bad_cfg, tempfile.TemporaryDirectory() as cwd:
            result = self.run_cli(cwd, "note", env={"MIDAS_CONFIG_DIR": bad_cfg.name})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_cli_works_from_unrelated_cwd_and_session_start_shows_note(self):
        with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as cwd:
            env = {"CLAUDE_CONFIG_DIR": cfg}
            result = self.run_cli(cwd, "test note", env=env)
            lessons = mh.load_lessons(os.path.realpath(cwd), base_dir=self._data_dir(cfg))
            out, _ = mh.handle_event("session_start", {"cwd": cwd}, mh.default_state(), lessons)
        self.assertEqual(result.stdout, "lesson saved\n")
        self.assertIn("test note", out["hookSpecificOutput"]["additionalContext"])


def bash_event(command, stderr="", stdout=""):
    return {
        "session_id": "s",
        "cwd": "/repo",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"is_error": False, "stderr": stderr, "stdout": stdout},
    }


def bash_fail_event(command, err="Exit code 2\nboom"):
    # Live-CC PostToolUseFailure shape: top-level error string, no tool_response.
    return {
        "session_id": "s",
        "cwd": "/repo",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "error": err,
        "is_interrupt": False,
    }


class TestLessonEvents(unittest.TestCase):
    def test_thrash_records_lesson(self):
        st = mh.default_state()
        lessons = {"v": 1, "lessons": []}
        _, st = mh.handle_event("post_tool_failure", bash_fail_event("pytest"), st, lessons)
        out, st = mh.handle_event(
            "post_tool_failure", bash_fail_event("pytest", err="ImportError: nope"), st, lessons)
        self.assertIsNotNone(out)
        self.assertEqual(lessons["lessons"][0]["kind"], "thrash")
        self.assertEqual(lessons["lessons"][0]["cmd"], "pytest")
        self.assertEqual(lessons["lessons"][0]["err"], "ImportError: nope")
        self.assertEqual(st["pending_fail_cmd"], "pytest")

    def test_success_after_thrash_records_fix(self):
        # v4: fix captured only when the failing command reran (arg-tweaked ok)
        st = mh.default_state()
        lessons = {"v": 1, "lessons": []}
        _, st = mh.handle_event("post_tool_failure", bash_fail_event("pytest -q"), st, lessons)
        _, st = mh.handle_event("post_tool_failure", bash_fail_event("pytest -q"), st, lessons)
        _, st = mh.handle_event("post_tool", bash_event("ls"), st, lessons)
        self.assertEqual(lessons["lessons"][0]["fix"], "")
        self.assertEqual(st["pending_fail_cmd"], "pytest -q")
        _, st = mh.handle_event("post_tool", bash_event("pytest -q -v"), st, lessons)
        self.assertEqual(lessons["lessons"][0]["fix"], "pytest -q -v")
        self.assertEqual(st["pending_fail_cmd"], "")

    def test_verify_success_after_thrash_records_no_fix(self):
        # v4 regression guard: an unrelated verify command is NOT a fix
        st = mh.default_state()
        lessons = {"v": 1, "lessons": []}
        _, st = mh.handle_event("post_tool_failure", bash_fail_event("python app.py"), st, lessons)
        _, st = mh.handle_event("post_tool_failure", bash_fail_event("python app.py"), st, lessons)
        _, st = mh.handle_event("post_tool", bash_event("python3 -m unittest discover -s tests"), st, lessons)
        self.assertEqual(lessons["lessons"][0]["fix"], "")
        self.assertEqual(st["pending_fail_cmd"], "python app.py")

    def test_legacy_is_error_thrash_records_lesson(self):
        # Legacy-runtime coverage: in-band is_error failures via post_tool still
        # record thrash lessons for runtimes that never send PostToolUseFailure.
        st = mh.default_state()
        lessons = {"v": 1, "lessons": []}
        fail = {
            "session_id": "s", "cwd": "/repo", "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
            "tool_response": {"is_error": True, "stderr": "ImportError: nope", "stdout": ""},
        }
        _, st = mh.handle_event("post_tool", fail, st, lessons)
        out, st = mh.handle_event("post_tool", fail, st, lessons)
        self.assertIsNotNone(out)
        self.assertEqual(lessons["lessons"][0]["kind"], "thrash")
        self.assertEqual(lessons["lessons"][0]["cmd"], "pytest")
        self.assertEqual(st["pending_fail_cmd"], "pytest")

    def test_session_start_lessons_block_capped_and_absent_is_v1(self):
        st = mh.default_state()
        out_plain, _ = mh.handle_event("session_start", {"cwd": "/repo"}, st)
        lessons = {"v": 1, "lessons": []}
        for i in range(8):
            mh.record_lesson(lessons, "note", "note " + ("x" * 60) + str(i))
        out, _ = mh.handle_event("session_start", {"cwd": "/repo"}, mh.default_state(), lessons)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        block = ctx.split("Past pitfalls this repo: ", 1)[1]
        self.assertEqual(out_plain["hookSpecificOutput"]["additionalContext"], mh.PROTOCOL)
        self.assertLessEqual(len("Past pitfalls this repo: " + block), 240)

    def test_session_start_lesson_recurring_suffix_threshold(self):
        lessons = {"v": 1, "lessons": [
            {"kind": "note", "cmd": "three times", "err": "", "fix": "", "n": 3, "ts": 2},
            {"kind": "note", "cmd": "four times", "err": "", "fix": "", "n": 4, "ts": 1},
        ]}
        out, _ = mh.handle_event("session_start", {"cwd": "/repo"}, mh.default_state(), lessons)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("four times (recurring: propose repo rule)", ctx)
        self.assertIn("three times", ctx)
        self.assertNotIn("three times (recurring: propose repo rule)", ctx)

    def test_session_start_lesson_recurring_suffix_respects_cap(self):
        lessons = {"v": 1, "lessons": [
            {"kind": "note", "cmd": "x" * 211, "err": "", "fix": "", "n": 4, "ts": 1},
        ]}
        out, _ = mh.handle_event("session_start", {"cwd": "/repo"}, mh.default_state(), lessons)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("Past pitfalls this repo:", ctx)
        self.assertNotIn("recurring:", ctx)

    def test_pre_tool_repeat_lesson_warns_once_and_allows(self):
        st = mh.default_state()
        lessons = {"v": 1, "lessons": [
            {"kind": "thrash", "cmd": "pytest", "err": "", "fix": "python -m pytest", "n": 2, "ts": 1}
        ]}
        data = {"tool_name": "Bash", "tool_input": {"command": " pytest "}}
        out, st = mh.handle_event("pre_tool", data, st, lessons)
        out2, _ = mh.handle_event("pre_tool", data, st, lessons)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "allow")
        self.assertIn("failed 2x", hso["additionalContext"])
        self.assertIsNone(out2)

    def test_no_cwd_main_skips_lesson_paths(self):
        with tempfile.TemporaryDirectory() as td:
            env = {"CLAUDE_PLUGIN_DATA": td}
            payload = json.dumps({"session_id": "nocwd", "tool_name": "Bash",
                                  "tool_input": {"command": "pytest"},
                                  "error": "Exit code 2\nboom", "is_interrupt": False})
            cmd = [sys.executable, os.path.join(ROOT, "hooks", "midas_hook.py"), "post_tool_failure"]
            for _ in range(2):
                subprocess.run(cmd, input=payload, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env={**os.environ, **env}, check=False)
            files = list(os.scandir(td))
        self.assertEqual(files, [])

    def test_main_persists_lessons_from_post_tool_failure(self):
        # main() must load/save lessons for the post_tool_failure event.
        with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as cwd:
            env = {"CLAUDE_CONFIG_DIR": cfg}
            payload = json.dumps({"session_id": "failwire", "cwd": cwd,
                                  "tool_name": "Bash",
                                  "tool_input": {"command": "pytest"},
                                  "error": "Exit code 2\nboom", "is_interrupt": False})
            cmd = [sys.executable, os.path.join(ROOT, "hooks", "midas_hook.py"), "post_tool_failure"]
            for _ in range(2):
                subprocess.run(cmd, input=payload, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env={**os.environ, **env}, check=False)
            lessons = mh.load_lessons(cwd, base_dir=os.path.join(cfg, "midas-data"))
            try:
                os.unlink(mh.state_path("failwire"))
            except OSError:
                pass
        self.assertEqual(lessons["lessons"][0]["kind"], "thrash")
        self.assertEqual(lessons["lessons"][0]["cmd"], "pytest")


if __name__ == "__main__":
    unittest.main()
