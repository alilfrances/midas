# Midas v2 Upgrade Plan — Self-Aware Agentic Scaffolding

> **For agentic workers (Codex):** Implement task-by-task in order. Steps use checkbox (`- [ ]`) syntax. Do NOT commit — coordinating agent reviews and commits. Run `python3 -m unittest discover -s tests -v` after every task.

**Goal:** Upgrade Midas from failure-pattern gates to a self-aware scaffold: deterministic tool routing (right tool every action), knowledge-freshness detection (never act on stale API memory), capability calibration (accept/decline honestly), and cross-session learning from past mistakes — all at net-negative token cost.

**Architecture principle (unchanged from v1):** Zero-dep Python stdlib hooks. No LLM calls, no network, no subprocess inside hooks. Every "intelligent" behavior is a deterministic pattern detector that fires a micro-nudge (≤30 tokens) at most once per session per class. Skills carry playbooks, cost nothing until loaded. New in v2: a tiny persistent lesson store keyed by project makes past failures actionable in future sessions — this is the "learning" mechanism, and it is pure pattern memory, not model magic.

**Why net tokens go DOWN, not up (accounting):**

| Piece | Cost | Offsetting saving |
|---|---|---|
| Protocol +1 line (capability rule) | ~+20 tok once | — |
| Bash router deny (3 classes, once each) | ~+25 tok each, failure-triggered | Prevents full-file `cat` dumps / unpaged `grep -r` floods — hundreds to thousands of tok each |
| Freshness nudge (once) | ~+25 tok | Prevents retry loops against stale API knowledge (each failed retry ≥100 tok) |
| Lesson injection at start (only when lessons exist) | ≤60 tok | Prevents repeating a failure sequence that previously cost a full thrash loop |
| Lesson match on PreToolUse (once) | ~+30 tok | Skips known-bad command + its error output |
| UserPromptSubmit hook | 0 tok when silent (99% of prompts) | — |
| New skills (scope) | 0 until loaded; +~15 tok description | — |

Worst-case unconditional new cost: ~55 tok/session (protocol lines 7+8 + scope skill description). Everything else failure-triggered, once-only, on-demand, or threshold-gated — and strictly cheaper than the failure it prevents. (See Task 7 for pushback/escalation/self-review costs.)

## Ecosystem Compatibility (verified against sibling plugins 2026-07-08)

Midas must work standalone AND alongside cortex, axon, tokenslim, caveman, codex-companion. Audit of their actual hook surfaces:

| Plugin | Hook surface | Interaction | Resolution |
|---|---|---|---|
| caveman | SessionStart + UserPromptSubmit (context injection only) | Additive context; no decisions | None needed. Midas nudge strings already caveman-compressed. |
| tokenslim | PostToolUse compressors on `Bash\|Read\|Edit\|Write\|Grep\|Glob\|mcp__.*` (rewrites via `updatedToolOutput`); PreToolUse Read guard (nudges unbounded reads >2000 lines) | (a) Parallel PostToolUse hooks each receive the ORIGINAL `tool_response` on stdin — tokenslim compression can never corrupt midas line-count/is_error detection. (b) Large-read advice overlaps: midas post-hoc at >400 lines, tokenslim pre-hoc at >2000. | Keep both (different thresholds, both once/session worst case). Make midas threshold env-tunable `MIDAS_READ_NUDGE_LINES` (default 400) so co-installed users can align or disable. |
| cortex | SessionStart context + MCP tools `cortex_query/overview/read_symbol/references/relations/impact/search_symbols` | All names match existing `MCP_EXPLORE_RE` → count as exploration for edit gate. | Already compatible. Add regression test with real cortex tool names. |
| axon | No hooks; MCP tools `localize, inspect, investigate, triage, spectrum, graph_context, repro, search, index, ...` | **BUG RISK**: most axon tool names do NOT match `MCP_EXPLORE_RE` → axon-driven investigation would not count as exploration → midas edit gate could wrongly deny the first fix mid-investigation. | Task 3 widens `MCP_EXPLORE_RE` with generic retrieval verbs (`localize\|inspect\|investigat\|triage\|spectrum\|context\|repro\|resolve\|docs`). Generic words — safe standalone, benefits any retrieval-style MCP. |
| codex-companion | Stop-time review gate (Stop hook, toggleable) | Two Stop blockers can stack → at most one extra turn each; both once-only; midas already respects `stop_hook_active` so no loops. | No change. Document in README. |

**Subagent note:** plugin PreToolUse hooks also fire inside subagents (cavecrew-investigator legitimately runs bare `grep` via Bash). Router state is keyed by `session_id`, shared with subagents → once-per-class cap is session-wide, so worst case is ONE redirected call across main thread + all agents. Compound/pipeline exemption (Task 3) keeps `codex exec ...`, `sh -c ...`, git hooks, and scripts unaffected.

**Standalone guarantee:** every mechanism is self-contained (stdlib, own state/data files with `midas-` prefix, own env vars `MIDAS_*`). Nudges referencing other tools always phrase conditionally ("context7 MCP if present, else WebFetch official docs"). Nothing assumes cortex/axon/tokenslim/caveman exist; nothing breaks when they do.

## Global Constraints (all inherited from v1, restated)

- Python 3 stdlib only. No pip installs. No network. No subprocess.
- Every hook silent-fails: any exception → exit 0, no output.
- Kill switch `MIDAS_DISABLE=1` exits all hooks immediately.
- Each nudge/gate fires at most ONCE per session per class (state-tracked).
- All model-facing strings caveman-compressed.
- State writes atomic (`tempfile` + `os.replace`); session_id sanitized.
- Pure-function hook logic: `handle_event(event, data, state) -> (out|None, state)`; lessons add a second pure layer `(event, data, state, lessons) -> (out|None, state, lessons)` — see Task 1 for exact shape.
- Respect `CLAUDE_CONFIG_DIR`; never hardcode `~/.claude`.
- Codex parity: hooks.json keeps `${CLAUDE_PLUGIN_ROOT:-${PLUGIN_ROOT:-.}}` pattern; both manifests updated together.

---

### Task 1: Persistent lesson store (cross-session memory)

**Files:**
- Modify: `hooks/midas_hook.py` (add lessons module section)
- Create: `tests/test_lessons.py`

**Storage location** (first that resolves):
1. `$CLAUDE_CONFIG_DIR/midas-data/`
2. `~/.claude/midas-data/`

Do **NOT** use `$CLAUDE_PLUGIN_DATA`: it resolves differently inside midas's hook vs a plain Bash-invoked `bin/midas-lesson` call (ambient / another plugin's dir), which splits the store across contexts and pollutes sibling plugins' data dirs. `$CLAUDE_CONFIG_DIR` is stable in both. (Original draft listed `$CLAUDE_PLUGIN_DATA` first — removed after verification caught cross-context split-brain + codex-dir pollution.)

`cwdhash = hashlib.sha1(os.path.realpath(cwd).encode()).hexdigest()[:12]`. **Realpath before hashing** so the hook (raw `cwd` field, possibly unresolved) and the CLI (`os.getcwd()`, resolved) agree on any symlinked path component. Missing cwd → no lesson features (silent no-op).

**Lesson file schema** (single JSON object):
```json
{
  "v": 1,
  "lessons": [
    {"kind": "thrash", "cmd": "<normalized failing command>", "err": "<first 80 chars of error>", "fix": "<command that later succeeded or empty>", "n": 2, "ts": 1751932800}
  ]
}
```

**Functions (all pure-ish, injectable base dir for tests):**
- `lessons_path(cwd, base_dir=None) -> str | None`
- `load_lessons(cwd) -> dict` — corrupt/missing → `{"v":1,"lessons":[]}`
- `save_lessons(cwd, lessons)` — atomic write; **cap 40 entries**, evict oldest `ts` first
- `record_lesson(lessons, kind, cmd, err="", fix="")` — dedupe by (kind, cmd): existing entry bumps `n` and `ts`, updates `fix` if newly provided
- `normalize_cmd(cmd)` — strip whitespace, collapse runs of spaces, truncate 120 chars
- `top_lessons(lessons, k=3) -> list` — most recent first; eligible: `kind == "note"` (always), else `n >= 2` or nonempty `fix`

- [x] **Step 1: Write failing tests** — `tests/test_lessons.py` covering: path resolution honors env override (use `unittest.mock.patch.dict(os.environ, ...)` or injectable base), roundtrip, corrupt file → empty, cap-40 eviction of oldest, dedupe bumps `n`, `top_lessons` filter/order (incl. `note` always eligible), missing cwd → `lessons_path` returns None.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.** Keep all lesson I/O inside `try/except` — a read-only filesystem must never break a session.
- [ ] **Step 4: Full suite passes.**

---

### Task 1b: `midas-lesson` CLI — model-initiated memory (declines, capability walls, deliberate notes)

**Files:**
- Create: `bin/midas-lesson` (executable, `#!/usr/bin/env python3`, no extension)
- Modify: `tests/test_lessons.py` (CLI tests via subprocess or import), `tests/test_packaging.py` (executable bit + shebang)

**Why:** hooks can't see model prose, so declines/capability walls are invisible to automatic recording. Plugin `bin/` dirs go on the Bash tool PATH — a tiny CLI lets the model deliberately record a lesson. Skills (Task 5) instruct when to call it.

**Behavior:** `midas-lesson "<text>"`
- Joins argv[1:] into text, collapses whitespace, truncates 160 chars. Empty text → exit 0, no-op.
- `MIDAS_DISABLE=1` → exit 0, no-op.
- Records via the same lessons module: `record_lesson(lessons, "note", text)` keyed by cwd (`os.getcwd()`), same store, same cap-40/LRU. Dedupe by text bumps `n`.
- Prints one line on success: `lesson saved` (so model gets confirmation); silent exit 0 on ANY error (read-only fs, etc.).
- Imports lessons functions from `../hooks/midas_hook.py` relative to the script's own resolved path (`os.path.realpath(__file__)`) — must work regardless of invocation cwd.

**Injection format for notes** (Task 2 session_start block): note entries render as bare text (no cmd/fix formatting), same 240-char total cap.

- [x] **Step 1: Failing tests** — records note; dedupe bumps n; empty arg no-op; MIDAS_DISABLE no-op; works when invoked from unrelated cwd; note appears in session_start pitfalls block.
- [ ] **Step 2: Verify fail. Step 3: Implement + `chmod +x`. Step 4: Full suite.** Packaging test asserts executable bit and shebang.

---

### Task 2: Wire lessons into events — record on failure, inject on start, warn on repeat

**Files:**
- Modify: `hooks/midas_hook.py`
- Modify: `tests/test_lessons.py` (add event-level tests), `tests/test_session_start.py`

**New state fields** (extend `default_state()`):
```python
"lesson_warn_fired": False,   # PreToolUse lesson match nudge, once
"pending_fail_cmd": "",       # last thrash-recorded command awaiting a fix
```

**Refactor:** `handle_event` gains optional `lessons` param: `handle_event(event, data, state, lessons=None) -> (out, state)`. Mutates `lessons` dict in place when recording; `main()` loads lessons lazily (only for events that use them: `session_start`, `pre_tool`, `post_tool`) and saves only when changed (dirty flag). Existing tests calling `handle_event(e, d, s)` must keep passing unchanged.

**Behavior:**
1. **Record on thrash** (post_tool Bash, when thrash nudge fires): `record_lesson(lessons, "thrash", normalize_cmd(cmd), err=first 80 chars of stderr/stdout error text)`; set `state["pending_fail_cmd"]`.
2. **Record fix** (post_tool Bash success): if `pending_fail_cmd` nonempty and this command succeeded AND (success command's first whitespace-token equals the failing command's first token OR success command matches `VERIFY_RE`) → set that lesson's `fix` to this normalized command, clear `pending_fail_cmd`. First-token guard stops unrelated commands (`ls` after thrash) being recorded as fixes; unrelated successes leave `pending_fail_cmd` in place.
3. **Inject at session_start**: if `top_lessons` nonempty, append to PROTOCOL output:
   `"\nPast pitfalls this repo: " + "; ".join("`{cmd}` failed{fix_part}" ...)` where `fix_part = ", fix: `{fix}`"` when present. Hard cap the whole block at 240 chars (truncate lesson list, never mid-entry).
4. **Warn on repeat** (pre_tool Bash — requires Task 3's Bash matcher): if `normalize_cmd(command)` exactly matches a lesson `cmd` with `n>=2` and not `lesson_warn_fired` → **allow but nudge** via `additionalContext` (permissionDecision "allow" + additionalContext): `"This exact command failed {n}x in past sessions.{ ' Fix then: `<fix>`.' if fix} Check midas:debug before retrying."`; set flag.

- [x] **Step 1: Failing tests** — thrash records lesson; success after thrash records fix; session_start with lessons appends block ≤240 chars and without lessons output identical to v1; pre_tool Bash match nudges once, allows, never denies; no-cwd input → all lesson paths silently skipped.
- [ ] **Step 2: Verify fail. Step 3: Implement. Step 4: Full suite.**

---

### Task 3: Deterministic tool router (right tool per action)

**Files:**
- Modify: `hooks/midas_hook.py` (pre_tool Bash branch), `hooks/hooks.json` + `.claude-plugin/plugin.json` (PreToolUse matcher `Edit|Write|Bash`)
- Create: `tests/test_router.py`

**Why:** "Pick most appropriate tool" made deterministic: catch the three highest-frequency wrong-tool patterns that waste tokens, redirect once each.

**New state fields:** `"router_fired": []` (list of fired class names).

**Behavior (pre_tool, tool_name == "Bash"):** Let `cmd = command.strip()`. **Never fire** when cmd contains `|`, `>`, `<`, `&&`, `;`, `$(` (pipelines/compounds are legit). Otherwise match classes in order; on first match whose class not in `router_fired`: append class, return deny with reason. Max one router deny per class per session.

| Class | Pattern (regex on cmd) | Deny reason |
|---|---|---|
| `read` | `^(cat|head|tail|less|more)\s+[^-]` | `Use Read tool (offset/limit) not {cmd0}. Retry with Read.` |
| `search` | `^(grep|rg|egrep|fgrep)\s` | `Use Grep tool not shell grep. Structured output, cheaper. Retry with Grep.` |
| `find` | `^find\s+\S+.*-name` | `Use Glob tool not find. Retry with Glob.` |

Deny shape identical to v1 `_deny`. Note in reason that gate fires once per class.

**Ordering with Task 2 lesson-warn and existing edit gate:** pre_tool dispatch order = lesson-warn (Bash) → router (Bash) → edit gate (Edit/Write). First producing output wins.

**Ecosystem fixes bundled here (see Ecosystem Compatibility table):**
- Widen `MCP_EXPLORE_RE` to also match retrieval-style MCP tools (axon et al.):
```python
MCP_EXPLORE_RE = re.compile(
    r"(query|search|read|grep|find|symbol|overview|references|relations|impact"
    r"|localize|inspect|investigat|triage|spectrum|context|repro|resolve|docs)",
    re.IGNORECASE)
```
- Make large-read nudge threshold env-tunable: `MIDAS_READ_NUDGE_LINES` (int, default 400, invalid value → 400). Lets tokenslim co-users align thresholds or effectively disable (`=999999`).

- [x] **Step 1: Failing tests** — each class denies once then allows; piped `cat f | jq .` never denied; `cat -n f` (flag) not denied by `[^-]` guard; `grep` inside `sh -c` compound not denied; Edit/Write path unaffected; `mcp__plugin_axon_axon__localize` and `mcp__plugin_cortex_cortex__cortex_query` both set `explored`; `MIDAS_READ_NUDGE_LINES=10` makes 20-line read nudge, `=abc` falls back to 400.
- [ ] **Step 2–4: fail → implement → full suite.** Also update both hook manifests' PreToolUse matcher to `Edit|Write|Bash` and validate JSON parses.

---

### Task 4: Knowledge-freshness gate (never act on stale API memory)

**Files:**
- Modify: `hooks/midas_hook.py`, `hooks/hooks.json`, `.claude-plugin/plugin.json`
- Create: `tests/test_freshness.py`

**New state fields:** `"freshness_fired": False`, `"prompt_freshness_fired": False`.

**Detector A — failure classifier (post_tool Bash failed):** error text (stderr+stdout, first 2000 chars) matches:
```
STALE_RE = re.compile(r"(ModuleNotFoundError|ImportError: cannot import|npm ERR!|ERESOLVE"
  r"|unknown option|unrecognized arguments?|no such option|deprecat(ed|ion)"
  r"|is not a function|has no attribute|does not provide an export"
  r"|requires? .{0,40}version|incompatible)", re.IGNORECASE)
```
If match and not `freshness_fired` → nudge once: `"Error smells like stale API knowledge (version/signature drift). Fetch current docs first — context7 MCP if present, else official docs via WebFetch — then fix."`; set flag. Fires independently of thrash nudge (different failure classes) but if both would fire on the same event, thrash wins (emit one nudge max per event).

**Detector B — prompt intent (new `user_prompt` event via UserPromptSubmit hook):** prompt matches
`re.compile(r"\b(latest|newest|up[- ]to[- ]date|current version|upgrade|migrat(e|ion)|deprecated|v?\d+\.\d+ (to|->) v?\d+)\b", re.IGNORECASE)`
and not `prompt_freshness_fired` → output `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "Freshness task. Verify against current docs (context7/WebFetch official) before answering from memory."}}`; set flag. **All other prompts: return None — zero output, zero tokens.** Never block prompts.

**Manifest wiring:** add to both `hooks/hooks.json` and `.claude-plugin/plugin.json`:
```json
"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT:-${PLUGIN_ROOT:-.}}/hooks/midas_hook.py\" user_prompt", "timeout": 5}]}]
```
(plugin.json uses plain `${CLAUDE_PLUGIN_ROOT}` matching its existing style.) Codex-runtime parity: if the Codex hook runner lacks UserPromptSubmit, the entry never fires — harmless; do not fork hooks.json.

- [x] **Step 1: Failing tests** — stale-error nudge once; thrash+stale same event → single nudge (thrash); plain prompt → None; freshness prompt → context once; second freshness prompt → None.
- [ ] **Step 2–4: fail → implement → full suite + JSON validation.**

---

### Task 5: Capability calibration (accept/decline honestly)

**Files:**
- Create: `skills/scope/SKILL.md`
- Modify: `hooks/midas_hook.py` (PROTOCOL), `tests/test_session_start.py` (budget)

**PROTOCOL — add line 7:**
```
7. Capability limits: missing tool/creds/device/network, or task outside scope — state upfront, offer nearest alternative, decline beats fake (midas:scope).
```
Adjust budget test: `len(PROTOCOL) < 1000` (was 800) with comment noting offset accounting from PLAN_V2 table.

**`skills/scope/SKILL.md`:**
```markdown
---
name: scope
description: Accept/decline calibration. Use when task may exceed capabilities — missing tools, creds, device, network, domain — or when tempted to fake progress.
---

# Scope

Before accepting task, 10-second audit:

1. **Tools** — needed tool/MCP/plugin present? Check available tools list, not memory. Missing: name it, offer nearest capable route.
2. **Access** — creds, network, device, filesystem perms? Can't reach = can't verify = say so upfront.
3. **Knowledge** — API/library moving fast or post-cutoff? Fetch current docs before acting (context7/WebFetch). Never code against remembered API when verify possible.
4. **Scale** — task needs hours/parallel work? State honest scope, propose slice or subagent split.

Decline pattern (use when capability genuinely missing):
- One line what blocks: "Cannot X: no Y."
- One line nearest alternative: "Can do Z instead" or "needs user: run `cmd`".
- Never fake it: no invented output, no pretend-verify, no silent partial delivery labeled done.
- Record it: `midas-lesson "declined X: no Y"` — next session in this repo starts knowing the limit.

Accept pattern: state assumptions + what will be verified how. Partial capability = accept with explicit boundary, not silent shrink.
```

Also add one line to `skills/debug/SKILL.md` (after the 4-failed-loops rule): `Stuck report done? Record: midas-lesson "X fails: <cause>" — persists across sessions.` And to Task 5 budget: skill body caps still ≤350 words each.

- [ ] **Steps: budget test adjust → implement → full suite.** Update `skills/midas/SKILL.md` overview to list new gates (router, freshness, lessons, scope) — keep ≤ current length + 6 lines.

---

### Task 6: Docs, versions, packaging

**Files:**
- Modify: `README.md`, `.claude-plugin/plugin.json` (add `"version": "0.2.0"`), `.codex-plugin/plugin.json` (`0.2.0`), `tests/test_packaging.py` if it asserts manifests

- [ ] **Step 1:** README: update What You Get table (v1 rows + router, freshness, lessons, scope), Token Budget section with the accounting table from this plan's header, note lesson store location + `rm -rf` of data dir as reset, `MIDAS_DISABLE=1` still kills everything including lesson writes and `midas-lesson` CLI. Document `midas-lesson` under a "Self-learning" section: automatic (thrash+fix pairs) vs deliberate (`midas-lesson "note"`), bloat bounds (cap 40/project, top-3/240-char injection), exact-match retrieval honesty. Add "Plays well with others" section: coexistence notes for tokenslim (threshold env var), stop-gate stacking with other Stop hooks, MCP retrieval tools counted as exploration, subagent behavior — plus explicit statement that none of these plugins are required.
- [ ] **Step 2:** Version bumps both manifests; validate all JSON files parse.
- [ ] **Step 3:** Full suite + smoke:
```bash
python3 -m unittest discover -s tests -v
echo '{"session_id":"smoke","cwd":"/tmp/x"}' | python3 hooks/midas_hook.py session_start
echo '{"session_id":"smoke","prompt":"upgrade react 17 to 18"}' | python3 hooks/midas_hook.py user_prompt
echo '{"session_id":"smoke","tool_name":"Bash","tool_input":{"command":"cat foo.py"}}' | python3 hooks/midas_hook.py pre_tool
MIDAS_DISABLE=1 sh -c 'echo "{}" | python3 hooks/midas_hook.py user_prompt'; echo "exit=$?"
echo 'not json' | python3 hooks/midas_hook.py user_prompt; echo "exit=$?"
```
Expected: suite green; session_start prints protocol JSON; user_prompt prints freshness context; pre_tool prints router deny; disabled + garbage inputs → silent exit 0.

---

### Task 7: Self-improvement — calibrated pushback, recurring-pitfall escalation, on-demand self-review

**Files:**
- Modify: `hooks/midas_hook.py` (PROTOCOL line 8, lesson-render suffix), `skills/ask/SKILL.md`, `skills/midas/SKILL.md`, `tests/test_session_start.py`, `tests/test_lessons.py`

**7a — Calibrated pushback (protocol + ask skill).** Hooks can't argue; the model does. Midas sets the rule once.

PROTOCOL — add line 8:
```
8. Evidence contradicts user instruction: say so once, one line, cite evidence (file:line, test output). Explicit override → comply, note objection in report. Never argue twice, never silently comply against evidence.
```
Budget test becomes `len(PROTOCOL) < 1100` (was 1000; accounting table updated below).

`skills/ask/SKILL.md` — append section:
```markdown
## Pushback

User instruction vs evidence conflict: one-line objection + evidence, then user decides. Rules:
- Once per disagreement. Restated override = final, comply and move on.
- Objection needs artifact: file:line, failing test, doc quote. No artifact = no objection, just do it.
- Comply-under-protest goes in final report, one line, no editorializing.
```

**7b — Recurring-pitfall escalation (lesson render).** In the session_start pitfalls block (Task 2), any lesson with `n >= 4` gets suffix `" (recurring: propose repo rule)"`. Same 240-char total cap — suffix counts toward it; truncation still never mid-entry. Deterministic, data-backed, fires only on cross-session repeat offenders. Model reaction (drafting the CLAUDE.md rule / skill suggestion) is its own judgment; midas only flags.

**7c — On-demand self-review (`/midas` skill).** Append to `skills/midas/SKILL.md`:
```markdown
## Self-review (on demand)

User asks how session went or /midas review:
1. Locate lesson store: $CLAUDE_PLUGIN_DATA, else $CLAUDE_CONFIG_DIR/midas-data, else ~/.claude/midas-data — file lessons-<sha1(cwd)[:12]>.json. Session state: $TMPDIR/midas-<session_id>.json.
2. Read both. Report: gates/nudges fired this session, top recurring lessons (n desc).
3. Suggest ONE concrete improvement max: CLAUDE.md rule, repo skill, or prompt habit — tied to highest-n lesson. No lesson n>=2 → say "no recurring failures, nothing to suggest".
Zero cost until invoked — stats live in files, not context.
```
Skill body still ≤350 words total after append; trim overview lines if needed.

**Token accounting delta:** unconditional cost rises ~20 tok (protocol line 8); escalation suffix ~8 tok, rare; self-review 0 until invoked. Header table row: worst-case unconditional new cost v2 total ≈ 55 tok/session.

- [x] **Step 1: Failing tests** — protocol contains "override" + budget <1100; lesson with `n=4` renders suffix, `n=3` doesn't; suffix respects 240 cap.
- [ ] **Step 2–4: fail → implement → full suite.** Verify both skill files still parse frontmatter and ≤350 words.

---

## Explicitly Out of Scope (v2)

- No LLM-in-hook, no network calls from hooks, no embeddings/semantic matching — lessons are exact-normalized-string memory by design.
- No per-plugin awareness of which MCP servers user has (hooks can't enumerate tools); nudges say "context7 if present, else WebFetch".
- No automatic tool substitution via `updatedInput` (silent rewrites confuse the model's own state) — deny-with-reason only.
- No transcript parsing (token-expensive, brittle).

## Design Decisions (defaults chosen; flag to user at review)

1. **Lesson storage**: `$CLAUDE_PLUGIN_DATA` → `$CLAUDE_CONFIG_DIR/midas-data` → `~/.claude/midas-data`. Per-project via cwd hash. Not in repo (no `.midas/` dir pollution, works in read-only checkouts).
2. **Router = deny once per class** (not nudge): deny forces the cheaper tool immediately; once-per-class cap keeps it non-annoying. Pipelines/compound commands always exempt.
3. **Lesson injection cap 240 chars / top 3** — worst case ~60 tokens, only in repos with recorded failures.
4. **Freshness prompt detector** errs conservative (narrow regex) — silence on 99% of prompts.
5. **Coexistence over configuration**: no cross-plugin detection attempted (hooks can't see other plugins). Instead: parallel-safe reads of original payloads, `MIDAS_*`-prefixed env/state/data namespaces, env-tunable read threshold, conditional phrasing in nudges. Same code path standalone and co-installed.
6. **MCP explore regex widened with generic verbs**, not plugin-specific names — benefits axon/cortex without hardcoding them; standalone users with any retrieval MCP benefit equally.
