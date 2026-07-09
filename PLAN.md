# Midas Implementation Plan

> Historical plan only. This file records the original implementation roadmap, not the shipped `0.3.1` contract. For current behavior, use the live hook code plus `README.md` and `skills/midas/SKILL.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pure Claude Code plugin that scaffolds mid-tier models (Haiku/Sonnet) into frontier-tier agentic behavior — explore-before-edit, plan-act-verify, systematic debugging, quality questions — at net-zero token overhead.

**Architecture:** Deterministic Python hooks watch tool-use signals via a tiny per-session state file and inject micro-nudges (≤25 tokens, each at most once per session) only when a known small-model failure pattern fires (premature edit, huge unbounded read, retry-thrashing, stop-without-verify). A ~150-token protocol is injected once at SessionStart. Six on-demand skills carry the full playbooks and cost nothing until loaded; the explore skill's grep-first/narrow-read discipline typically saves more tokens than all injections combined.

**Tech Stack:** Python 3.10+ stdlib only (zero dependencies), Claude Code plugin system (hooks + skills), stdlib `unittest` for tests.

## Global Constraints

- Zero runtime dependencies: Python 3 stdlib only. No pip installs.
- Every hook must silent-fail: any exception → exit 0, no output. A broken hook must never break a session.
- Respect `CLAUDE_CONFIG_DIR`; never hardcode `~/.claude`.
- Kill switch: if env `MIDAS_DISABLE=1`, every hook exits 0 immediately with no output.
- Each nudge/gate fires at most ONCE per session (tracked in state) — repeated injection would violate the token budget.
- All model-facing strings (protocol, nudges, gate reasons, skill bodies) are caveman-compressed: no articles/filler, technical terms exact.
- State file writes are atomic (temp file + `os.replace`) and the state path is sanitized from session_id.
- Hook logic lives in pure functions `(event, data, state) -> (output_or_None, new_state)` so tests run in-memory with no subprocess/disk I/O.
- Do NOT commit — the coordinating agent reviews and commits.

---

### Task 1: Plugin manifest + marketplace

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `.claude-plugin/marketplace.json`
- Create: `.gitignore`
- Create: `LICENSE` (MIT, holder "Alil Kuizon", year 2026)

**Interfaces:**
- Produces: hook wiring that later tasks' dispatcher must satisfy — `hooks/midas_hook.py` invoked as `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/midas_hook.py" <event>` with events `session_start`, `pre_tool`, `post_tool`, `stop`.

- [ ] **Step 1: Write `.claude-plugin/plugin.json`**

```json
{
  "name": "midas",
  "description": "Turns mid-tier models to gold. Deterministic scaffolding — explore-before-edit gate, verify-before-stop gate, thrash detection, on-demand playbooks — lifts Haiku/Sonnet agentic quality a tier at net-zero token overhead.",
  "author": {
    "name": "Alil Kuizon"
  },
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/midas_hook.py\" session_start",
            "timeout": 5,
            "statusMessage": "Midas protocol loading..."
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/midas_hook.py\" pre_tool",
            "timeout": 5
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Grep|Glob|Read|Edit|Write|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/midas_hook.py\" post_tool",
            "timeout": 5
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/midas_hook.py\" stop",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: Write `.claude-plugin/marketplace.json`**

```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "midas",
  "description": "Midas — scaffolding plugin that lifts mid-tier model agentic quality a tier at net-zero token overhead.",
  "owner": {
    "name": "Alil Kuizon"
  },
  "plugins": [
    {
      "name": "midas",
      "description": "Mid-tier model to gold. Failure-pattern gates + on-demand playbooks. Net-zero tokens.",
      "source": "./",
      "category": "productivity"
    }
  ]
}
```

- [ ] **Step 3: Write `.gitignore`**

```
__pycache__/
*.pyc
.DS_Store
```

- [ ] **Step 4: Validate both JSON files parse**

Run: `python3 -c "import json; json.load(open('.claude-plugin/plugin.json')); json.load(open('.claude-plugin/marketplace.json')); print('OK')"`
Expected: `OK`

---

### Task 2: Hook dispatcher core — state + I/O shell + kill switch

**Files:**
- Create: `hooks/midas_hook.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces (used by every later task):
  - `handle_event(event: str, data: dict, state: dict) -> tuple[dict | None, dict]` — pure; returns (stdout JSON dict or None, updated state). Task 2 ships it as a stub returning `(None, state)`; Tasks 3–6 fill in branches.
  - `load_state(session_id: str) -> dict`, `save_state(session_id: str, state: dict) -> None`
  - `default_state() -> dict` returning `{"explored": False, "reads": 0, "edit_gate_fired": False, "read_nudge_fired": False, "thrash_nudge_fired": False, "stop_gate_fired": False, "edits_since_verify": 0, "last_bash": "", "last_bash_failed": false-as-False, "bash_fail_streak": 0}`
  - `main()` — reads stdin JSON, dispatches `sys.argv[1]`, prints JSON if any, always exits 0.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_state.py
import json
import os
import unittest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import midas_hook as mh


class TestState(unittest.TestCase):
    def test_default_state_keys(self):
        st = mh.default_state()
        for key in ("explored", "reads", "edit_gate_fired", "read_nudge_fired",
                    "thrash_nudge_fired", "stop_gate_fired", "edits_since_verify",
                    "last_bash", "bash_fail_streak"):
            self.assertIn(key, st)

    def test_state_path_sanitizes_session_id(self):
        p = mh.state_path("../../etc/passwd")
        self.assertNotIn("..", os.path.basename(p))
        self.assertNotIn("/", os.path.basename(p).replace("midas-", "").replace(".json", ""))

    def test_roundtrip(self):
        sid = "unittest-roundtrip"
        st = mh.default_state()
        st["reads"] = 3
        mh.save_state(sid, st)
        loaded = mh.load_state(sid)
        self.assertEqual(loaded["reads"], 3)
        os.unlink(mh.state_path(sid))

    def test_load_missing_returns_default(self):
        loaded = mh.load_state("unittest-never-saved-xyz")
        self.assertEqual(loaded, mh.default_state())

    def test_load_corrupt_returns_default(self):
        sid = "unittest-corrupt"
        with open(mh.state_path(sid), "w") as f:
            f.write("{not json")
        loaded = mh.load_state(sid)
        self.assertEqual(loaded, mh.default_state())
        os.unlink(mh.state_path(sid))

    def test_handle_event_unknown_is_noop(self):
        out, st = mh.handle_event("bogus", {}, mh.default_state())
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests, verify failure**

Run: `python3 -m unittest tests.test_state -v` (from repo root)
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'midas_hook'`

- [ ] **Step 3: Implement `hooks/midas_hook.py`**

```python
#!/usr/bin/env python3
"""Midas hook dispatcher. Zero-dep, stdlib only.

Usage: midas_hook.py <event>   event: session_start | pre_tool | post_tool | stop
Reads Claude Code hook JSON on stdin, may print hook JSON on stdout.
Silent-fails on any error (exit 0, no output) — never breaks a session.
"""
import json
import os
import re
import sys
import tempfile

# --- state -----------------------------------------------------------------

def default_state():
    return {
        "explored": False,          # any Grep/Glob this session
        "reads": 0,                 # Read count this session
        "edit_gate_fired": False,
        "read_nudge_fired": False,
        "thrash_nudge_fired": False,
        "stop_gate_fired": False,
        "edits_since_verify": 0,
        "last_bash": "",
        "bash_fail_streak": 0,
    }


def state_path(session_id):
    base = os.environ.get("TMPDIR") or tempfile.gettempdir()
    sid = re.sub(r"[^A-Za-z0-9_-]", "", session_id or "default") or "default"
    return os.path.join(base, "midas-%s.json" % sid)


def load_state(session_id):
    try:
        with open(state_path(session_id)) as f:
            data = json.load(f)
        merged = default_state()
        if isinstance(data, dict):
            merged.update({k: data[k] for k in merged if k in data})
        return merged
    except Exception:
        return default_state()


def save_state(session_id, state):
    path = state_path(session_id)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# --- event logic (pure; branches added per task) -----------------------------

def handle_event(event, data, state):
    """Returns (stdout_json_dict_or_None, new_state)."""
    return None, state


# --- shell -------------------------------------------------------------------

def main():
    if os.environ.get("MIDAS_DISABLE") == "1":
        return
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    data = json.load(sys.stdin)
    sid = data.get("session_id", "default")
    state = load_state(sid)
    out, state = handle_event(event, data, state)
    save_state(sid, state)
    if out is not None:
        print(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3 -m unittest tests.test_state -v`
Expected: all PASS (6 tests OK)

---

### Task 3: SessionStart protocol injection

**Files:**
- Modify: `hooks/midas_hook.py` (add `PROTOCOL` constant + `session_start` branch in `handle_event`)
- Test: `tests/test_session_start.py`

**Interfaces:**
- Consumes: `handle_event`, `default_state` from Task 2.
- Produces: on `session_start`, returns `({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": PROTOCOL}}, state)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session_start.py
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import midas_hook as mh


class TestSessionStart(unittest.TestCase):
    def test_injects_protocol(self):
        out, st = mh.handle_event("session_start", {"session_id": "s"}, mh.default_state())
        self.assertIsNotNone(out)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("EXPLORE", ctx)
        self.assertIn("VERIFY", ctx)
        self.assertIn("midas:debug", ctx)

    def test_protocol_under_budget(self):
        # ~4 chars/token heuristic; budget 200 tokens => 800 chars
        self.assertLess(len(mh.PROTOCOL), 800)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test, verify fails** — `python3 -m unittest tests.test_session_start -v` → FAIL (out is None / no PROTOCOL attr)

- [ ] **Step 3: Implement.** Add to `midas_hook.py`:

```python
PROTOCOL = """Midas protocol. Every task:
1. EXPLORE before edit: Glob > Grep -n > Read narrow (offset/limit). Grep can locate — don't full-read big files.
2. PLAN multi-step work: goal, unknowns, ordered steps, verify step each. Template: midas:plan skill.
3. Batch independent tool calls in one block.
4. VERIFY before claiming done: run narrowest check, state evidence. No check possible — say so explicitly.
5. Same error twice: stop retrying, load midas:debug.
6. Ask user only when answer changes action. Batch questions, give recommended default (midas:ask).
On-demand playbooks: midas:plan midas:explore midas:edit midas:debug midas:verify midas:ask."""
```

and replace the `handle_event` stub body's first lines with:

```python
def handle_event(event, data, state):
    """Returns (stdout_json_dict_or_None, new_state)."""
    if event == "session_start":
        out = {"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": PROTOCOL,
        }}
        return out, state
    return None, state
```

- [ ] **Step 4: Run all tests** — `python3 -m unittest discover -s tests -v` → all PASS

---

### Task 4: PostToolUse tracker — exploration, read nudge, bash thrash, verify reset

**Files:**
- Modify: `hooks/midas_hook.py` (add `post_tool` branch + helpers `_count_lines`, `_bash_failed`, `VERIFY_RE`, `_nudge`)
- Test: `tests/test_post_tool.py`

**Interfaces:**
- Consumes: `handle_event` dispatch from Task 3.
- Produces state transitions later tasks rely on: `explored`, `reads`, `edits_since_verify`, `bash_fail_streak`; nudge output shape `{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "<msg>"}}`.
- Hook input fields used: `tool_name`, `tool_input` (dict), `tool_response` (dict or list — shapes vary; see tokenslim project notes). Treat defensively.

**Behavior spec:**
- `Grep`/`Glob` → `explored = True`. No output.
- `Read` → `reads += 1`; also sets `explored = True` when `reads >= 2`. If response text > 400 lines AND `tool_input` has no `limit` AND `read_nudge_fired` is False → nudge `"Midas: large read. Next time Grep -n first, Read offset/limit."`, set `read_nudge_fired`.
- `Edit`/`Write` → `edits_since_verify += 1`. No output.
- `Bash`: let `cmd = tool_input.get("command","")`. If `VERIFY_RE` matches `cmd` → `edits_since_verify = 0`. Failure detection `_bash_failed(resp)`: True if `resp` is a dict and (`resp.get("is_error")` truthy, or `resp.get("interrupted")` truthy, or a non-zero `returnCode`/`exit_code` field present). If failed and `cmd.strip() == state["last_bash"]` → `bash_fail_streak += 1` else streak = 1 (failed) / 0 (succeeded). When streak reaches 2 and `thrash_nudge_fired` False → nudge `"Midas: same command failed 2x. Stop retrying. Read error verbatim, form one hypothesis, load midas:debug."`, set `thrash_nudge_fired`. Always store `last_bash = cmd.strip()`.
- `VERIFY_RE = re.compile(r"\b(pytest|unittest|npm (test|run)|npx|yarn (test|run)|jest|vitest|go (test|build|vet)|cargo (test|check|build)|make|tsc|eslint|ruff|mypy|flake8|swift (test|build)|xcodebuild|mvn|gradle|phpunit|rspec|python3? -m (unittest|pytest))\b")`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_post_tool.py
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
```

- [ ] **Step 2: Run, verify fails** — `python3 -m unittest tests.test_post_tool -v` → FAILs (post_tool branch missing)

- [ ] **Step 3: Implement the `post_tool` branch** per behavior spec. Response-text extraction for Read must handle: dict with `file.content` (str), plain `content` str, or list of `{"type":"text","text":...}` blocks — concatenate what exists, count `\n`. Nudge helper:

```python
def _nudge(msg):
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "additionalContext": msg}}
```

- [ ] **Step 4: Run full suite** — `python3 -m unittest discover -s tests -v` → all PASS

---

### Task 5: PreToolUse premature-edit gate

**Files:**
- Modify: `hooks/midas_hook.py` (add `pre_tool` branch)
- Test: `tests/test_pre_tool.py`

**Interfaces:**
- Consumes: state fields `explored`, `reads`, `edit_gate_fired` from Task 4.
- Produces deny shape: `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "<msg>"}}`.

**Behavior spec:** On `pre_tool` with `tool_name` in (`Edit`, `Write`):
- Allow silently (return `None`) when: `edit_gate_fired` already True, OR `explored` True, OR `reads >= 2`, OR tool is `Write` to a path that does not exist yet (`os.path.exists` False — creating new files needs no exploration).
- Otherwise deny ONCE with reason: `"Midas gate: editing with no exploration. Grep usages of the symbol/file you are changing (midas:explore), then retry this exact edit. Gate fires once per session."` and set `edit_gate_fired = True`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pre_tool.py
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
```

- [ ] **Step 2: Run, verify fails** — `python3 -m unittest tests.test_pre_tool -v` → FAIL
- [ ] **Step 3: Implement `pre_tool` branch** per spec.
- [ ] **Step 4: Run full suite** — `python3 -m unittest discover -s tests -v` → all PASS

---

### Task 6: Stop verification gate

**Files:**
- Modify: `hooks/midas_hook.py` (add `stop` branch)
- Test: `tests/test_stop.py`

**Interfaces:**
- Consumes: `edits_since_verify`, `stop_gate_fired` from Task 4.
- Produces block shape: `{"decision": "block", "reason": "<msg>"}`.

**Behavior spec:** On `stop`:
- If `data.get("stop_hook_active")` is truthy → return `None` (never loop).
- If `edits_since_verify > 0` and `stop_gate_fired` False → block with reason `"Midas verify gate: {N} edit(s) since last check. Run narrowest verification (midas:verify) or state one line why verification is skipped — then stop."`, set `stop_gate_fired = True` and reset `edits_since_verify = 0` (a deliberate skip-statement satisfies the gate; it never fires twice).
- Else `None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stop.py
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import midas_hook as mh


class TestStopGate(unittest.TestCase):
    def test_edits_without_verify_blocks_once(self):
        st = mh.default_state()
        st["edits_since_verify"] = 2
        out, st = mh.handle_event("stop", {"session_id": "s"}, st)
        self.assertEqual(out["decision"], "block")
        self.assertIn("2 edit", out["reason"])
        out2, _ = mh.handle_event("stop", {"session_id": "s"}, st)
        self.assertIsNone(out2)

    def test_no_edits_no_block(self):
        out, _ = mh.handle_event("stop", {"session_id": "s"}, mh.default_state())
        self.assertIsNone(out)

    def test_stop_hook_active_never_blocks(self):
        st = mh.default_state()
        st["edits_since_verify"] = 5
        out, _ = mh.handle_event("stop", {"session_id": "s", "stop_hook_active": True}, st)
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, verify fails** — `python3 -m unittest tests.test_stop -v` → FAIL
- [ ] **Step 3: Implement `stop` branch** per spec.
- [ ] **Step 4: Run full suite** — `python3 -m unittest discover -s tests -v` → all PASS (state + session_start + post_tool + pre_tool + stop)

---

### Task 7: Six skills

**Files:**
- Create: `skills/midas/SKILL.md`, `skills/plan/SKILL.md`, `skills/explore/SKILL.md`, `skills/edit/SKILL.md`, `skills/debug/SKILL.md`, `skills/verify/SKILL.md`, `skills/ask/SKILL.md`

**Interfaces:**
- Consumes: skill names referenced by PROTOCOL and nudges (`midas:plan`, `midas:explore`, `midas:edit`, `midas:debug`, `midas:verify`, `midas:ask`) — directory names must match exactly.

Each file: YAML frontmatter (`name`, `description`), body caveman-compressed, hard cap 350 words body. Write these files verbatim:

- [ ] **Step 1: `skills/midas/SKILL.md`**

```markdown
---
name: midas
description: Midas overview + status. Trigger /midas for what plugin does, which gates active, how to disable (MIDAS_DISABLE=1).
---

# Midas

Scaffolding for mid-tier models. Active pieces:

- Session protocol injected at start (~150 tokens, once).
- Edit gate: first edit with zero exploration denied once — grep first, retry.
- Verify gate: stop after edits with no check blocked once — verify or state why skipped.
- Thrash nudge: same command failing 2x triggers debug protocol pointer.
- Large-read nudge: unbounded read >400 lines triggers grep-first pointer.

Each gate/nudge fires max once per session. Disable everything: `MIDAS_DISABLE=1`.

Playbooks (load on demand): midas:plan midas:explore midas:edit midas:debug midas:verify midas:ask.
```

- [ ] **Step 2: `skills/plan/SKILL.md`**

```markdown
---
name: plan
description: Decomposition template for multi-step work. Use before any task needing 3+ actions or touching 2+ files. Prevents malformed plans and mid-task drift.
---

# Plan

Before acting on multi-step work, write plan in this shape (5-10 lines max):

1. **Goal** — one sentence, restate what user asked. If restatement uncertain, that signal to ask (midas:ask).
2. **Unknowns** — what you must discover first. Each unknown maps to one explore action (Grep/Glob/Read).
3. **Steps** — ordered, each one concrete action. Each step names files touched.
4. **Verify** — per step or at end: exact command proving it worked.
5. **Out of scope** — what you will NOT touch. Guards against drift.

Rules:
- Resolve unknowns BEFORE step 1. Never plan around a guess.
- Step with no verify method = smell. Rework it.
- Plan changed mid-task? State new plan in one line, then continue. Silent pivots cause thrash.
- Small task (1 file, obvious change): skip template, just state goal + verify.
```

- [ ] **Step 3: `skills/explore/SKILL.md`**

```markdown
---
name: explore
description: Token-cheap codebase search strategy. Use before editing unfamiliar code, when locating symbols/usages/config, or after Midas edit gate fires.
---

# Explore

Funnel, cheapest first:

1. **Glob** pattern for candidate files (`**/*auth*`, `src/**/*.ts`).
2. **Grep -l** to shortlist files containing symbol.
3. **Grep -n -C 3** for definition + usages with context. Search declaration patterns (`def X`, `class X`, `func X`, `X =`) not bare name.
4. **Read narrow** — offset/limit around the grep hit line. Full read only when file <200 lines.

Rules:
- Batch independent searches in one tool block — parallel, one round-trip.
- Before changing symbol: grep ALL usages, not just definition. Callers break silently otherwise.
- Wrong-looking grep results: widen pattern once, then try Glob on filenames. Two dead ends = rethink term, don't brute-force variants.
- Never Read whole file to "get context" when Grep can locate. Full-file reads are the #1 token leak.
- Record what you learned in one line before moving on (file:line of target, usage count).
```

- [ ] **Step 4: `skills/edit/SKILL.md`**

```markdown
---
name: edit
description: Editing discipline — smallest correct diff, style match, one concern per edit. Use before non-trivial edits or multi-file changes.
---

# Edit

Before edit:
- Read the exact region you change plus enclosing function/class. Not whole file.
- Grep usages of anything you rename/re-sign — every caller updates in same task.

During edit:
- Smallest diff that is correct. No drive-by refactors, no formatting churn, no added comments for simple code.
- Match surrounding style exactly: naming, indent, idiom, comment density.
- One concern per edit call. Two unrelated fixes = two edits.
- Multi-file change: order edits so code compiles at each step where possible (types first, then users).

After edit:
- Each edit needs verify path (midas:verify). Batch edits, then verify once — not verify per keystroke.
- Edit failed to match? Re-Read the exact lines, don't guess at whitespace.
```

- [ ] **Step 5: `skills/debug/SKILL.md`**

```markdown
---
name: debug
description: Systematic debugging loop. Load when any command/test fails twice, output surprises, or Midas thrash nudge fires. Replaces retry-guessing.
---

# Debug

Loop (max 4 iterations, then report honestly):

1. **Reproduce** — one command that shows failure. No repro = no debugging, find repro first.
2. **Read error verbatim** — quote exact message. First actionable error only; downstream errors are noise.
3. **Locate** — grep the failing symbol/file/line from the error. Read narrow around it.
4. **Hypothesize** — ONE sentence: "fails because X". No hypothesis = gather more data (add print/log, read caller), don't edit.
5. **Test hypothesis** — single smallest change. Never two changes at once — can't attribute result.
6. **Verify** — rerun repro. Fixed: remove debug artifacts, done. Not fixed: hypothesis wrong, revert change, back to 2 with new data.

Bans:
- No shotgun edits (changing several suspects at once).
- No retrying identical command hoping different result.
- No "fix" that silences error without explaining original cause.
- 4 failed loops: report exact command, error, hypotheses tried, current state. Honest stuck beats fake progress.
```

- [ ] **Step 6: `skills/verify/SKILL.md`**

```markdown
---
name: verify
description: Evidence-before-claims checklist. Load before saying done/fixed/passing, or when Midas verify gate blocks a stop.
---

# Verify

Claim requires evidence. Before "done" / "fixed" / "works":

1. Pick narrowest real check: the one failing test, the touched module's tests, targeted build. Full suite only when change is broad or project demands.
2. Run it. Read output — exit code AND content. "Command ran" is not "check passed".
3. Behavior change: exercise changed path (run app/script/curl), not just compile.
4. State evidence in report: command + result, one line. "Tests pass" alone = unverified claim.

Failures:
- Check fails: report it as failing with exact output. Never claim partial success as success.
- Can't verify (needs device/network/credentials): say exactly what wasn't verified and why. That honest, acceptable.
- Skipping verification for trivial change (docs, comment): state "verification skipped: docs-only" — one line satisfies gate.
```

- [ ] **Step 7: `skills/ask/SKILL.md`**

```markdown
---
name: ask
description: Question quality rules — when to ask user vs decide, how to batch, when defaults beat questions. Use when tempted to ask user anything.
---

# Ask

Ask ONLY when answer changes what you do next AND you cannot resolve from request, code, or convention.

Don't ask when:
- Codebase answers it (grep/read first — 2 minutes of exploring beats round-trip).
- Convention answers it (project style, framework default).
- Any reasonable option is easily reversible — pick one, state choice, proceed.

When you do ask:
- Batch ALL questions in one message. Serial questions burn user round-trips.
- Each question: concrete options + your recommended default + why. "Option A (recommended): ... Option B: ..." Never open-ended "what do you want?"
- State what you'll do if no answer — then user silence is also answer.

Blocking vs non-blocking: irreversible/destructive/scope-changing = must ask. Everything else = decide, note decision in report, user can course-correct.
```

- [ ] **Step 8: Verify frontmatter parses** — for each file, `head -5` shows `---`, `name:`, `description:` lines. Run: `grep -L "^description:" skills/*/SKILL.md` → expected: no output.

---

### Task 8: README with install instructions + token budget

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: everything above; feature table must match shipped behavior exactly (no invented numbers).

- [ ] **Step 1: Write `README.md`** with these sections, in this order:

1. **Title + one-line pitch:** "Midas — turns mid-tier models to gold. Deterministic scaffolding that lifts Haiku/Sonnet agentic quality toward frontier-tier behavior at net-zero token overhead."
2. **Why** (3 sentences): research shows scaffolding roughly doubles weaker-model success on agentic tasks while barely moving frontier models; dominant small-model failures are premature edits before exploration, skipped verification, and retry-thrashing; Midas targets exactly these with deterministic gates, not prompt bloat.
3. **What you get** table: | Piece | Fires when | Cost | — rows for session protocol (~150 tok, once), edit gate (deny once, 0 tok until fired), verify gate (block once), thrash nudge (~25 tok, once), large-read nudge (~25 tok, once), six on-demand skills (0 until loaded).
4. **Install:**
   ````markdown
   ## Install

   In Claude Code:

   ```
   /plugin marketplace add alilkuizon/midas
   /plugin install midas@midas
   ```

   From a local clone:

   ```
   /plugin marketplace add "/path/to/midas"
   /plugin install midas@midas
   ```

   Requires Python 3.10+ on PATH (`python3`). No other dependencies.
   ````
5. **Token budget** section: worst-case unconditional cost = protocol ~150 tokens + skill descriptions ~120 tokens ≈ 270 tokens/session; every other injection is failure-triggered and once-only (≤ ~100 tokens combined worst case); the explore discipline (grep-first, offset/limit reads) typically saves thousands of tokens per session on any non-trivial codebase, so expected net is negative. State these as design targets, not measured benchmarks.
6. **Disable:** `MIDAS_DISABLE=1` env var kills all hooks; `/plugin uninstall midas@midas` removes.
7. **How it works** (short): per-session state file in `$TMPDIR`, pure-function hook logic, silent-fail everywhere.

- [ ] **Step 2: Verify install commands match marketplace.json** — plugin name `midas`, marketplace name `midas` → `midas@midas` correct.

- [ ] **Step 3: Full suite final run** — `python3 -m unittest discover -s tests -v` → all PASS. Also end-to-end smoke:

```bash
echo '{"session_id":"smoke"}' | python3 hooks/midas_hook.py session_start
```
Expected: one JSON line containing `"additionalContext"`.

```bash
MIDAS_DISABLE=1 sh -c 'echo "{\"session_id\":\"smoke\"}" | python3 hooks/midas_hook.py session_start'
```
Expected: no output, exit 0.

```bash
echo 'not json' | python3 hooks/midas_hook.py stop; echo "exit=$?"
```
Expected: `exit=0`, no other output (silent-fail).

---

## Self-Review Notes

- Spec coverage: token-neutrality (Global Constraints + Task 8 budget), optimal tool use (Task 4 nudges + explore skill), planning (plan skill + protocol), reading/grep (explore skill + read nudge), writing (edit skill + edit gate), instruction-to-action (protocol + plan skill), question quality (ask skill), install instructions (Task 8), pure plugin (Tasks 1–8, no MCP/server).
- Type consistency: `handle_event(event, data, state) -> (dict|None, dict)` used identically in all test files; state keys defined once in Task 2 `default_state` and consumed by name in Tasks 4–6.
- No placeholders: all skill bodies, hook JSON shapes, and test code are verbatim.
