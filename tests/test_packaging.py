import json
import os
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def load_json(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return json.load(f)


class TestPackaging(unittest.TestCase):
    def test_claude_and_codex_versions_match(self):
        claude = load_json(".claude-plugin", "plugin.json")
        codex = load_json(".codex-plugin", "plugin.json")
        self.assertEqual(claude["version"], codex["version"])
        self.assertEqual(claude["version"], "0.3.0")

    def test_codex_manifest_points_at_skills(self):
        manifest = load_json(".codex-plugin", "plugin.json")
        self.assertEqual(manifest["name"], "midas")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["interface"]["category"], "Developer Tools")

    def test_codex_marketplace_points_at_repo_root(self):
        marketplace = load_json(".agents", "plugins", "marketplace.json")
        self.assertEqual(marketplace["name"], "midas")
        plugin = marketplace["plugins"][0]
        self.assertEqual(plugin["name"], "midas")
        self.assertEqual(plugin["source"], {"source": "local", "path": "./"})

    def test_codex_hooks_use_portable_plugin_root(self):
        hooks = load_json("hooks", "hooks.json")["hooks"]
        for entries in hooks.values():
            for entry in entries:
                for hook in entry["hooks"]:
                    command = hook["command"]
                    self.assertIn("${CLAUDE_PLUGIN_ROOT:-${PLUGIN_ROOT:-.}}", command)
                    self.assertIn("hooks/codex_hook.py", command)

    def test_user_prompt_submit_registered(self):
        hooks = load_json("hooks", "hooks.json")["hooks"]
        self.assertIn("UserPromptSubmit", hooks)
        command = hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
        self.assertIn("${CLAUDE_PLUGIN_ROOT:-${PLUGIN_ROOT:-.}}", command)
        self.assertIn(" user_prompt", command)

        claude_hooks = load_json(".claude-plugin", "plugin.json")["hooks"]
        self.assertIn("UserPromptSubmit", claude_hooks)
        claude_command = claude_hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", claude_command)
        self.assertNotIn("PLUGIN_ROOT:-", claude_command)
        self.assertIn("hooks/midas_hook.py", claude_command)
        self.assertIn(" user_prompt", claude_command)

    def test_pre_tool_matcher_includes_read_in_both_manifests(self):
        codex_hooks = load_json("hooks", "hooks.json")["hooks"]
        self.assertEqual(codex_hooks["PreToolUse"][0]["matcher"], "Edit|Write|Bash|Read")
        claude_hooks = load_json(".claude-plugin", "plugin.json")["hooks"]
        self.assertEqual(claude_hooks["PreToolUse"][0]["matcher"], "Edit|Write|Bash|Read")

    def test_post_tool_failure_registered_in_both_manifests(self):
        codex_hooks = load_json("hooks", "hooks.json")["hooks"]
        self.assertIn("PostToolUseFailure", codex_hooks)
        entry = codex_hooks["PostToolUseFailure"][0]
        self.assertEqual(entry["matcher"], "Bash")
        command = entry["hooks"][0]["command"]
        self.assertIn("${CLAUDE_PLUGIN_ROOT:-${PLUGIN_ROOT:-.}}", command)
        self.assertIn("hooks/codex_hook.py", command)
        self.assertIn(" post_tool_failure", command)

        claude_hooks = load_json(".claude-plugin", "plugin.json")["hooks"]
        self.assertIn("PostToolUseFailure", claude_hooks)
        entry = claude_hooks["PostToolUseFailure"][0]
        self.assertEqual(entry["matcher"], "Bash")
        command = entry["hooks"][0]["command"]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", command)
        self.assertNotIn("PLUGIN_ROOT:-", command)
        self.assertIn("hooks/midas_hook.py", command)
        self.assertIn(" post_tool_failure", command)

    def test_codex_hook_adapter_exists(self):
        path = os.path.join(ROOT, "hooks", "codex_hook.py")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn('MIDAS_RUNTIME"] = "codex"', content)

    def test_midas_lesson_cli_is_executable_with_shebang(self):
        path = os.path.join(ROOT, "bin", "midas-lesson")
        self.assertTrue(os.access(path, os.X_OK))
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.readline().strip(), "#!/usr/bin/env python3")


if __name__ == "__main__":
    unittest.main()
