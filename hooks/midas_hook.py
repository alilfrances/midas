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

PROTOCOL = """Midas protocol. Every task:
1. EXPLORE before edit: Glob > Grep -n > Read narrow (offset/limit). Grep can locate — don't full-read big files.
2. PLAN multi-step work: goal, unknowns, ordered steps, verify step each. Template: midas:plan skill.
3. Batch independent tool calls in one block.
4. VERIFY before claiming done: run narrowest check, state evidence. No check possible — say so explicitly.
5. Same error twice: stop retrying, load midas:debug.
6. Ask user only when answer changes action. Batch questions, give recommended default (midas:ask).
On-demand playbooks: midas:plan midas:explore midas:edit midas:debug midas:verify midas:ask."""

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

def _nudge(msg):
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "additionalContext": msg}}


def _read_content(data):
    resp = data.get("tool_response") or {}
    # tool_response shape varies: {"file": {"content": str}}, {"content": str},
    # or a list of {"type": "text", "text": str} blocks
    if isinstance(resp, list):
        return "".join(b.get("text", "") for b in resp if isinstance(b, dict))
    if not isinstance(resp, dict):
        return ""
    file_data = resp.get("file")
    if isinstance(file_data, dict):
        return file_data.get("content") or ""
    content = resp.get("content")
    return content if isinstance(content, str) else ""


VERIFY_RE = re.compile(
    r"\b(pytest|unittest|npm (test|run)|npx|yarn (test|run)|jest|vitest"
    r"|go (test|build|vet)|cargo (test|check|build)|make|tsc|eslint|ruff"
    r"|mypy|flake8|swift (test|build)|xcodebuild|mvn|gradle|phpunit|rspec"
    r"|python3? -m (unittest|pytest))\b")


def _looks_like_verify(command):
    return bool(VERIFY_RE.search(command))


MCP_EXPLORE_RE = re.compile(
    r"(query|search|read|grep|find|symbol|overview|references|relations|impact)",
    re.IGNORECASE)


def _deny(reason):
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def handle_event(event, data, state):
    """Returns (stdout_json_dict_or_None, new_state)."""
    if event == "session_start":
        out = {"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": PROTOCOL,
        }}
        return out, state
    if event == "pre_tool":
        tool = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}
        path = tool_input.get("file_path", "")
        editing_existing = tool == "Edit" or (tool == "Write" and os.path.exists(path))
        unexplored = not state.get("explored") and state.get("reads", 0) < 2
        if editing_existing and unexplored and not state["edit_gate_fired"]:
            state["edit_gate_fired"] = True
            return _deny("Explore first: Glob/Grep then narrow Read (midas:explore). Then retry this edit. Gate fires once per session."), state
        return None, state
    if event == "post_tool":
        tool = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}

        if tool in ("Grep", "Glob"):
            state["explored"] = True

        # MCP retrieval tools (e.g. Cortex graph queries) count as exploration
        if tool.startswith("mcp__") and MCP_EXPLORE_RE.search(tool):
            state["explored"] = True

        if tool == "Read":
            state["reads"] += 1
            if state["reads"] >= 2:
                state["explored"] = True
            content = _read_content(data)
            unbounded = "limit" not in tool_input and "offset" not in tool_input
            if (unbounded and not state["read_nudge_fired"] and
                    content.count("\n") + bool(content) > 400):
                state["read_nudge_fired"] = True
                return _nudge("Huge Read. Use Grep -n then narrow Read. Load midas:explore."), state

        if tool in ("Edit", "Write"):
            state["edits_since_verify"] += 1

        if tool == "Bash":
            command = tool_input.get("command", "")
            resp = data.get("tool_response") or {}
            failed = bool(resp.get("is_error")) if isinstance(resp, dict) else False

            if _looks_like_verify(command):
                state["edits_since_verify"] = 0

            if failed and command == state.get("last_bash"):
                state["bash_fail_streak"] += 1
            elif failed:
                state["last_bash"] = command
                state["bash_fail_streak"] = 1
            else:
                state["last_bash"] = command
                state["bash_fail_streak"] = 0

            if (failed and state["bash_fail_streak"] >= 2 and
                    not state["thrash_nudge_fired"]):
                state["thrash_nudge_fired"] = True
                return _nudge("Same command failed twice. Stop retrying; load midas:debug."), state

    if event == "stop":
        if data.get("stop_hook_active"):
            return None, state
        edits = state.get("edits_since_verify", 0)
        if edits and not state["stop_gate_fired"]:
            state["stop_gate_fired"] = True
            return {
                "decision": "block",
                "reason": "%s edit(s) since verify. Run narrow check or state why skipped. Load midas:verify." % edits,
            }, state

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
