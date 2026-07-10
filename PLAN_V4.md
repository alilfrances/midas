# Midas v4 Plan — Fix-Capture Loop-Back, Honest CLI Confirmation, Router Audit

> **For agentic workers (Codex):** Implement task-by-task in order. Steps use checkbox (`- [ ]`) syntax. Do NOT commit — coordinating agent reviews and commits. Run `python3 -m unittest discover -s tests -v` after every task.

**Goal:** Close two verified defects from an external review (false fix-capture, false `lesson saved` confirmation) and lock in the Bash-router coverage rule with a regression matrix. Small correctness release on top of v0.3.0.

**Review triage (coordinator-verified against v0.3.0 on main, 2026-07-10):**

| Review point | Verdict | Evidence |
|---|---|---|
| Large-Read nudge reactive, should be PreToolUse | **Stale — already shipped** | v0.3.0 Task 6: `preread_gate_fired` + `_file_exceeds_lines` deny unbounded Reads of 400+ line files pre-spend; post-hoc nudge kept as backstop by design. No work. |
| No loop-back verification that fix landed | **Valid** (as fix-capture defect) | Live repro: after `pytest -q` thrash, a successful `pytest --version` was recorded as `fix` (first-token match); after `python app.py` thrash, unrelated `make lint` was recorded as `fix` (VERIFY_RE match). Nothing confirms the failing command actually works now. → Task 1 |
| `midas-lesson` prints confirm without verifying write | **Valid** | Live repro: with the store dir unwritable (`CLAUDE_CONFIG_DIR` routed through a plain file), CLI still printed `lesson saved`, exit 0, nothing written. `save_lessons` swallows all errors and returns nothing. Hook-side silent-fail is BY DESIGN and stays; only the CLI's explicit confirmation is dishonest. → Task 2 |
| Router coverage inconsistent (`find` spotty) | **Mostly working as designed** | Audit matrix: read class (cat/head/tail/less/more incl. flag-first, `tail -f/-F` exempt) and search class (grep/rg/egrep/fgrep) are tight; compounds exempt. `find` denies only `-name/-iname/-path/-ipath/-regex/-type` — the predicates Glob CAN replace. `-mtime/-size/-empty/-newer/-perm/-user/-maxdepth` are deliberately allowed: Glob has no metadata filtering, so "Use Glob" would be wrong guidance. One true gap: `find -name '*.py'` with no path arg evades. → Task 3 (encode rule + matrix, close the no-path gap, document) |

## Global Constraints (inherited, restated)

- Python 3 stdlib only. No pip installs. No network. No subprocess.
- Every HOOK silent-fails: any exception → exit 0, no output. (The `midas-lesson` CLI may print an honest one-line failure notice — it is model-invoked, not session-critical, and decline-beats-fake applies. It still always exits 0.)
- Kill switch `MIDAS_DISABLE=1` exits hooks and CLI immediately.
- Each nudge/gate fires at most ONCE per session per class.
- New state fields MUST be added to `default_state()`.
- Both manifests updated together when hooks change (none change in v4).

---

### Task 1: Fix-capture requires loop-back confirmation

**Files:**
- Modify: `hooks/midas_hook.py` (post_tool Bash success branch, `_set_lesson_fix`)
- Modify: `tests/test_post_tool_failure.py`, `tests/test_lessons.py`

**Current defect:** on Bash success with `pending_fail_cmd` set, the fix is captured when the success command shares a first token OR matches `VERIFY_RE`. Both gates capture non-fixes (`pytest --version`; unrelated `make lint`).

**New rule — a fix is captured only when the failure demonstrably no longer reproduces:** the success command must be `_same_command(command, pending)` (existing fuzzy helper: exact, else same first token + difflib ratio ≥ 0.8). That means the failing command itself — possibly with tweaked args — ran and succeeded. Nothing else counts.

1. Replace the gate `_first_token(command) == _first_token(pending) or _looks_like_verify(command)` with `_same_command(command, pending)`.
2. In `_set_lesson_fix`, do NOT store a fix identical to the lesson's `cmd` (normalized equality) — an identical rerun that now passes proves recovery but teaches nothing; clear `pending_fail_cmd`, bump nothing, leave `fix` empty.
3. `pending_fail_cmd` is still cleared only when the confirmation fires (unrelated successes leave it pending — unchanged).
4. Keep `_looks_like_verify` verify-reset behavior untouched — this task changes only fix CAPTURE, not the stop gate.

**Fixture calibration (verify ratios empirically in the test, as v3 did):** `pytest -q` → `pytest -q -v` must capture (ratio ≈ 0.84); `pytest -q` → `pytest --version` must NOT (ratio ≈ 0.72, below 0.8); `python app.py` → `make lint` must NOT (different first token); `pytest -q` → identical `pytest -q` success clears pending, records no fix.

- [ ] **Step 1: Write failing tests** for the four fixtures above plus: pending survives an unrelated `ls` success; lesson `err` untouched by confirmation.
- [ ] **Step 2: Run, verify fail. Step 3: Implement. Step 4: Full suite passes.**

---

### Task 2: `midas-lesson` confirms only verified writes

**Files:**
- Modify: `hooks/midas_hook.py` (`save_lessons` return value), `bin/midas-lesson`
- Modify: `tests/test_lessons.py`

**Behavior:**
1. `save_lessons(cwd, lessons, base_dir=None) -> bool` — True only when the atomic write completed (`os.replace` returned); every failure path (inner and outer except) returns False. Hook call sites ignore the return value — hook silent-fail semantics unchanged.
2. `bin/midas-lesson`: after `save_lessons`, VERIFY by re-loading: `check = load_lessons(cwd)` and confirm an entry with `kind == "note"` and the normalized text exists. Print `lesson saved` only when save returned True AND the re-read confirms. Otherwise print `lesson not saved (store unwritable)` and exit 0. (Model-facing honesty: the model called the CLI deliberately; a silent or false confirm both mislead. Exit stays 0 — never break the session.)
3. `MIDAS_DISABLE=1` and empty-text behavior unchanged (silent no-op).

**Tests:** unwritable store via base-dir-through-a-plain-file (the live repro: create a file, pass its path as the lessons base dir / `CLAUDE_CONFIG_DIR`) → CLI prints `lesson not saved (store unwritable)`, exit 0, no file created; happy path still prints `lesson saved` and the note round-trips; `save_lessons` returns True on success and False when `os.makedirs`/write fails; hook events still emit nothing on store failure (silent-fail regression guard).

- [ ] **Step 1: Failing tests. Step 2: Verify fail. Step 3: Implement. Step 4: Full suite.**

---

### Task 3: Router coverage — encode the rule, close the no-path `find` gap, regression matrix

**Files:**
- Modify: `hooks/midas_hook.py` (find patterns in BOTH `ROUTER_PATTERNS` and `CODEX_ROUTER_PATTERNS`), `README.md`
- Modify: `tests/test_router.py`

**Coverage rule (make it explicit in a code comment + README):** the router denies a bare command ONLY when the recommended replacement can actually do the job. Concretely for `find`: deny only name/path/type filtering (`-name -iname -path -ipath -regex -type`) — Glob can express those; metadata predicates (`-mtime -size -empty -newer -perm -user` etc.) and action predicates (`-exec -delete …`) have no Glob equivalent and MUST stay allowed. `tail -f/-F` stays exempt (Read can't follow). Compounds stay exempt.

1. **Close the no-path gap:** current pattern `^find\s+\S+.*-(name|iname|path|ipath|regex|type)\b` requires a path argument, so `find -name '*.py'` evades. Change both runtimes' find patterns to make the path optional, e.g. `^find\b(?:\s+\S+)*?\s+-(name|iname|path|ipath|regex|type)\b` — verify it still does NOT match when the only dash-args are metadata predicates, and that `find . -type f -exec rm {} \;` is unreachable anyway (`;`/`|` are compound-exempt first; a `-delete` form without compound tokens must still deny only if it ALSO carries a Glob-replaceable predicate — acceptable: the deny reason says retry with Glob for the filtering part; if this feels wrong mid-implementation, add an action-predicate exclusion `\s-(exec|execdir|ok|okdir|delete)\b` → skip deny, and test it).
2. **Regression matrix test:** port the coordinator's audit matrix into `tests/test_router.py` as a table-driven test over both runtimes — every DENY and every deliberate allow from the triage table above, so future pattern edits can't silently change coverage. Include: all read-class variants (`cat f`, `cat -n f`, `head -20 f`, `tail -n 50 f`, `less +100 f`, `more f`), `tail -f`/`tail -F` allowed, all search-class variants, find deny set (`-name/-iname/-path/-ipath/-regex/-type`, with and without a path arg), find allow set (`-mtime/-size/-empty/-newer/-perm/-user/-maxdepth`, bare `find .`), compound exemptions.
3. **README:** one short paragraph under the Bash router row/section stating the coverage rule ("denies only what Read/Grep/Glob can actually replace; metadata and action `find`s, `tail -f`, and compound commands pass through").

- [ ] **Step 1: Failing tests (matrix incl. no-path find). Step 2: Verify fail. Step 3: Implement. Step 4: Full suite.**

---

### Task 4: Version + packaging

**Files:**
- Modify: `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` (both `0.3.1` — bugfix release), `tests/test_packaging.py` (version assertion)

- [ ] **Step 1:** Bump both manifests to `0.3.1`; update `test_packaging.py`; validate all JSON parses.
- [ ] **Step 2:** Full suite + smoke:
```bash
python3 -m unittest discover -s tests -v
# false-fix regression: thrash then --version success must NOT capture a fix
# CLI honesty: unwritable store must print "lesson not saved (store unwritable)"
```

---

## Explicitly Out of Scope (v4)

- Removing the post-hoc large-read nudge (kept as backstop behind the v0.3.0 pre-read gate).
- Chained-fix attribution (recording `pip install foo` as the fix when a later rerun passes) — requires success-history state for marginal signal; the confirmed-rerun rule is honest and cheap.
- New router classes (`ls -R`, `awk`, `sed` readers) — `sed -n` bounded reads are what midas itself recommends on Codex; no evidence of waste from the others.
- Hook-side error reporting — hooks stay silent-fail by contract; only the deliberate CLI gets the honest failure line.
