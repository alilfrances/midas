# Midas

Midas — turns mid-tier models to gold. Deterministic scaffolding that lifts Haiku/Sonnet agentic quality toward frontier-tier behavior at net-zero token overhead.

## Why

Research shows scaffolding roughly doubles weaker-model success on agentic tasks while barely moving frontier models. Dominant small-model failures are premature edits before exploration, skipped verification, and retry-thrashing. Midas targets exactly these with deterministic gates, not prompt bloat.

## What You Get

| Piece | Fires when | Cost |
| --- | --- | --- |
| Session protocol | Session starts | ~150 tok, once |
| Edit gate (per-file) | Edit of an existing file that was never Read, whose dir was never Grep/Glob'd, and no MCP exploration ran | Deny once, 0 tok until fired |
| Verify gate | Stop after edits with no check (shell verify commands or MCP test/lint/typecheck runners both count) | Block once |
| Thrash nudge | Same command fails twice (fuzzy: arg-tweaked retries like `pytest x` -> `pytest x -v` count) | ~25 tok, once |
| Pre-emptive read guard | Unbounded Read of a 400+ line file, before the tokens are spent | Deny once |
| Large-read nudge | Unbounded read over 400 lines (post-hoc backstop) | ~25 tok, once |
| Bash router | Bare `cat`/`head`/`tail`/`grep`/`find` with filter predicates via Bash (`tail -f`/`-F` follow is exempt); Codex allows `rg` | Deny once per class |
| Freshness gate | Stale-looking errors or upgrade/latest prompts | ~25 tok, once |
| Lessons | Prior repo failures or deliberate notes exist | Top 3, <=240 chars |
| Scope skill | Capability/access limits | 0 until loaded |
| Seven on-demand skills | User/model loads playbook | 0 until loaded |

### Runtime coverage

Shipped plugin version: `0.3.1`.

Midas handles six live hook events in Claude Code and Codex: `session_start`, `user_prompt`, `pre_tool`, `post_tool`, `post_tool_failure`, and `stop`.

Claude uses `hooks/midas_hook.py`. Codex uses `hooks/codex_hook.py`, which applies Codex-native guidance such as `rg --files`, `rg -n -C 3`, and narrow `sed -n` reads.

In Claude Code, live failure detection (thrash nudge, freshness-on-error, and automatic lesson capture) depends on the `PostToolUseFailure` hook event. Versions without that event simply skip those behaviors; the other five events still run.

## Install

In Claude Code, run these slash commands inside an interactive Claude session:

```
/plugin marketplace add alilfrances/midas
/plugin install midas@midas
```

From a local clone:

```
/plugin marketplace add "/absolute/path/to/midas"
/plugin install midas@midas
```

In the Claude Code CLI, run these shell commands:

```sh
claude plugin marketplace add alilfrances/midas
claude plugin install midas@midas
```

From a local clone:

```sh
claude plugin marketplace add "/absolute/path/to/midas"
claude plugin install midas@midas
```

In Codex, run these shell commands:

```sh
codex plugin marketplace add alilfrances/midas
codex plugin add midas@midas
```

From a local clone:

```sh
codex plugin marketplace add "/absolute/path/to/midas"
codex plugin add midas@midas
```

Official docs: [Codex plugins](https://developers.openai.com/codex/plugins/build#add-a-marketplace-from-the-cli), [Codex CLI plugin reference](https://developers.openai.com/codex/cli/reference#codex-plugin), [Claude Code plugins](https://code.claude.com/docs/en/discover-plugins), [Claude Code plugin CLI reference](https://code.claude.com/docs/en/plugins-reference).

After installing, run `/reload-plugins` in Claude Code or start a new Claude Code/Codex session so the hooks and skills are loaded.

Requires Python 3.10+ on PATH (`python3`). No other dependencies.

## Token Budget

| Piece | Cost | Offsetting saving |
| --- | --- | --- |
| Protocol +1 line (capability rule) | ~+20 tok once | - |
| Bash router deny (3 classes, once each) | ~+25 tok each, failure-triggered | Prevents full-file `cat` dumps / unpaged `grep -r` floods — hundreds to thousands of tok each |
| Freshness nudge (once) | ~+25 tok | Prevents retry loops against stale API knowledge (each failed retry >=100 tok) |
| Lesson injection at start (only when lessons exist) | <=60 tok | Prevents repeating a failure sequence that previously cost a full thrash loop |
| Lesson match on PreToolUse (once) | ~+30 tok | Skips known-bad command + its error output |
| UserPromptSubmit hook | 0 tok when silent (99% of prompts) | - |
| New skills (scope) | 0 until loaded; +~15 tok description | - |

Worst-case unconditional new cost is ~55 tok/session. Everything else is failure-triggered, once-only, and meant to be cheaper than the failure it prevents.

## Self-learning

Midas keeps a tiny per-project lesson file outside the repo. Base directory precedence is:

1. `MIDAS_CONFIG_DIR/midas-data`
2. `CLAUDE_CONFIG_DIR/midas-data`
3. `~/.claude/midas-data`

Lesson files are named `lessons-<sha1(realpath(cwd))[:12]>.json`.

`CLAUDE_PLUGIN_DATA` is intentionally ignored for lesson storage because it is execution-context dependent and would split the store between hook runs and plain CLI calls like `midas-lesson`. Reset by removing the chosen data dir.

Automatic learning records retry-thrash commands and the later successful fix command when it matches the failed command's first token or a verify command. Deliberate notes use:

```sh
midas-lesson "note"
```

`MIDAS_DISABLE=1` disables all hook behavior and the `midas-lesson` CLI. Bloat is bounded: max 40 lessons per project, top 3 injected at session start, and the injected block is capped at 240 chars. Retrieval is honest exact/pattern memory, not semantic search.

## Plays well with others

No companion plugin is required. Midas is stdlib-only and self-contained.

`tokenslim`: large-read threshold is tunable with `MIDAS_READ_NUDGE_LINES`; invalid values fall back to 400.

Other Stop hooks: stop gates can stack, but Midas blocks once and respects active stop hooks to avoid loops.

MCP retrieval tools count as exploration when their names look like query/search/read/symbol/context/localize/triage/repro/docs work, so Cortex, Axon, and similar tools can satisfy the edit gate. MCP test runners and checkers (`run_tests`, `verify`, `lint`, `typecheck`, `check`, `build`, `compile` in the tool name) count as verification and keep the stop gate quiet.

Subagents share the same session state. Router denies are once per class across the session, and compound Bash commands are exempt.

The router's coverage rule: deny only what the recommended tool can actually replace. `find` is denied only on Glob-replaceable predicates (`-name`/`-iname`/`-path`/`-ipath`/`-regex`/`-type`, path argument optional); metadata predicates (`-mtime`, `-size`, `-user`, ...) and bare `find .` pass through because Glob has no equivalent, as does `tail -f`/`-F` because Read cannot follow.

## Disable

`MIDAS_DISABLE=1` env var kills all hooks and lesson writes. `/plugin uninstall midas@midas` removes.

## How It Works

Midas stores tiny per-session state in `$TMPDIR`. Hook logic is pure-function based for direct tests. All hook entrypoints silent-fail so a broken hook never breaks a session.

Claude and Codex use separate hook entrypoints. Claude keeps `hooks/midas_hook.py`; Codex uses `hooks/codex_hook.py`. The live runtime surface is `session_start`, `user_prompt`, `pre_tool`, `post_tool`, `post_tool_failure`, and `stop`.

## Development

Run the test suite from the repository root:

```sh
python -m unittest discover -s tests -v
```
