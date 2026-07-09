# Midas v3 Hardening Plan — Fix Live Failure Detection + Gate Precision

> Historical roadmap only. This file documents the v3 correction plan and can intentionally describe stale or pre-fix behavior. It is not the shipped `0.3.1` contract; use the live hook code plus `README.md` and `skills/midas/SKILL.md`.

> **For agentic workers (Codex):** Implement task-by-task in order. Steps use checkbox (`- [ ]`) syntax. Do NOT commit — coordinating agent reviews and commits. Run `python3 -m unittest discover -s tests -v` after every task.

**Goal:** Fix the P1 live-detection break (failing Bash commands are invisible to midas in real Claude Code sessions) and tighten four gate-precision gaps found in a live code review. No new features — this is a correctness release.

**Architecture principle (unchanged):** Zero-dep Python stdlib hooks. No LLM calls, no network, no subprocess inside hooks. Deterministic detectors, micro-nudges ≤30 tok, once per session per class. Silent-fail everywhere.

## Ground truth — how live Claude Code actually reports Bash failures (verified against installed CC v2.1.42 bundle, 2026-07-09)

The v2 code keys Bash failure off `tool_response.is_error` in the `PostToolUse` event. **Live Claude Code never delivers that.** Verified in the shipped `cli.js`:

1. On non-zero exit the Bash tool **throws** (`if (isError) throw new ShellError(stdout, stderr, code, interrupted)`). A failing Bash never produces a successful tool result.
2. `PostToolUse` hooks fire **only for tools that succeed**. Failing tools are routed to a **separate hook event: `PostToolUseFailure`**, with input:
   ```json
   {
     "hook_event_name": "PostToolUseFailure",
     "tool_name": "Bash",
     "tool_input": {"command": "..."},
     "tool_use_id": "...",
     "error": "Exit code 2\n<stderr>\n<stdout>",
     "is_interrupt": false
   }
   ```
   (plus the common fields `session_id`, `cwd`, `transcript_path`.) Note there is **no `tool_response`** and no structured exit code — `error` is one preformatted string beginning `Exit code N`, with stderr/stdout appended. `is_interrupt: true` means the user aborted the command — that is NOT a command failure.
3. `PostToolUseFailure` is a first-class event in CC's hook schema: it accepts a matcher (tool name), and its `hookSpecificOutput` supports `additionalContext` with `hookEventName: "PostToolUseFailure"` — so nudges work from it.
4. On CC versions predating `PostToolUseFailure`, an unknown event entry in the manifest simply never fires — harmless (same parity argument as v2's UserPromptSubmit note). Same for the Codex runtime: do not fork `hooks/hooks.json`.

**Consequence:** thrash detection, freshness-on-error, and automatic lesson recording (thrash + fix capture) are all dead in live sessions today. The 76 green tests fake `{"is_error": true}` — a test-vs-reality gap. Tasks 1–2 fix this; Tasks 3–6 fix precision gaps; Task 7 is docs/packaging.

## Global Constraints (inherited, restated)

- Python 3 stdlib only. No pip installs. No network. No subprocess.
- Every hook silent-fails: any exception → exit 0, no output.
- Kill switch `MIDAS_DISABLE=1` exits all hooks immediately.
- Each nudge/gate fires at most ONCE per session per class (state-tracked).
- All model-facing strings caveman-compressed.
- State writes atomic; session_id sanitized; state schema merged forward by `load_state` (new fields MUST be added to `default_state()`).
- Codex parity: `hooks/hooks.json` keeps `${CLAUDE_PLUGIN_ROOT:-${PLUGIN_ROOT:-.}}`; both manifests updated together; unknown events never fire on runtimes that lack them.

---

### Task 1 (P1): `post_tool_failure` event — restore live failure detection

**Files:**
- Modify: `hooks/midas_hook.py`, `hooks/hooks.json`, `.claude-plugin/plugin.json`
- Modify: `tests/test_post_tool.py`, `tests/test_freshness.py`, `tests/test_lessons.py`
- Create: `tests/test_post_tool_failure.py`

**New state fields** (extend `default_state()`):
```python
"last_fail_cmd": "",   # last failing Bash command (replaces overloading last_bash for failures)
```
Keep `last_bash` and `bash_fail_streak` keys (schema merge safety) but streak logic now lives on `last_fail_cmd`.

**New event `post_tool_failure`** in `handle_event` (and in `main()`'s lessons-loading list `("session_start", "pre_tool", "post_tool", "post_tool_failure")`):

1. Only act when `tool_name == "Bash"`. Extract `command = tool_input.get("command", "")` and `err_text = str(data.get("error") or "")[:2000]`.
2. **Interrupt guard:** if `data.get("is_interrupt")` is truthy → return `(None, state)` untouched. A user abort is not a command failure; it must not feed the streak or lessons.
3. **Streak:** if `_same_command(command, state["last_fail_cmd"])` (Task 2 helper; for this task exact `normalize_cmd` equality is fine, Task 2 upgrades it) → `bash_fail_streak += 1`; else `last_fail_cmd = command`, `bash_fail_streak = 1`.
4. **Thrash nudge** (unchanged semantics): streak ≥ 2 and not `thrash_nudge_fired` → set flag, `record_lesson(lessons, "thrash", normalize_cmd(command), err=err_text[:80])`, set `pending_fail_cmd`, return failure-nudge `"Same command failed twice. Stop retrying; load midas:debug."`.
5. **Freshness:** else if `STALE_RE.search(err_text)` and not `freshness_fired` → set flag, return the existing freshness nudge. (One nudge max per event; thrash wins — unchanged rule.)
6. **Nudge shape:** failure nudges must use the failure event name:
   ```python
   def _failure_nudge(msg):
       return {"hookSpecificOutput": {"hookEventName": "PostToolUseFailure",
                                      "additionalContext": msg}}
   ```

**`post_tool` (success path) cleanup:**
- Bash success now means: reset `bash_fail_streak = 0` and `last_fail_cmd = ""`, keep `last_bash = command`, keep verify-reset (`_looks_like_verify`) and fix-capture (`pending_fail_cmd` → `_set_lesson_fix`) exactly as they are — fix-capture already lived on the success side and is correct once failures actually populate `pending_fail_cmd`.
- **Keep** the legacy `is_error` branch as a fallback (`failed = bool(resp.get("is_error"))`) for runtimes that do deliver error responses through `post_tool` — it is dead weight on live CC but harmless, and it keeps the Codex runtime covered if its runner reports failures in-band. When the legacy branch detects failure it should route through the same streak/thrash/freshness logic (extract a shared helper, e.g. `_handle_bash_failure(state, lessons, command, err_text)` returning `(out, state)`, called from both events — nudge wrapper passed in or selected by event).

**Manifest wiring** — add to BOTH `.claude-plugin/plugin.json` (with `${CLAUDE_PLUGIN_ROOT}`, `midas_hook.py`) and `hooks/hooks.json` (with `${CLAUDE_PLUGIN_ROOT:-${PLUGIN_ROOT:-.}}`, `codex_hook.py`):
```json
"PostToolUseFailure": [
  {"matcher": "Bash",
   "hooks": [{"type": "command",
              "command": "python3 \"<root-pattern>/hooks/<entrypoint>\" post_tool_failure",
              "timeout": 5}]}
]
```

**Tests:**
- `tests/test_post_tool_failure.py` — use the REAL payload shape verbatim (documented in the Ground truth section above: top-level `error` string starting `"Exit code 2"`, `is_interrupt`, NO `tool_response`). Cover: same command failing twice → thrash nudge once with `hookEventName == "PostToolUseFailure"` + lesson recorded with `err` from the error string; different commands failing → no nudge; `is_interrupt: true` twice → streak stays 0, no lesson; stale error text (`ModuleNotFoundError` inside `error`) → freshness nudge once; thrash+stale same event → thrash only; failure then Bash success with same first token → lesson `fix` captured and `pending_fail_cmd` cleared; non-Bash tool_name → no-op.
- Rewrite the `is_error`-based cases in `test_post_tool.py` / `test_freshness.py` / `test_lessons.py` to drive the new `post_tool_failure` event; keep ONE legacy test per behavior asserting the `is_error` fallback still works through `post_tool` (labeled as legacy-runtime coverage).
- Manifest test (extend `test_packaging.py`): both manifests parse, both contain a `PostToolUseFailure` entry invoking `post_tool_failure`.

- [x] **Step 1: Write failing tests.**
- [x] **Step 2: Run, verify fail.**
- [x] **Step 3: Implement.**
- [x] **Step 4: Full suite passes.**

---

### Task 2 (P4): Fuzzy thrash matching — catch arg-tweaked retries

**Files:**
- Modify: `hooks/midas_hook.py`
- Modify: `tests/test_post_tool_failure.py`

**Why:** exact-string matching means `pytest x` → `pytest x -v` never trips thrash; arg-tweaking retries are the dominant real thrash pattern.

**Behavior:** add
```python
import difflib

def _same_command(a, b):
    na, nb = normalize_cmd(a), normalize_cmd(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if _first_token(na) != _first_token(nb):
        return False
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.8
```
Use it ONLY for the failure-streak comparison in `post_tool_failure` (step 3 of Task 1). Lesson store keys and the pre_tool lesson-warn stay exact-match (retrieval honesty — a lesson must quote the literal command that failed). The lesson recorded on thrash uses the CURRENT (latest) command form.

**Tests:** `pytest x` fail then `pytest x -v` fail → thrash fires; `pytest a` fail then `mypy a` fail → no thrash (different first token); `pytest tests/test_a.py` then `pytest tests/test_b.py` → fires only if ratio ≥ 0.8 (assert actual helper behavior, pick fixtures on the right side of the threshold); empty command → never matches.

- [x] **Steps 1–4: failing tests → verify fail → implement → full suite.**

---

### Task 3 (P3): Per-path edit gate — explored means *this target*, not *anything ever*

**Files:**
- Modify: `hooks/midas_hook.py`
- Modify: `tests/test_pre_tool.py`, `tests/test_post_tool.py`

**Why:** `explored` is a session-global bool: read file A, then blind-edit unexplored file B passes. The gate only ever guards the first blind edit of a session.

**New state fields:**
```python
"read_paths": [],      # realpaths of files Read this session (cap 50, FIFO)
"explored_dirs": [],   # realpaths of dirs covered by Grep/Glob this session (cap 20, FIFO)
```
Keep `explored` and `reads` keys: `explored` remains the session-global flag set ONLY by MCP retrieval tools (path cannot be attributed, so MCP exploration keeps unlocking everything — cortex/axon compatibility per PLAN_V2 ecosystem table). `reads` stays as a counter (harmless, other logic may read it) but no longer unlocks the gate globally.

**post_tool changes:**
- `Read`: append `os.path.realpath(file_path)` to `read_paths` (dedupe, cap 50 oldest-out).
- `Grep`/`Glob`: append `os.path.realpath(tool_input.get("path") or data.get("cwd") or "")` to `explored_dirs` (skip empty; dedupe; cap 20). A Grep with no `path` searches cwd → cwd counts as explored.
- MCP retrieval (`MCP_EXPLORE_RE`): unchanged, sets `explored = True`.

**pre_tool gate rewrite:** for `Edit`, or `Write` to an existing path, compute `target = os.path.realpath(file_path)`. The edit is **explored** when ANY of:
1. `target in read_paths`;
2. any `d in explored_dirs` where `target` is under `d` (`os.path.commonpath` or prefix check with `os.sep` guard — beware `/a/b` vs `/a/bc`);
3. `state["explored"]` is True (MCP session-global).

Not explored and not `edit_gate_fired` → deny once per session (flag + message unchanged — the once-per-session budget cap from Global Constraints stays; only the trigger becomes accurate).

**Tests:** read A then edit A → allowed; read A then edit B → denied; Grep in `/repo/src` then edit `/repo/src/x.py` → allowed; Grep in `/repo/src` then edit `/repo/srcx/y.py` → denied (prefix trap); MCP cortex query then edit anywhere → allowed; caps enforced (51st read evicts oldest); symlinked read path matches realpath edit target; second blind edit after gate fired → allowed (once-per-session unchanged).

- [x] **Steps 1–4: failing tests → verify fail → implement → full suite.**

---

### Task 4 (P5): MCP test runners count as verify

**Files:**
- Modify: `hooks/midas_hook.py`
- Modify: `tests/test_stop.py` (or `test_post_tool.py`, wherever verify-reset lives)

**Why:** `VERIFY_RE` only matches shell commands. Repos that verify via an MCP tool (e.g. `mcp__ci__run_tests`) never reset `edits_since_verify`, so the stop gate nags for a check that already ran.

**Behavior:** in `post_tool`, for MCP tools:
```python
MCP_VERIFY_RE = re.compile(r"(run_tests?|_tests?\b|verify|lint|typecheck|check|build|compile)", re.IGNORECASE)

if tool.startswith("mcp__") and MCP_VERIFY_RE.search(tool):
    state["edits_since_verify"] = 0
```
Keep the existing MCP-explore branch; a tool may match both (both effects apply). Token choice is deliberately narrower than the explore regex — do NOT include bare `test` (would match `mcp__foo__latest_docs` via `test`? no — but it WOULD match `mcp__contest__…`; the anchored `run_tests?`/`_tests?\b` forms avoid that class of false positive).

**Tests:** `mcp__ci__run_tests` after 2 edits → `edits_since_verify == 0` and stop gate silent; `mcp__foo__typecheck` resets; `mcp__cortex__cortex_query` does NOT reset; shell `pytest` path unchanged.

- [x] **Steps 1–4: failing tests → verify fail → implement → full suite.**

---

### Task 5 (P6): Router tightening — flag-first args and bare find

**Files:**
- Modify: `hooks/midas_hook.py`
- Modify: `tests/test_router.py`

**Why:** `cat -n f` evades `^cat\s+[^-]`; `find /x -type f` (no `-name`) evades the find pattern.

**Behavior:** update BOTH `ROUTER_PATTERNS` and `CODEX_ROUTER_PATTERNS`:
- `read` class: `^(cat|head|tail|less|more)\b` with an explicit exemption — do not fire when the command matches `^tail\s+.*-[fF]\b` (`tail -f`/`-F` is a legit follow, Read can't replace it). Implementation: check the exemption regex before the class match, or bake it in: `^(?!tail\s+.*-[fF]\b)(cat|head|tail|less|more)\s+\S`.
- `find` class: `^find\s+\S+.*-(name|iname|path|ipath|regex|type)\b` — still requires a filter predicate so bare `find /x` (rare, often exploratory) stays exempt, but `-type`-only walks are caught.
- Compound-token exemption (`|`, `>`, `&&`, …) unchanged and still checked first.

**Tests:** `cat -n f` denied (read class, once); `head -20 f` denied; `tail -f log` NOT denied; `tail -n 5 log` denied; `find src -type f` denied (find class); `find .` alone NOT denied; compound `cat f | jq .` still exempt; Codex runtime variants mirror all of the above.

- [x] **Steps 1–4: failing tests → verify fail → implement → full suite.**

---

### Task 6 (P7): Pre-emptive large-read guard (PreToolUse Read)

**Files:**
- Modify: `hooks/midas_hook.py`, `hooks/hooks.json`, `.claude-plugin/plugin.json` (PreToolUse matcher → `Edit|Write|Bash|Read`)
- Modify: `tests/test_pre_tool.py`

**Why:** the post_tool large-read nudge fires after the tokens are already spent. A PreToolUse check can refuse the unbounded Read before the spend.

**New state field:** `"preread_gate_fired": False`.

**Behavior (pre_tool, tool_name == "Read"):** when `limit` and `offset` are both absent, `preread_gate_fired` is False, and the target file exceeds the threshold → set flag, deny once per session:
`"File ~{n}+ lines. Read with offset/limit or Grep -n first. Gate fires once per session."`
- Threshold: reuse `MIDAS_READ_NUDGE_LINES` (default 400).
- Line counting must be bounded and silent-fail: open `file_path` in binary, read in 64KB chunks counting `\n`, **stop as soon as count exceeds threshold** (report `threshold+`), hard-cap total bytes scanned at 5MB; any OSError / missing file / directory → no deny. A NUL byte in the first chunk (binary file) → no deny (Read handles images/binaries itself).
- Ordering in pre_tool dispatch: lesson-warn (Bash) → router (Bash) → **preread (Read)** → edit gate (Edit/Write).
- Keep the post_tool nudge as-is (it still covers the reads that happen after the one allowed deny; different flag, both once/session, consistent with tokenslim coexistence notes).

**Tests:** unbounded Read of a 500-line temp file → deny once, second unbounded Read → allowed; Read with `limit` → never denied; 100-line file → never denied; nonexistent path → never denied; binary file with NULs → never denied; `MIDAS_READ_NUDGE_LINES=10` → 20-line file denied; matcher updated in both manifests (packaging test).

- [x] **Steps 1–4: failing tests → verify fail → implement → full suite + JSON validation.**

---

### Task 7: Docs, versions, packaging

**Files:**
- Modify: `README.md`, `.claude-plugin/plugin.json` (`"version": "0.3.0"`), `.codex-plugin/plugin.json` (`0.3.0`), `tests/test_packaging.py`

- [x] **Step 1:** README: document the `PostToolUseFailure` requirement ("thrash/freshness/lesson auto-capture need Claude Code ≥ 2.1.x with the PostToolUseFailure hook event; on older versions those features are inert, everything else works"), update the What You Get table (per-file edit gate, pre-emptive read guard, MCP verify), note `tail -f` exemption.
- [x] **Step 2:** Version bump both manifests; validate every JSON file parses.
- [x] **Step 3:** Full suite + smoke:
```bash
python3 -m unittest discover -s tests -v
echo '{"session_id":"smoke","cwd":"/tmp/x","tool_name":"Bash","tool_input":{"command":"pytest -q"},"error":"Exit code 2\nFAILED tests/x.py","is_interrupt":false}' | python3 hooks/midas_hook.py post_tool_failure
echo '{"session_id":"smoke","cwd":"/tmp/x","tool_name":"Bash","tool_input":{"command":"pytest -q"},"error":"Exit code 2\nFAILED tests/x.py","is_interrupt":false}' | python3 hooks/midas_hook.py post_tool_failure
echo '{"session_id":"smoke","tool_name":"Bash","tool_input":{"command":"cat -n foo.py"}}' | python3 hooks/midas_hook.py pre_tool
MIDAS_DISABLE=1 sh -c 'echo "{}" | python3 hooks/midas_hook.py post_tool_failure'; echo "exit=$?"
echo 'not json' | python3 hooks/midas_hook.py post_tool_failure; echo "exit=$?"
```
Expected: suite green; second `post_tool_failure` prints the thrash nudge with `hookEventName":"PostToolUseFailure"`; pre_tool prints read-class router deny; disabled + garbage → silent exit 0.

---

## Explicitly Out of Scope (v3)

- No structured exit-code parsing from the `error` string beyond passing it to `STALE_RE`/lesson `err` — CC gives one preformatted string; parsing `Exit code N` adds nothing the detectors need.
- No per-file edit-gate firing (once per session per class stands — v3 fixes the trigger, not the budget).
- No fuzzy matching in the lesson store or pre_tool lesson-warn — lessons stay exact-normalized-string memory (retrieval honesty).
- No transcript parsing; no `updatedInput` rewrites; no network/subprocess in hooks.

## Design Decisions (defaults chosen; flag to coordinating agent at review)

1. **New event over field-sniffing:** live CC routes tool failures to `PostToolUseFailure` and never populates `tool_response.is_error` in `PostToolUse` — registering the real event is the fix; keeping the legacy `is_error` branch costs nothing and covers non-CC runtimes.
2. **`is_interrupt` excluded from streaks:** a user abort says nothing about the command.
3. **Thrash similarity = same first token + difflib ratio ≥ 0.8:** deterministic, stdlib, catches arg tweaks without conflating `pytest a.py` / `mypy a.py`.
4. **MCP exploration stays session-global** in the per-path edit gate: MCP tool inputs don't reliably carry paths; cortex/axon compatibility outweighs precision there.
5. **Pre-read guard denies (router-style) rather than nudges:** consistent with design decision 2 of PLAN_V2 — deny forces the cheap path immediately; once-per-session cap keeps it non-annoying; bounded chunked line-count keeps the hook O(file prefix) with a 5MB ceiling.
