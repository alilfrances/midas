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
                    self.assertIn("hooks/midas_hook.py", command)


if __name__ == "__main__":
    unittest.main()
