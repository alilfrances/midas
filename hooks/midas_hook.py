#!/usr/bin/env python3
"""Midas hook dispatcher. Zero-dep, stdlib only.

Usage: midas_hook.py <event>
event: session_start | user_prompt | pre_tool | post_tool | post_tool_failure | stop
Reads Claude Code hook JSON on stdin, may print hook JSON on stdout.
Silent-fails on any error (exit 0, no output) — never breaks a session.
"""
import difflib
import json
import hashlib
import os
import re
import sys
import tempfile
import time

CLAUDE_PROTOCOL = """Midas protocol. Every task:
1. EXPLORE before edit: Glob > Grep -n > Read narrow (offset/limit). Grep can locate — don't full-read big files.
2. PLAN multi-step work: goal, unknowns, ordered steps, verify step each. Template: midas:plan skill.
3. Batch independent tool calls in one block.
4. VERIFY before claiming done: run narrowest check, state evidence. No check possible — say so explicitly.
5. Same error twice: stop retrying, load midas:debug.
6. Ask user only when answer changes action. Batch questions, give recommended default (midas:ask).
7. Capability limits: missing tool/creds/device/network, or task outside scope — state upfront, offer nearest alternative, decline beats fake (midas:scope).
8. Evidence contradicts user instruction: say so once, one line, cite evidence (file:line, test output). Explicit override → comply, note objection in report. Never argue twice, never silently comply against evidence.
On-demand playbooks: midas:plan midas:explore midas:edit midas:debug midas:verify midas:ask midas:scope."""

CODEX_PROTOCOL = """Midas protocol. Every task:
1. EXPLORE before edit: rg --files for candidates, rg -n -C 3 for symbols, then narrow reads (sed -n or equivalent). Don't full-read big files.
2. PLAN multi-step work: goal, unknowns, ordered steps, verify step each. Template: midas:plan skill.
3. Batch independent tool calls in one block.
4. VERIFY before claiming done: run narrowest check, state evidence. No check possible — say so explicitly.
5. Same error twice: stop retrying, load midas:debug.
6. Ask user only when answer changes action. Batch questions, give recommended default (midas:ask).
7. Capability limits: missing tool/creds/device/network, or task outside scope — state upfront, offer nearest alternative, decline beats fake (midas:scope).
8. Evidence contradicts user instruction: say so once, one line, cite evidence (file:line, test output). Explicit override → comply, note objection in report. Never argue twice, never silently comply against evidence.
On-demand playbooks: midas:plan midas:explore midas:edit midas:debug midas:verify midas:ask midas:scope."""

PROTOCOL = CLAUDE_PROTOCOL

# --- state -----------------------------------------------------------------

def default_state():
    return {
        "explored": False,          # session-global: set ONLY by MCP retrieval tools
        "reads": 0,                 # Read count this session (counter only)
        "read_paths": [],           # realpaths of files Read this session (cap 50, FIFO)
        "explored_dirs": [],        # realpaths of dirs covered by Grep/Glob (cap 20, FIFO)
        "edit_gate_fired": False,
        "read_nudge_fired": False,
        "preread_gate_fired": False,
        "thrash_nudge_fired": False,
        "stop_gate_fired": False,
        "edits_since_verify": 0,
        "last_bash": "",
        "last_fail_cmd": "",        # last failing Bash command (streak lives here)
        "bash_fail_streak": 0,
        "lesson_warn_fired": False,
        "pending_fail_cmd": "",
        "router_fired": [],
        "freshness_fired": False,
        "prompt_freshness_fired": False,
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


# --- lessons ---------------------------------------------------------------

def _empty_lessons():
    return {"v": 1, "lessons": []}


def _lessons_base_dir(base_dir=None):
    # NOTE: must NOT use $CLAUDE_PLUGIN_DATA. That var is context-dependent —
    # set to midas's dir inside midas's hook, but ambient (or another plugin's
    # dir) during a plain Bash call like bin/midas-lesson. Keying off it would
    # split the store between the hook and CLI and pollute sibling plugins.
    # $MIDAS_CONFIG_DIR gives Codex/other runtimes a neutral override;
    # $CLAUDE_CONFIG_DIR remains the Claude Code-compatible default.
    if base_dir:
        return base_dir
    if os.environ.get("MIDAS_CONFIG_DIR"):
        return os.path.join(os.environ.get("MIDAS_CONFIG_DIR"), "midas-data")
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR"), "midas-data")
    return os.path.expanduser(os.path.join("~", ".claude", "midas-data"))


def lessons_path(cwd, base_dir=None):
    try:
        if not cwd:
            return None
        # realpath so hook (raw cwd field) and CLI (os.getcwd()) agree on
        # symlinked paths — otherwise CLI notes never match hook lookups.
        key = os.path.realpath(str(cwd))
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        return os.path.join(_lessons_base_dir(base_dir), "lessons-%s.json" % digest)
    except Exception:
        return None


def load_lessons(cwd, base_dir=None):
    try:
        path = lessons_path(cwd, base_dir=base_dir)
        if not path:
            return _empty_lessons()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("lessons"), list):
            return _empty_lessons()
        return {"v": 1, "lessons": [x for x in data["lessons"] if isinstance(x, dict)]}
    except Exception:
        return _empty_lessons()


def save_lessons(cwd, lessons, base_dir=None):
    try:
        path = lessons_path(cwd, base_dir=base_dir)
        if not path:
            return False
        items = list(lessons.get("lessons") or [])
        items = sorted(items, key=lambda item: item.get("ts", 0))[-40:]
        data = {"v": 1, "lessons": items}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, path)
            with open(path, encoding="utf-8") as f:
                return json.load(f) == data
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False
    except Exception:
        return False


def normalize_cmd(cmd):
    try:
        return re.sub(r"\s+", " ", str(cmd or "").strip())[:120]
    except Exception:
        return ""


def record_lesson(lessons, kind, cmd, err="", fix=""):
    try:
        norm = normalize_cmd(cmd)
        if not norm:
            return
        now = int(time.time())
        items = lessons.setdefault("lessons", [])
        for item in items:
            if item.get("kind") == kind and item.get("cmd") == norm:
                item["n"] = int(item.get("n") or 0) + 1
                item["ts"] = now
                if fix:
                    item["fix"] = normalize_cmd(fix)
                if err:
                    item["err"] = str(err)[:80]
                return
        items.append({
            "kind": kind,
            "cmd": norm,
            "err": str(err or "")[:80],
            "fix": normalize_cmd(fix),
            "n": 1,
            "ts": now,
        })
    except Exception:
        pass


def top_lessons(lessons, k=3):
    try:
        items = lessons.get("lessons") or []
        eligible = [
            item for item in items
            if item.get("kind") == "note" or int(item.get("n") or 0) >= 2 or item.get("fix")
        ]
        return sorted(eligible, key=lambda item: item.get("ts", 0), reverse=True)[:k]
    except Exception:
        return []


# --- event logic (pure; branches added per task) -----------------------------

def _nudge(msg):
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "additionalContext": msg}}


def _failure_nudge(msg):
    return {"hookSpecificOutput": {"hookEventName": "PostToolUseFailure",
                                   "additionalContext": msg}}


def _pre_tool_context(msg):
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "additionalContext": msg,
    }}


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


STALE_RE = re.compile(
    r"(ModuleNotFoundError|ImportError: cannot import|npm ERR!|ERESOLVE"
    r"|unknown option|unrecognized arguments?|no such option|deprecat(ed|ion)"
    r"|is not a function|has no attribute|does not provide an export"
    r"|requires? .{0,40}version|incompatible)",
    re.IGNORECASE)

FRESH_PROMPT_RE = re.compile(
    r"\b(latest|newest|up[- ]to[- ]date|current version|upgrade|migrat(e|ion)"
    r"|deprecated|v?\d+\.\d+ (to|->) v?\d+)\b",
    re.IGNORECASE)


def _bash_error_text(data):
    try:
        resp = data.get("tool_response") or {}
        if not isinstance(resp, dict):
            return ""
        return "\n".join(str(resp.get(key) or "") for key in ("stderr", "stdout")).strip()[:2000]
    except Exception:
        return ""


def _first_token(command):
    parts = normalize_cmd(command).split()
    return parts[0] if parts else ""


def _same_command(a, b):
    na, nb = normalize_cmd(a), normalize_cmd(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if _first_token(na) != _first_token(nb):
        return False
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.8


def _handle_bash_failure(state, lessons, command, err_text, wrap):
    """Shared failure logic: streak, thrash nudge, freshness nudge.

    Called from post_tool_failure (live CC) and the legacy is_error branch of
    post_tool (runtimes that report failures in-band). `wrap` builds the
    event-appropriate nudge output.
    """
    if _same_command(command, state.get("last_fail_cmd", "")):
        state["bash_fail_streak"] += 1
    else:
        state["last_fail_cmd"] = command
        state["bash_fail_streak"] = 1

    if state["bash_fail_streak"] >= 2 and not state["thrash_nudge_fired"]:
        state["thrash_nudge_fired"] = True
        if lessons is not None:
            failed_cmd = normalize_cmd(command)
            record_lesson(lessons, "thrash", failed_cmd, err=err_text[:80])
            state["pending_fail_cmd"] = failed_cmd
        return wrap("Same command failed twice. Stop retrying; load midas:debug."), state

    if not state.get("freshness_fired") and STALE_RE.search(err_text):
        state["freshness_fired"] = True
        return wrap("Error smells like stale API knowledge (version/signature drift). Fetch current docs first — context7 MCP if present, else official docs via WebFetch — then fix."), state

    return None, state


def _set_lesson_fix(lessons, failed_cmd, fix_cmd):
    try:
        norm_failed = normalize_cmd(failed_cmd)
        norm_fix = normalize_cmd(fix_cmd)
        for item in lessons.get("lessons") or []:
            if item.get("kind") == "thrash" and item.get("cmd") == norm_failed:
                item["fix"] = norm_fix
                item["ts"] = int(time.time())
                return True
    except Exception:
        pass
    return False


def _track_path(items, path, cap):
    """Append realpath(path) to items, dedupe, FIFO-cap. Silent on any error."""
    try:
        if not path:
            return
        real = os.path.realpath(path)
        if real in items:
            return
        items.append(real)
        del items[:-cap]
    except Exception:
        pass


def _path_under(target, d):
    """True when target is d or lives under directory d (no /a/b vs /a/bc trap)."""
    try:
        if not d:
            return False
        if target == d:
            return True
        return target.startswith(d.rstrip(os.sep) + os.sep)
    except Exception:
        return False


def _edit_target_explored(state, path):
    target = os.path.realpath(path)
    if target in (state.get("read_paths") or []):
        return True
    if any(_path_under(target, d) for d in (state.get("explored_dirs") or [])):
        return True
    # MCP exploration is session-global: tool inputs carry no reliable path
    return bool(state.get("explored"))


MCP_EXPLORE_RE = re.compile(
    r"(query|search|read|grep|find|symbol|overview|references|relations|impact"
    r"|localize|inspect|investigat|triage|spectrum|context|repro|resolve|docs)",
    re.IGNORECASE)

# Deliberately narrower than the explore regex: anchored run_tests?/_tests?
# forms avoid false positives like mcp__contest__... via bare "test".
MCP_VERIFY_RE = re.compile(
    r"(run_tests?|_tests?\b|verify|lint|typecheck|check|build|compile)",
    re.IGNORECASE)

ROUTER_PATTERNS = (
    ("read", re.compile(r"^(?!tail\s+.*-[fF]\b)(cat|head|tail|less|more)\s+\S"),
     "Use Read tool (offset/limit) not {cmd0}. Retry with Read. Gate fires once per class."),
    ("search", re.compile(r"^(grep|rg|egrep|fgrep)\s"),
     "Use Grep tool not shell grep. Structured output, cheaper. Retry with Grep. Gate fires once per class."),
    # Deny only Glob-replaceable predicates (path arg optional). Metadata
    # predicates (-mtime, -size, -user, ...) have no Glob equivalent — allowed.
    ("find", re.compile(r"^find\b(?:\s+\S+)*?\s+-(name|iname|path|ipath|regex|type)\b"),
     "Use Glob tool not find. Retry with Glob. Gate fires once per class."),
)

CODEX_ROUTER_PATTERNS = (
    ("read", re.compile(r"^(?!tail\s+.*-[fF]\b)(cat|head|tail|less|more)\s+\S"),
     "Avoid full-file {cmd0}. Use rg -n first, then a bounded read like sed -n 'START,ENDp'. Gate fires once per class."),
    ("search", re.compile(r"^(grep|egrep|fgrep)\s"),
     "Use rg -n -C 3 instead of {cmd0}. Gate fires once per class."),
    ("find", re.compile(r"^find\b(?:\s+\S+)*?\s+-(name|iname|path|ipath|regex|type)\b"),
     "Use rg --files -g 'PATTERN' instead of find -name. Gate fires once per class."),
)

COMPOUND_TOKENS = ("|", ">", "<", "&&", ";", "$(")


def _deny(reason):
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def _read_nudge_limit():
    try:
        return int(os.environ.get("MIDAS_READ_NUDGE_LINES", "400"))
    except Exception:
        return 400


def _file_exceeds_lines(path, threshold):
    """Bounded, silent-fail line probe: True only when path is a text file with
    more than `threshold` newlines. Stops counting at threshold+1, scans at most
    5MB, treats NUL-in-first-chunk as binary (no deny), any OSError -> False."""
    try:
        count = 0
        scanned = 0
        first = True
        with open(path, "rb") as f:
            while scanned < 5 * 1024 * 1024:
                chunk = f.read(65536)
                if not chunk:
                    break
                if first:
                    if b"\x00" in chunk:
                        return False
                    first = False
                scanned += len(chunk)
                count += chunk.count(b"\n")
                if count > threshold:
                    return True
        return False
    except Exception:
        return False


def _runtime(data=None):
    try:
        if isinstance(data, dict) and data.get("midas_runtime"):
            return str(data.get("midas_runtime"))
        env_runtime = os.environ.get("MIDAS_RUNTIME")
        if env_runtime:
            return env_runtime
        if os.environ.get("PLUGIN_ROOT") and not os.environ.get("CLAUDE_PLUGIN_ROOT"):
            return "codex"
    except Exception:
        pass
    return "claude"


def _protocol_for_runtime(runtime):
    if runtime == "codex":
        return CODEX_PROTOCOL
    return CLAUDE_PROTOCOL


def _router_patterns_for_runtime(runtime):
    if runtime == "codex":
        return CODEX_ROUTER_PATTERNS
    return ROUTER_PATTERNS


def _router_deny(command, state, runtime="claude"):
    try:
        cmd = command.strip()
        if not cmd or any(token in cmd for token in COMPOUND_TOKENS):
            return None
        fired = state.setdefault("router_fired", [])
        cmd0 = cmd.split()[0]
        for name, pattern, reason in _router_patterns_for_runtime(runtime):
            if name in fired:
                continue
            if pattern.search(cmd):
                fired.append(name)
                return _deny(reason.format(cmd0=cmd0))
    except Exception:
        return None
    return None


def _lesson_entry_text(item):
    if item.get("kind") == "note":
        text = item.get("cmd", "")
    else:
        fix = item.get("fix") or ""
        text = "`%s` failed" % item.get("cmd", "")
        if fix:
            text += ", fix: `%s`" % fix
    if int(item.get("n") or 0) >= 4:
        text += " (recurring: propose repo rule)"
    return text


def _lessons_block(lessons):
    try:
        entries = []
        for item in top_lessons(lessons, k=3):
            text = _lesson_entry_text(item)
            if not text:
                continue
            block = "\nPast pitfalls this repo: " + "; ".join(entries + [text])
            if len(block) > 240:
                break
            entries.append(text)
        if entries:
            return "\nPast pitfalls this repo: " + "; ".join(entries)
    except Exception:
        pass
    return ""


def handle_event(event, data, state, lessons=None):
    """Returns (stdout_json_dict_or_None, new_state)."""
    runtime = _runtime(data)
    if event == "session_start":
        context = _protocol_for_runtime(runtime)
        if lessons:
            context += _lessons_block(lessons)
        out = {"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }}
        return out, state
    if event == "user_prompt":
        prompt = data.get("prompt", "")
        if FRESH_PROMPT_RE.search(prompt) and not state.get("prompt_freshness_fired"):
            state["prompt_freshness_fired"] = True
            return {"hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "Freshness task. Verify against current docs (context7/WebFetch official) before answering from memory.",
            }}, state
        return None, state
    if event == "pre_tool":
        tool = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}
        if tool == "Bash" and lessons and not state.get("lesson_warn_fired"):
            cmd = normalize_cmd(tool_input.get("command", ""))
            for item in lessons.get("lessons") or []:
                if (item.get("cmd") == cmd and int(item.get("n") or 0) >= 2 and
                        item.get("kind") != "note"):
                    state["lesson_warn_fired"] = True
                    msg = "This exact command failed %sx in past sessions." % item.get("n")
                    if item.get("fix"):
                        msg += " Fix then: `%s`." % item.get("fix")
                    msg += " Check midas:debug before retrying."
                    return _pre_tool_context(msg), state
        if tool == "Bash":
            out = _router_deny(tool_input.get("command", ""), state, runtime)
            if out is not None:
                return out, state
        if (tool == "Read" and not state.get("preread_gate_fired") and
                "limit" not in tool_input and "offset" not in tool_input):
            threshold = _read_nudge_limit()
            if _file_exceeds_lines(tool_input.get("file_path", ""), threshold):
                state["preread_gate_fired"] = True
                search_hint = "rg -n" if runtime == "codex" else "Grep -n"
                return _deny(
                    "File ~%d+ lines. Read with offset/limit or %s first. "
                    "Gate fires once per session." % (threshold, search_hint)), state
        path = tool_input.get("file_path", "")
        editing_existing = tool == "Edit" or (tool == "Write" and os.path.exists(path))
        unexplored = not _edit_target_explored(state, path)
        if editing_existing and unexplored and not state["edit_gate_fired"]:
            state["edit_gate_fired"] = True
            if runtime == "codex":
                msg = "Explore first: rg --files/rg -n, then a narrow read (midas:explore). Then retry this edit. Gate fires once per session."
            else:
                msg = "Explore first: Glob/Grep then narrow Read (midas:explore). Then retry this edit. Gate fires once per session."
            return _deny(msg), state
        return None, state
    if event == "post_tool":
        tool = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}

        if tool in ("Grep", "Glob"):
            _track_path(state.setdefault("explored_dirs", []),
                        tool_input.get("path") or data.get("cwd") or "", 20)

        # MCP retrieval tools (e.g. Cortex graph queries) count as exploration.
        # Session-global: MCP inputs carry no attributable path.
        if tool.startswith("mcp__") and MCP_EXPLORE_RE.search(tool):
            state["explored"] = True

        # MCP test runners / checkers count as verify (a tool may match both)
        if tool.startswith("mcp__") and MCP_VERIFY_RE.search(tool):
            state["edits_since_verify"] = 0

        if tool == "Read":
            state["reads"] += 1
            _track_path(state.setdefault("read_paths", []),
                        tool_input.get("file_path", ""), 50)
            content = _read_content(data)
            unbounded = "limit" not in tool_input and "offset" not in tool_input
            if (unbounded and not state["read_nudge_fired"] and
                    content.count("\n") + bool(content) > _read_nudge_limit()):
                state["read_nudge_fired"] = True
                if runtime == "codex":
                    msg = "Huge Read. Use rg -n, then a bounded read. Load midas:explore."
                else:
                    msg = "Huge Read. Use Grep -n then narrow Read. Load midas:explore."
                return _nudge(msg), state

        if tool in ("Edit", "Write"):
            state["edits_since_verify"] += 1

        if tool == "Bash":
            command = tool_input.get("command", "")
            resp = data.get("tool_response") or {}
            failed = bool(resp.get("is_error")) if isinstance(resp, dict) else False

            if _looks_like_verify(command):
                state["edits_since_verify"] = 0

            state["last_bash"] = command

            if failed:
                # Legacy fallback: live CC never delivers is_error (failures go
                # to post_tool_failure) but in-band runtimes may.
                return _handle_bash_failure(
                    state, lessons, command, _bash_error_text(data), _nudge)

            state["bash_fail_streak"] = 0
            state["last_fail_cmd"] = ""

            pending = state.get("pending_fail_cmd", "")
            if pending and lessons is not None and _same_command(command, pending):
                # loop-back confirmed: the failing command itself (arg-tweaked
                # ok) reran and passed. An identical rerun proves recovery but
                # teaches nothing — clear pending without a self-referential fix.
                if normalize_cmd(command) != normalize_cmd(pending):
                    _set_lesson_fix(lessons, pending, command)
                state["pending_fail_cmd"] = ""

    if event == "post_tool_failure":
        tool = data.get("tool_name", "")
        if tool != "Bash":
            return None, state
        if data.get("is_interrupt"):
            # user abort, not a command failure — never feeds streak or lessons
            return None, state
        tool_input = data.get("tool_input") or {}
        command = tool_input.get("command", "")
        err_text = str(data.get("error") or "")[:2000]
        return _handle_bash_failure(state, lessons, command, err_text, _failure_nudge)

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
    cwd = data.get("cwd")
    lessons = None
    before_lessons = None
    if event in ("session_start", "pre_tool", "post_tool", "post_tool_failure") and cwd:
        lessons = load_lessons(cwd)
        before_lessons = json.dumps(lessons, sort_keys=True)
    out, state = handle_event(event, data, state, lessons)
    save_state(sid, state)
    if lessons is not None and json.dumps(lessons, sort_keys=True) != before_lessons:
        save_lessons(cwd, lessons)
    if out is not None:
        print(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
